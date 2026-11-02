# itempipe

Two agents against one SQLite file: one finds job leads, one applies to them.
Each can run in as many parallel workers as you like; the database is the queue.

```sh
nix develop
python -m itempipe search "remote Go, EU timezone" -n 4
python -m itempipe apply -n 2
python -m itempipe run "remote Python infra" -n 4 -k 2   # search, then apply
python -m itempipe ls --state NEW
python -m itempipe answer 3 "one month"
python -m itempipe reap
```

`search` records leads with `create_item`; duplicates, archived included, are
refused. `apply` workers atomically claim a lead, drive a browser through the
application, and end it in exactly one way: `advance_item` (applied),
`pause_item_for_answer` (a human must answer), `archive_item` (dead end) or
`release_item` (retry later).

## Why it is safe to run several

- `claim_next_item` is one `UPDATE ... RETURNING`; the database is the lock.
- Every later mutation is fenced by the claim token. An expired lease can never
  finish someone else's item.
- `begin_submission` is written before the irreversible submit. A worker that
  dies mid-submit leaves `NEEDS_REVIEW`, never a second application.
- `reap` requeues expired claims and unpauses items whose question has been
  answered.

## Layout

```
itempipe/
  config.py     env + defaults
  db.py         connection, schema, transaction
  schema.sql    items + questions
  store.py      all SQL and the state machine
  tools.py      web, email, item tools
  agents.py     prompts, model, search() and apply()
  cli.py        search | apply | run | ls | answer | reap
  prompts/      search.md, apply.md
```
