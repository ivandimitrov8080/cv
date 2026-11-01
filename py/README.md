# itempipe (TODO: rename)

The Python port described in `../PORT-TO-PYTHON.md`: the same prompt bodies and
tool semantics as `agents/*.org`, but **N search workers and K apply workers run
concurrently against one SQLite database**. The Emacs/typst files are untouched
and remain the reference until the cutover.

```
py/
  pyproject.toml    uv / PyPI metadata (nix does not need it)
  .env.example      DEEPSEEK_API_KEY, ITEMPIPE_DB, limits, msmtp, paths
  itempipe/
    config.py       state_vocab + roles + every limit
    db.py           connect / migrate / BEGIN IMMEDIATE
    schema.sql      items, notes, attempts, targets, host_leases, workers, questions
    store.py        ALL SQL: ItemStore, AnswerStore, TargetStore, HostLeases, WorkerRegistry
    worker.py       WorkerContext, lease renewal, heartbeat, reaper
    tools/          the 12 tools, as per-worker closures
    prompts/        search.md, apply.md
    agents.py       prompt loading, model, search/apply workers
    dispatch.py     search fan-out + apply queue drain
    org_import.py   jobs.org / application-questions.org -> SQLite
    cli.py          run | work | process | ls | status | reap | answers | import-org
  tests/            5 files: store, tools, config, import, dispatch
```

## Shell

```sh
nix develop                    # from the repo root: python + sqlite + msmtp + playwright-mcp
cp py/.env.example .env        # .envrc sources ./.env; fill DEEPSEEK_API_KEY
itempipe-tests                 # pytest, via the shell function
nix build .#itempipe           # the CLI as a wrapped script
nix flake check                # the same tests in a sandbox
```

## Use

```sh
itempipe import-org jobs.org application-questions.org   # one-shot, idempotent
itempipe ls --state NEW
itempipe process --count 2 --max 4 --dry-run             # one apply pass
itempipe work --role apply --count 2
itempipe work --role search --count 4 --brief "java remote EU"
itempipe run "one pass" --workers 4 --max 3
itempipe status            # depth, in-flight, stale leases, review queue
itempipe status --review   # items a human must look at, never auto-retried
itempipe reap
itempipe answers add 7 "one month"
```

## Deviations from the plan, deliberately

* **`py/` is not a second flake.** The Python toolchain is part of the repo's
  existing `flake.nix` (devShell, `packages.itempipe`, `checks`).
* **No supervisor LLM.** `run` fans out over the channel map and then drains the
  queue — `dispatch.py` is the supervisor. A planning model in front of a
  deterministic fan-out bought nothing and cost a prompt that nothing loaded.
* **No `export-org`, no LangGraph `Send` graph, no `runs` table writer.** The
  plan lists them as optional/phase 8-9; they were dead code here.
* **`create` has `require_url=False` for the importer** instead of a second
  `import_item()` that duplicated the insert.

## Invariants worth knowing before changing anything

1. `claim_next` is one `UPDATE ... RETURNING`: the database is the lock.
2. Every mutation after a claim is fenced by `claim_token`; a stale worker gets
   `"This item is no longer assigned to this worker; stopping."`
3. Dedupe is `UNIQUE(name_key)`, not a string comparison; the duplicate check
   deliberately ignores `archived_at`.
4. `archive` is a column; pipeline queries filter `archived_at IS NULL`.
5. `advance_item` requires a `submitting`/`sent` attempt. A lease that expires
   mid-submission goes to `NEEDS_REVIEW`, never back to the queue.
6. Pause is a state, and the reaper un-pauses it once the question is answered.
7. Every tool returns a string and is wrapped in `try/except`.
