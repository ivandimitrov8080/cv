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

## The browser

`apply` starts **one** headless Chromium with `--remote-debugging-port` (9222 by default,
`ITEMPIPE_CDP_PORT` to change it) and then gives each worker its own `playwright-mcp`
process attached to it with `--cdp-endpoint`. So there is one browser, one profile, one
set of logins, and one tab per worker: a worker's tools only ever see its own page.

* A Chromium already listening on that port is adopted and left running; one we started
  is terminated when `apply` exits.
* The profile is `/tmp/itempipe-chromium` (`ITEMPIPE_PROFILE` to change it). Under
  `chromium-sandboxed` that is inside the sandbox's tmpfs, so logins do not survive a
  reboot; bind-mount a profile path into the wrapper if you want them to.
* Playwright's MCP server is documented as giving each client its own browser context
  unless `--shared-browser-context` is passed (that flag is HTTP-only). Tab isolation is
  what we rely on; if you need one cookie jar across all workers, verify it rather than
  assume it.

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
