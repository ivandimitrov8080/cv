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

That is the whole interface. The whole program is `itempipe/__init__.py` — one peewee model,
six tools, two coroutines — plus the two prompts in `itempipe/prompts/`. Opening an older
tracker migrates its `items` rows into the current schema once, in place.

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

## State

`NEW` → claimed as `WORKING` → `ACTIVE` (applied) or archived with a reason.
`NEEDS_REVIEW` means a submit was interrupted; a human should look at it.
