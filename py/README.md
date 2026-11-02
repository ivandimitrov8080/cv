# itempipe

Two agents against one SQLite file. `search` finds job leads and records them; `apply`
claims leads from the same queue and drives a browser through the application. Each runs in
as many parallel workers as you ask for.

```sh
nix develop
python -m itempipe init                        # create/upgrade the db, show the queue
python -m itempipe search "remote Python, EU timezones" -n 4
python -m itempipe apply -n 2
itempipe-check                     # queue tests, no model or network needed
```

That is the whole interface. The program is `itempipe/__init__.py` — one peewee model,
six tools, two coroutines — plus the two prompts in `itempipe/prompts/`, and
`itempipe/status.py`, the progress board.

## Why it is safe to run several

* `claim_next_item` is a compare-and-swap: read the next free row, then claim it with one
  conditional `UPDATE` guarded by the token it had. The database is the lock; a lost race
  just means looking again.
* Every later write is fenced by the claim token, so a worker whose lease expired can never
  finish someone else's lead.
* `begin_submission` is written *before* the irreversible submit. A worker that dies
  mid-submit leaves the lead in `NEEDS_REVIEW` on the next claim, never in a second
  application.
* Claiming also picks up rows whose lease expired, so a killed worker costs nothing.

Inspect the queue with `sqlite3 py/itempipe.db 'select id, state, name from items'`.

## Search is many small sessions

A search worker is not one conversation. It is up to `ITEMPIPE_SESSIONS` (6) sessions in a
row, each of them a fresh agent with an empty message list that is asked to verify one
posting, call `create_item` once, and stop. Once a session records a lead every tool
answers `SESSION OVER`, and the worker starts the next one; the tool budget per session
is `ITEMPIPE_TOOL_BUDGET` (12) calls and its graph limit `ITEMPIPE_SESSION_STEPS` (26).

What survives the reset is the `seen` table: one row per normalised URL or query, with the
verdict of the last check and the session that wrote it. So the tenth worker does not open
the page the first one read, and a session that ends cannot forget where it had been.

| verdict | meaning | looked at again |
| --- | --- | --- |
| `claimed` | a worker is on it | after the 30-minute lease |
| `open` | fetched, not recorded | next session |
| `dead` | 4xx/5xx | after a week |
| `searched` / `noresults` | a query's outcome | next session / after a week |
| `skipped` | opened, nothing recorded | next session |
| `duplicate` | `create_item` refused the name | next session |
| `recorded` | it became a lead | never |

The insert *is* the lock, exactly like `claim_next_item`: two sessions cannot fetch the
same URL at once, and the loser is told so instead of retrying.

Where to look is code, not vibes: `ANGLE_BOARDS` gives each angle a few starting boards,
`frontier` hands a session the first ones this run has not opened yet (rotated, so two
workers sharing one angle start on different pages), and a session ends when the angle's
boards are used up or two sessions in a row record nothing. Set `ITEMPIPE_HN_THREAD` to
this month's who-is-hiring id — the default is a frozen one.

`reconcile` runs when a session ends: a page it opened and did not record becomes
`skipped` rather than being forgotten or left claimed. `itempipe init` forgets `seen` rows
older than a year (`ITEMPIPE_SEEN_DAYS`) and prints how many.

When one worker out of ten dies, the other nine still report: the workers are gathered
with `return_exceptions=True`, and a worker's report is `<tag> — N recorded: …` plus the
longest thing it said, so a model that stops with a canned sentence is visible as
`[no report: …]` instead of being passed off as a summary.

## The browser

`apply` starts **one** headless Chromium with `--remote-debugging-port` (9222 by default,
`ITEMPIPE_CDP_PORT` to change it) and then gives each worker its own `playwright-mcp`
process attached to it with `--cdp-endpoint`. So there is one browser, one profile, one
set of logins, and one tab per worker: a worker's tools only ever see its own page.

* A Chromium already listening on that port is adopted and left running; one we started
  is terminated when `apply` exits.
* Chromium's stderr goes to `/tmp/itempipe-chromium-9222.log` (`ITEMPIPE_CHROME_LOG`, and
  `ITEMPIPE_CDP_PORT` for the port). If it refuses to start, `apply` prints the last lines
  of that log plus the likely fix, and exits 1, rather than a traceback.
* `ITEMPIPE_HEADED=1` drops `--headless`, so you can watch a run.
* The profile's location and the browser's death are the two things that go wrong here.
  `--headless` plus no display, a profile locked by an earlier crash, and a taken port each
  have their own line in that message.
* The profile is `/tmp/itempipe-chromium` (`ITEMPIPE_PROFILE` to change it). Under
  `chromium-sandboxed` that is inside the sandbox's tmpfs, so logins do not survive a
  reboot; bind-mount a profile path into the wrapper if you want them to.
* Playwright's MCP server is documented as giving each client its own browser context
  unless `--shared-browser-context` is passed (that flag is HTTP-only). Tab isolation is
  what we rely on; if you need one cookie jar across all workers, verify it rather than
  assume it.

## When a tool cannot answer

Tools never raise. `ddgs` raises `DDGSException("No results found.")` where it could
return `[]`, and that used to take the whole worker down mid-node:

* `web_search` retries `ITEMPIPE_SEARCH_TRIES` times (default 3, 1.5s then 3s apart) and
then returns `NOT searched: …` with the reason and what to do about it — a rate limit is
worded differently from a genuinely empty query, because the fix differs.
* `fetch_page` returns `NOT fetched: …` for a non-http URL, a network error, or any
`4xx`/`5xx` rather than handing the model an error page as if it were the posting.
* The prompts tell the agent that `NOT …` means change the query or the site, not retry.

A search that finds nothing is a normal outcome, so it is reported, not thrown; queue
state is untouched by either tool, so no lead is lost when this happens.

## Progress

While workers run, `rich` draws a board on **stderr**: a row per worker (spinner, tool
calls made, what it is doing right now) and a footer with the queue tally, refreshed once a
second from the database. When it finishes, the board is replaced by a final panel.

The board lives in `itempipe/status.py` and is driven by five functions:

```python
status.open_board("itempipe apply", tally)   # any callable -> {state: count}
status.worker("w1", "call", "fetch_page {...}")
status.finished("w1", "Acme: applied")
status.refresh()                             # re-read the tally, tick the clock
status.close_board()
```

All of them are no-ops before `open_board`, and none of them import the database.

stdout only ever gets the report, so `itempipe apply > out.txt` still works; with no tty
(or without `rich`) each event is a plain line on stderr instead:

```
  0:07 w1  → fetch_page {'url': 'https://acme.dev/jobs/42'}
  0:09 w1  ← Submission guard set.
  0:09 w1  ✓ Acme Corp: applied (no reason).
```

## State

`NEW` → claimed as `WORKING` → `ACTIVE` (applied) or archived with a reason.
`NEEDS_REVIEW` means a submit was interrupted; a human should look at it.
