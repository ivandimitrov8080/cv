# itempipe

Two agents against one SQLite file. `search` finds job leads and records them; `apply`
claims leads from the same queue and drives a browser through the application. Each runs in
as many parallel workers as you ask for.

```sh
nix develop
python -m itempipe init                        # create/upgrade the db, show the queue
python -m itempipe search -n 4                 # until Ctrl-C; see itempipe/search.toml
python -m itempipe apply -n 2
itempipe-check                     # queue tests, no model or network needed
```

That is the whole interface. The program is `itempipe/__init__.py` — one peewee model,
six tools, two coroutines — plus the two prompts in `itempipe/prompts/`,
`itempipe/status.py`, the progress board, and `itempipe/search.toml`, which holds
**everything about the job market**. The module knows how to search, never what to look
for: no stack, country, site or job title appears in the Python.

## What to look for is a file

`itempipe/search.toml` (override with `ITEMPIPE_CONFIG`) is the whole brief:

| key | what it does |
| --- | --- |
| `[search] roles` | phrases to search for. Add as many as you like — this *is* the width of the search |
| `[search] domains` | industries crossed with every role (`crypto`, `fintech`, …); empty by default |
| `[search] seniority` | prefixes tried in front of each role |
| `[search] phrases` / `contract_phrases` | how postings are announced, for permanent and contract work |
| `[search] sites` | hosts swept with `site:` — the ATSs where small companies post, plus regional boards |
| `[search.regions]` / `[search.cities]` | where; each group is interleaved, never walked in order |
| `[search] notes` | handed to the agent verbatim: who you are, what you will and will not take |
| `[vars]` + `[[boards]]` | the default task queue, with `{var}` substitution in the URLs |

A Java developer who also wants crypto work adds `"crypto"` to `domains` (or a
`"crypto backend"` phrase to `roles`) and nothing else — no code changes. Two files, two
hunts: `ITEMPIPE_CONFIG=~/crypto.toml itempipe search`, `ITEMPIPE_CONFIG=~/java.toml …`.
With no config the search is empty rather than invented, and `itempipe search` says so.

The agent's prompt (`itempipe/prompts/search.md`) is likewise free of stacks and
countries; the `{{NOTES}}` placeholder is filled from `[search] notes` and `{{CV}}` from
the CV, so all the context about the person and the target lives in data.

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

## Search is many small sessions, and it does not finish

`itempipe search` runs until you stop it with Ctrl-C; it then prints the session log,
one line per session, ending with what each worker recorded. `ITEMPIPE_SESSIONS` caps the
sessions per worker if you want a bounded run (`0`, the default, means no cap).

A search worker is not one conversation. It is a rotation of *sessions*, each of them a
fresh agent with an empty message list that is asked to verify one posting, call
`create_item` once, and stop. Once a session records a lead every tool answers
`SESSION OVER`, and the worker starts the next one; the tool budget per session is
`ITEMPIPE_TOOL_BUDGET` (12) calls, its graph limit `ITEMPIPE_SESSION_STEPS` (26), and
there is `ITEMPIPE_PAUSE` (2s) between sessions.

Each session gets a **plan** from a shared queue, `next_tasks`:

1. boards from the `[[boards]]` queue that nobody has read in `ITEMPIPE_BOARD_DAYS` (3)
   days — they are claimed from `seen` by whichever worker asks first, so the default list
   is consumed by the pool and then left behind; no worker is tied to one board or one
   subject;
2. then queries from `query_space()`, built from the config: roles × seniority × regions ×
   phrasing, domains crossed with every role, `site:` sweeps of the configured hosts, and
   city by city. That is the part that keeps producing new places to look after the queued
   boards are read out, and it is how companies that only ever post on their own ATS get
   found.

Nothing is pinned to a region: the configured groups are **interleaved** (`interleave`)
so adjacent queries come from different parts of the world.

The queue is the scheduler, so `-n 10` no longer means "ten copies of the same six ideas".
A task claimed at plan time and never used is released by `reconcile` at the end of the
session, so a crashed session costs nothing. When the queue is genuinely empty — every
board recently read and every query run within `ITEMPIPE_QUERY_DAYS` (30) — a worker says
so on the board, sleeps `ITEMPIPE_IDLE` (300s) and looks again; it does not exit.

Boards are config too: add a `[[boards]]` entry with a `label` and `urls` (see the shipped
`search.toml`), and point `ITEMPIPE_CONFIG` at your file. Adding LinkedIn or a regional
board is a line in that file, not a patch:

```toml
[[boards]]
label = "LinkedIn"
urls = ["https://www.linkedin.com/jobs/search/?keywords=Java%20Spring&f_WT=2"]
```

What survives a session reset is the `seen` table: one row per normalised URL or query,
with the verdict of the last check and the session that wrote it. So the tenth worker
does not open the page the first one read, and a session that ends cannot forget where it
had been. `itempipe init` and every `search` first run `remember_leads`, which writes a
`recorded` row for every URL already in the tracker — otherwise each run would spend its
first sessions re-finding its own leads.

| verdict | meaning | looked at again |
| --- | --- | --- |
| `claimed` | a worker is on it | at once if it is your own, else after the 30-minute lease |
| `open` | fetched, not recorded | next session |
| `board` | a board page, not a posting | after `ITEMPIPE_BOARD_DAYS` |
| `dead` | 4xx/5xx | after a week |
| `searched` / `noresults` | a query's outcome | after `ITEMPIPE_QUERY_DAYS` |
| `skipped` | opened, nothing recorded | next session |
| `duplicate` | `create_item` refused the name | next session |
| `recorded` | it became a lead | never |

The insert *is* the lock, exactly like `claim_next_item`: two sessions cannot fetch the
same URL at once, and the loser is told so instead of retrying. `reconcile` runs when a
session ends: a page it opened and did not record becomes `skipped` rather than being
forgotten or left claimed. `itempipe init` forgets `seen` rows older than a year
(`ITEMPIPE_SEEN_DAYS`).

A session that raises does not end the run: the worker logs it on the board and backs off
(`ITEMPIPE_PAUSE`, doubling to a minute) before the next session.

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
