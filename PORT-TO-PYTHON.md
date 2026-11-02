# Porting `job-search` + `job-apply` to Python (LangChain, SQLite)

Scope: **minimal feature port, deliberately different architecture.** Same two
prompt bodies, same tool semantics, but N search workers and N apply workers
run concurrently against one SQLite database instead of one sequential pair.

Deliberately **not** in scope: org capture/agenda UI, a dashboard, vector
search, multi-user, rewriting the prompt policies.

---

## 0. Naming rules

The domain is an implementation detail (it gets renamed later), so code names
describe **what the operation does to a record**, never what the record is
about:

1. The unit of the pipeline is an **item**. Code never says "lead",
   "application", "candidate", "job", "company".
2. Verbs for the operations: `create`, `claim`, `advance`, `pause`, `release`,
   `archive`, `lookup`, `send`, `list`.
3. State **values** are data, not identifiers: they live in `config.state_vocab`
   (a list of strings), and code refers to _roles_ — `initial_state`,
   `working_state`, `success_state`, `paused_state`, `review_state`,
   `terminal_states`. Renaming the domain changes a config list, not code.
4. Domain wording survives in exactly two places: `prompts/*.md` (the agent
   bodies) and config values. Nothing else.
5. Package name `itempipe` is a **placeholder** with a `# TODO: rename`.

### Rename map (Emacs → Python)

| Emacs                                                                    | Python                                                            |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| `cv-jobs-lead-fields` (`company role url location size english contact`) | `items` columns (`name title url location size language contact`) |
| `jobs.org` / Org TODO keyword                                            | `items.state` + `items.archived_at`                               |
| `cv-jobs-lead` / `cv-jobs-add` / `cv-jobs-get` / `cv-jobs-update`        | `ItemStore.create()`, `.get()`, `.update()`                       |
| `cv-jobs--lead-put` (APPLIED + schedule)                                 | `advance()` side effect on `success_state`                        |
| `cv-jobs--archive`, `cv-jobs-archive`                                    | `ItemStore.archive(id, reason)`                                   |
| `cv-jobs--find-existing`                                                 | `ItemStore.find_by_name_key()` (no archive filter)                |
| `cv-jobs--note`                                                          | `ItemStore.add_note(id, kind, body)`                              |
| `cv-jobs-next-lead`                                                      | tool `claim_next_item` → `ItemStore.claim_next(worker)`           |
| `cv-jobs-mark-applied`                                                   | tool `advance_item`                                               |
| `cv-jobs-archive-lead`                                                   | tool `archive_item`                                               |
| `cv-jobs-hold-for-question`                                              | tool `pause_item_for_answer`                                      |
| `cv-jobs-find-answers`                                                   | tool `lookup_answer` → `ItemStore.search_answers()`               |
| `cv-jobs-record-lead`                                                    | tool `create_item`                                                |
| `cv-jobs-send-application-email`                                         | tool `send_templated_email`                                       |
| (`jobs.org` inlined in prompt)                                           | tools `list_items`, `claim_research_target`                       |
| `application-questions.org`                                              | `questions` table                                                 |
| `cv-jobs--current` (`defvar` marker)                                     | `WorkerContext.item_id` + `claim_token`                           |

---

## 1. What is being ported

| Piece                              | Emacs (now)                                                                                                | Python (target)                                                          |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `job-search` prompt                | `agents/job-search.org` (Deepseek, ctx `jobs.org` + `cv.typ`, tools: websearch, fetch, `cv_jobs_add_lead`) | same body → `prompts/search.md`, instantiated **once per search worker** |
| `job-apply` prompt                 | `agents/job-apply.org` (Playwright MCP + 6 tools)                                                          | same body → `prompts/apply.md`, instantiated **once per apply worker**   |
| supervisor                         | `gptel-make-preset 'job` (one `Agent` tool, sequential)                                                    | `prompts/supervisor.md` + fan-out/queue drain (§9)                       |
| `cv-jobs--current`                 | mutable global marker                                                                                      | per-worker `WorkerContext` with a **fencing token**                      |
| `cv-jobs-find-answers` (orderless) | completion matching                                                                                        | token scoring over `questions` (§7)                                      |
| `cv-jobs-send-application-email`   | `mail-send` → msmtp                                                                                        | `EmailMessage` → `msmtp -t` (§8)                                         |
| `~/src/cv/result/*.pdf`            | attachments                                                                                                | `config.attachments`                                                     |

Tool signatures are the contract, so the prompt port is a mechanical rename
(§0 map) plus deleting the `:PROPERTIES:` front matter. The anti-fabrication
rules, "form only", "one item per claim", and the sub-agent "final message is
the only thing that comes back" all transfer word for word.

---

## 2. Architecture: one database, many stateless workers

The original is sequential because gptel has one agent at a time and
`cv-jobs--current` is a global. Both constraints disappear in Python, so the
port splits **orchestration** from **execution**:

```
                    ┌──────────────────────────────────────┐
   supervisor  ───▶ │  dispatch: fan-out (search)          │
   (LangGraph)      │            queue drain (apply)       │
                    └───────┬──────────────────────┬───────┘
                            │                      │
                 ┌──────────▼────────┐   ┌─────────▼──────────┐
                 │ search worker 1..N│   │ apply worker 1..K  │
                 │ prompt: search.md │   │ prompt: apply.md   │
                 │ web_search/fetch  │   │ playwright MCP     │
                 │ create_item       │   │ claim/advance/...  │
                 └──────────┬────────┘   └─────────┬──────────┘
                            │                      │
                     ┌──────▼──────────────────────▼──────┐
                     │  SQLite (WAL): items, targets,     │
                     │  notes, questions, attempts,       │
                     │  workers, runs                     │
                     └────────────────────────────────────┘
```

Properties this buys:

- **Workers are stateless and disposable.** All state is in the DB; a worker
  can be killed at any moment and restarted (see §6 at-most-once for the one
  caveat around irreversible actions).
- **Same binary, two roles.** `itempipe work --role search --count 4` and
  `itempipe work --role apply --count 3` can be separate processes on separate
  machines. It also runs as one process with an internal asyncio pool (§9).
- **The DB _is_ the lock.** Claiming is a single atomic `UPDATE ... RETURNING`;
  no message broker, no in-memory queue, nothing to lose on crash.

Why not a task queue (Celery/RQ/Redis)? The workload is 5–20 items per run, not
thousands; SQLite plus WAL is enough and keeps one dependency and one file.
The claim query is the only thing that would change if the store moved to
PostgreSQL (`FOR UPDATE SKIP LOCKED`).

### Concurrency budget (why K is small)

| Resource          | Limit                                                                | Mitigation                                                                    |
| ----------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Browser instances | **2–3** — each Playwright MCP is a Chromium with its own profile dir | `browser_slots` cap; per-worker `--user-data-dir`; one MCP process per worker |
| LLM API           | ~4–6 concurrent requests                                             | `asyncio.Semaphore(max_llm_concurrency)` + retry on 429 with jitter           |
| Target hosts      | 1 request at a time per host                                         | host lease (§6) so two workers never hit the same origin                      |
| SQLite writes     | 1 writer at a time                                                   | WAL + `busy_timeout=5000` + short `BEGIN IMMEDIATE` transactions              |
| Outgoing email    | 1 per few seconds                                                    | `email_min_interval`, staggered by worker index                               |

Search workers are cheap and can run at 4–8; apply workers are expensive, so K
defaults to 2. Both are config, not code.

---

## 3. Layout

```
src/cv/
  pyproject.toml            # uv, one package "itempipe"  (TODO: rename)
  .env.example              # DEEPSEEK_API_KEY, ITEMPIPE_DB, limits, msmtp, paths
  itempipe/
    config.py               # pydantic-settings, state_vocab, concurrency limits
    db.py                   # connect(), migrate(), row factory, tx helpers
    schema.sql
    store.py                # ItemStore + TargetStore: all SQL lives here
    worker.py               # WorkerContext, lease renewal, heartbeat, reaper
    tools/
      items.py              # create_item, claim_next_item, advance_item,
                            #   archive_item, release_item, list_items,
                            #   begin_submission
      targets.py            # claim_research_target, mark_target_unusable
      answers.py            # lookup_answer, pause_item_for_answer
      email.py              # send_templated_email
      web.py                # web_search, fetch_page
      browser.py            # per-worker playwright MCP client
    prompts/
      supervisor.md
      search.md
      apply.md
    agents.py               # build_search_worker / build_apply_worker / build_supervisor
    dispatch.py             # fan-out + queue drain (async pools)
    cli.py                  # run | work | process | ls | status | reap | answers
                            #   | import-org | export-org
  tests/
    test_store_claim.py     # concurrency: no double claim, fencing, leases
    test_store.py
    test_answers.py
    test_tools_contract.py  # tools return strings, never raise
    test_import_org.py
```

Deps: `langchain`, `langgraph`, `langchain-openai` (Deepseek is OpenAI-compatible,
base_url `https://api.deepseek.com/v1`), `langchain-mcp-adapters`, `mcp`,
`pydantic-settings`, `httpx`, `trafilatura`, `ddgs`. Dev: `pytest`,
`pytest-asyncio`, `ruff`.

> `deepseek-v4-flash` is a gptel backend nickname, not necessarily an API model
> id. Make it `ITEMPIPE_MODEL` (default `deepseek-chat`) and verify against the
> key before Phase 6.

CLI:

```
itempipe run "<brief>"                  # supervisor: fan out + drain
itempipe work --role search --count 4   # long-lived worker pool
itempipe work --role apply  --count 2
itempipe process [--max 5]              # one apply pass (the old behaviour)
itempipe ls --state NEW                 # queue inspection
itempipe status                         # depth, in-flight, stale leases
itempipe reap                           # release expired leases, flag stuck submits
itempipe answers add <id> "<text>"      # unblock a PAUSED item
itempipe import-org jobs.org application-questions.org
```

Everything that writes or sends accepts `--dry-run`.

---

## 4. Schema (`schema.sql`)

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,               -- the subject of the item
  name_key       TEXT NOT NULL UNIQUE,        -- lower(trim(collapse(name)))
  title          TEXT NOT NULL,               -- what is being asked for
  url            TEXT NOT NULL,
  url_key        TEXT NOT NULL,               -- normalized url (dedupe hint)
  location       TEXT,
  region         TEXT,
  size           TEXT,                        -- vocabulary checked in Python
  language       TEXT,                        -- vocabulary checked in Python
  contact        TEXT,
  notes          TEXT,                        -- one-line summary
  state          TEXT NOT NULL,               -- validated against config.state_vocab
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  advanced_on    TEXT,
  follow_up_on   TEXT,
  archived_at    TEXT,
  archive_reason TEXT,
  meta_json      TEXT,                        -- everything the org import kept
  -- worker coordination
  claimed_by     TEXT,
  claim_token    TEXT,
  claimed_until  TEXT,
  attempt_count  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS items_state_idx ON items(state, id)
  WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS items_url_idx ON items(url_key);

CREATE TABLE IF NOT EXISTS notes (
  id         INTEGER PRIMARY KEY,
  item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL DEFAULT 'note',    -- note | state | reason | worker
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS notes_item_idx ON notes(item_id, id);

-- Audit trail for concurrent attempts. `status='submitting'` is the
-- at-most-once guard for irreversible actions (§6).
CREATE TABLE IF NOT EXISTS attempts (
  id          INTEGER PRIMARY KEY,
  item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  worker_id   TEXT NOT NULL,
  status      TEXT NOT NULL,                  -- claimed | submitting | sent
                                              -- | submitted | abandoned | failed
  detail      TEXT,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS attempts_item_idx ON attempts(item_id, id);

-- Search-side shared TODO: stops N searchers researching the same subject.
CREATE TABLE IF NOT EXISTS targets (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  name_key      TEXT NOT NULL UNIQUE,
  url           TEXT,
  state         TEXT NOT NULL DEFAULT 'OPEN', -- OPEN | CLAIMED | DONE | UNUSABLE
  claimed_by    TEXT,
  claim_token   TEXT,
  claimed_until TEXT,
  detail        TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Host politeness: one worker per origin at a time.
CREATE TABLE IF NOT EXISTS host_leases (
  host          TEXT PRIMARY KEY,
  holder        TEXT NOT NULL,
  expires_at    TEXT NOT NULL
);

-- Liveness, so `status` can show dead workers and the reaper can be safe.
CREATE TABLE IF NOT EXISTS workers (
  id           TEXT PRIMARY KEY,
  role         TEXT NOT NULL,                 -- search | apply | supervisor
  pid          INTEGER,
  host         TEXT,
  started_at   TEXT NOT NULL DEFAULT (datetime('now')),
  heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
  item_id      INTEGER
);

CREATE TABLE IF NOT EXISTS questions (
  id           INTEGER PRIMARY KEY,
  question     TEXT NOT NULL UNIQUE,
  question_key TEXT NOT NULL UNIQUE,
  answer       TEXT,                          -- NULL = unanswered → drives pause
  source       TEXT,
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  answered_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY,
  agent       TEXT NOT NULL,                  -- supervisor | search | apply
  worker_id   TEXT,
  brief       TEXT,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  result      TEXT,
  thread_id   TEXT                            -- LangGraph checkpointer id
);
```

`config.py` vocabulary block:

```python
state_vocab   = ["NEW", "WORKING", "ACTIVE", "INTERVIEW", "OFFER", "PAUSED",
                 "NEEDS_REVIEW", "DONE", "REJECTED", "IGNORED"]
initial_state = "NEW"        # claim_next() pulls from here
working_state = "WORKING"    # held while a worker owns the item
success_state = "ACTIVE"     # advance() moves here + schedules follow-up
paused_state  = "PAUSED"     # pause_item_for_answer() moves here
review_state  = "NEEDS_REVIEW"   # ambiguous outcome, a human must look
terminal      = {"DONE", "REJECTED", "IGNORED"}

# concurrency
max_search_workers  = 4
max_apply_workers   = 2
browser_slots       = 2
max_llm_concurrency = 6
claim_lease_seconds = 900
heartbeat_seconds   = 30
email_min_interval  = 5.0
```

---

## 5. Claiming: atomic, leased, fenced

Everything that makes parallel workers safe lives in `store.py`:

```sql
-- claim_next: one statement, no read-then-write race
UPDATE items SET
  state         = :working_state,
  claimed_by    = :worker_id,
  claim_token   = :token,          -- uuid4, unique per claim
  claimed_until = datetime('now', :lease),
  attempt_count = attempt_count + 1
WHERE id = (
  SELECT id FROM items
   WHERE state = :initial_state AND archived_at IS NULL
   ORDER BY id LIMIT 1
)
RETURNING *;
```

```sql
-- every mutating tool call is fenced by the token, so a zombie worker
-- whose lease expired can never finish someone else's item
UPDATE items SET state = :new_state, claim_token = NULL, claimed_by = NULL,
                 claimed_until = NULL, advanced_on = date('now'), ...
 WHERE id = :item_id AND claim_token = :token AND state = :working_state;
```

`rowcount == 0` → the tool returns
`"This item is no longer assigned to this worker; stopping."` and the agent
gives up. That is the fencing token pattern, and it is the single most
important difference from the Emacs version (where a stale marker could be
written by anyone).

Leases are renewed by a background `asyncio` task every
`heartbeat_seconds` (30 s) against `claim_lease_seconds` (15 min), so a slow
browser session doesn't lose its item, and a dead worker's items come back
within one lease period.

`itempipe reap` (and a task in `run`) does:

1. `state = working_state AND claimed_until < now` → back to `initial_state`,
   note `"lease expired, requeued"`, attempt row `failed`;
2. `state = working_state AND claimed_until < now AND` an attempt is
   `submitting` → `review_state` instead of requeue (see §6);
3. `state = paused_state` and the linked question now has an answer → back to
   `initial_state` (this replaces the manual "set the lead back to LEAD");
4. delete stale `host_leases` and mark `workers` with old heartbeats as dead.

---

## 6. At-most-once for irreversible actions

Concurrency plus a real-world side effect (an actual application being sent)
means a crash can leave "did it send or not?" ambiguity. The rule:

> **A lease that expires while an irreversible action is in flight must never
> be retried automatically.**

Implementation, both paths:

- **Email** — `send_templated_email` writes an `attempts` row with
  `status='submitting'` _before_ invoking msmtp, then `status='sent'` after.
  `reap` sees a stuck `submitting` and moves the item to `review_state`.
- **Browser** — the submit click happens inside an agent-driven browser, so it
  cannot be wrapped by a tool. Hence one added tool, `begin_submission()`,
  which writes `status='submitting'`. `prompts/apply.md` gains a single
  sentence: _"Immediately before the final submit action, call
  `begin_submission`."_ This is the only deliberate prompt deviation in the
  port and it is the price of at-most-once; the alternative is occasionally
  double-applying to a company.

`advance_item` then requires a `submitting` attempt for the item and flips it
to `submitted`; `archive_item`/`release_item` resolve it as `abandoned`.

`review_state` items surface via `itempipe status --review` and never re-enter
the automatic queue on their own.

---

## 7. Answer lookup without Org completions

Emacs used `completion-styles '(orderless)`. Dependency-free port:

```python
score(q, tokens) = 3 * sum(tok in q for tok in tokens) + 1 * (all tokens present)
```

- tokenize the query on non-alphanumerics, lowercase;
- rank `SELECT * FROM questions WHERE answer IS NOT NULL`;
- tie-break on `answered_at DESC`, cap at `ITEMPIPE_ANSWERS_LIMIT` (default 3);
- empty/`None` query → every answered question (still capped);
- output format identical to Emacs (`"1. <question>\n- <answer>"`, blank-line
  joined) because the apply prompt documents how to read it;
- carry `note`/format hints with the answer — those are why answers survive
  real forms (salary units, placeholder conventions).

Read-only, so it is safe under any concurrency. Upgrade path if scoring proves
bad: `questions_fts` FTS5 + `bm25()`, same tool signature.

---

## 8. Templated email

Functionally identical to `cv-jobs-send-application-email`:

1. read `application-email-template.txt`; first line must be `Subject: …`;
2. refuse while `<<<` is still present ("write your email first");
3. substitute **only** `{{position}}` and `{{company}}`; any other `{{…}}` is an
   error — returned as an error string, not raised;
4. attach `config.attachments`, each checked readable;
5. `attempts.status='submitting'` → send via
   `subprocess.run(["msmtp", "-a", cfg.msmtp_account, "-t"], input=msg.as_bytes())`
   (the same path `sendmail-send-it` used, no new credentials;
   `smtplib.SMTP_SSL` only when `ITEMPIPE_SMTP_HOST` is set) → `status='sent'`;
6. return `"… sent to <email> for <title> at <name>."` or
   `"… NOT sent (<step>): <err>"`, keeping the failing step name from
   `cv-jobs--send-step` — that is what made msmtp failures debuggable;
7. serialised by an `asyncio.Lock` + `email_min_interval`, because parallel
   workers sending simultaneously is a spam signal.

Never generates body text. `--dry-run` renders the MIME message, sends nothing.

---

## 9. Orchestration

### Two different shapes

Search and apply are not the same problem, so they get different dispatch
logic rather than one generic fan-out:

- **Search is a fan-out over _queries_**. The supervisor slices the brief into
  N distinct angles (different channels/boards/keyword clusters — the
  `strategies/no-hr-strategy.org` channel map is the natural source), runs one
  search worker per slice, and merges the reports. LangGraph's `Send` API does
  this directly.
- **Apply is a _queue drain_, not a fan-out**, because the item count is
  unknown and each unit is expensive. A bounded pool of K workers loops
  `claim → work → record` until the queue is empty or `--max` is hit. This is
  plain `asyncio.TaskGroup` in `dispatch.py`; wrapping it in graph nodes adds
  nothing.

### Dispatch sketch

```python
# dispatch.py
async def drain_apply(store, *, workers: int, max_items: int | None, dry_run: bool):
    sem = asyncio.Semaphore(workers)
    async def one(index: int):
        ctx = WorkerContext(role="apply", index=index)   # own id, token, renewal task
        async with sem, browser_slot(index, dry_run=dry_run):   # capped Chromium
            agent = build_apply_worker(store, ctx)
            await run_to_completion(agent, brief=cfg.apply_brief(ctx))
    async with asyncio.TaskGroup() as tg:
        for i in range(workers):
            tg.create_task(one(i))
    # each worker re-checks the queue itself; the group ends when all idle
```

```python
# supervisor: LangGraph, search only
graph = (
    StateGraph(SupervisorState)
    .add_node("plan",        plan_slices)        # brief -> N search briefs
    .add_node("search",      run_search_worker)
    .add_node("drain_apply", run_apply_drain)
    .add_node("report",      merge_reports)
    .add_conditional_edges("plan", fan_out, ["search"])   # Send(...) per slice
    .add_edge("search", "drain_apply")
    .add_edge("drain_apply", "report")
    .compile(checkpointer=SqliteSaver.from_conn_string(cfg.db))
)
```

- `run_to_completion` = `await agent.ainvoke({"messages": [...]},
config={"recursion_limit": 60})` returning only the last `AIMessage` content
  — sub-agents "cannot ask questions, final message is the only thing that
  comes back", as the prompts require.
- Prompts load from `prompts/*.md` at import; front matter (`backend`, `model`,
  `tools`) is parsed into `init_chat_model(...)`, so each role can use a
  different model (cheap model for search fan-out, stronger one for apply).
- `search.md` gets the CV text inlined (~4.5 KB) and must **not** get the whole
  tracker inlined; it gets `list_items` + `claim_research_target`.
- `--dry-run` swaps `advance_item`/`send_templated_email`/`begin_submission`
  for stubs that report intent and skips the MCP client.
- Model-free tests: `FakeMessagesListChatModel` + a scripted tool-call sequence
  proves wiring, arg parsing and string returns per worker.

### Prompt changes forced by parallelism

Small but necessary (each is one sentence, added to `search.md`/`apply.md`):

- _"Another worker may be handling a different item right now. Never act on an
  item you did not claim, and stop as soon as a tool tells you the item is no
  longer yours."_
- _"Call `claim_research_target` before researching a subject; if it returns
  nothing, pick another one."_
- _"Immediately before the final submit action, call `begin_submission`."_

---

## 10. Tools

Every tool is `@tool` + pydantic args, returns a **string**, and wraps its body
in `try/except Exception` returning `"NOT done (<step>): <err>"`. The prompt
bodies depend on that (it is how `cv_jobs_hold_for_question`'s
`wrong-type-argument` bug surfaced at all).

Per-worker closures: `make_apply_tools(store, ctx)` returns tools bound to a
`WorkerContext(worker_id, item_id, claim_token)`. **No module-level globals**, so
K workers can share a process. The model still sees the original parameterless
signatures (`claim_next_item()`, `advance_item()`) — the token is supplied by
the closure, never by the model.

| Tool                    | Args                                                                                  | Returns / notes                                                                                                 |
| ----------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `create_item`           | `name, title, url, location=None, contact=None, notes=None, size=None, language=None` | recorded / duplicate / rejected report; race-safe via `name_key` UNIQUE + `ON CONFLICT DO NOTHING` then re-read |
| `claim_next_item`       | —                                                                                     | multi-line item report, or `"No NEW items left in the pipeline."`; sets `ctx.item_id`/`ctx.claim_token`         |
| `advance_item`          | —                                                                                     | fenced; `"Marked <name> — <title> as ACTIVE and scheduled a follow-up."`                                        |
| `release_item`          | `reason`                                                                              | fenced; returns item to `initial_state` for a retryable failure (browser died, page timed out)                  |
| `archive_item`          | `reason=None`                                                                         | fenced; `"Archived <name>: <reason>"`                                                                           |
| `pause_item_for_answer` | `question`                                                                            | saves question unanswered, `state → paused_state`, note `"On hold, no answer for: <q>"`                         |
| `begin_submission`      | —                                                                                     | writes `attempts.status='submitting'` (§6)                                                                      |
| `lookup_answer`         | `query=None`                                                                          | numbered Q/A text                                                                                               |
| `send_templated_email`  | `email, name, title, subject_override=None`                                           | `"… sent …"` / `"… NOT sent (<step>): <err>"`                                                                   |
| `list_items`            | `states=None, limit=200`                                                              | `Name — STATE` lines (replaces inlining the org file)                                                           |
| `claim_research_target` | `name`                                                                                | atomically marks a subject as being researched, so N searchers don't duplicate                                  |
| `mark_target_unusable`  | `name, reason`                                                                        | dead posting/not-hiring, so other searchers skip it                                                             |
| `web_search`            | `query`                                                                               | titles + urls + snippets (`ddgs`)                                                                               |
| `fetch_page`            | `url`                                                                                 | extracted text, `max_chars` cap (`httpx` + `trafilatura`)                                                       |
| browser                 | —                                                                                     | per-worker `MultiServerMCPClient({"playwright": {...}}).get_tools()`                                            |

`contact` stays documented as `"form only"`, and `create_item`'s description
repeats "never a search-results page, never a board front page" verbatim.

---

## 11. Invariants (each one is a test)

1. **No double claim.** 20 concurrent `claim_next()` calls on 5 items return 5
   distinct ids and 15 `None`. (`test_store_claim.py`, real file-backed DB, not
   `:memory:`.)
2. **Fencing.** A stale token cannot `advance_item`; the item keeps its real
   state. (§5)
3. **Dedupe is a constraint, not a comparison.** Two workers racing to
   `create_item` with the same `name_key`: exactly one row, and the loser gets
   the "Already tracked …" string.
4. **Archive is a column.** Every pipeline query filters `archived_at IS NULL`;
   the duplicate check deliberately does not.
5. **`name`/`title` required**, both collapsed to one line; `url` must match
   `^https?://`; `size`/`language` validated against their vocabularies and
   dropped with a warning rather than failing the call.
6. **`rowcount` is asserted** in `create()` — the Emacs version silently dropped
   `CREATED`/`NOTES` because a lookup miss was swallowed by a `when`.
7. **Unanswered questions stay unanswerable** — `lookup_answer` filters
   `answer IS NOT NULL`, or the model will happily submit "None".
8. **Pause is a state**, not a note (`HOLD` was never reachable when the tool
   threw; the reaper's rule 3 now un-pauses automatically once answered).
9. **Irreversible actions are at-most-once** (§6).
10. **One writer per host** (`host_leases`) and ≤ `browser_slots` Chromiums.

---

## 12. Org → SQLite import

`itempipe import-org jobs.org application-questions.org` (one-shot, idempotent
on `name_key`):

1. parse headings `^\*+ +(TODO )?(Name) — (Title)( +:tags:)?$`;
2. property drawer → columns (`URL LOCATION NATION SIZE ENGLISH CONTACT CREATED
APPLIED_ON NOTES`) plus `meta_json` for the rest (`INTERVIEW` included);
3. `- NOTE: …` body lines → `notes`;
4. entries under `* Archive` → `archived_at` / `archive_reason` from
   `:ARCHIVE_TIME:` / `:ARCHIVE_OLPATH:`;
5. `#+TODO:` keyword map becomes `config.state_vocab`:
   `LEAD APPLIED INTERVIEW OFFER HOLD | HIRED REJECTED IGNORED`
   → `NEW ACTIVE INTERVIEW OFFER PAUSED | DONE REJECTED IGNORED`;
6. `application-questions.org` → `questions`; `:ANSWER:` → `answer`,
   `:SOURCE:`/`:NOTE:`/`:FORMAT:` carried over, empty answers → `NULL`;
7. print a diff summary (counts per state, #notes, #answers);
8. leave `jobs.org` untouched and git-tracked; `export-org` regenerates a
   read-only mirror while the Org agenda is still wanted.

Cutover: Python becomes the only writer, Org goes read-only (the parallel
`itempipe work` workers make dual-writing genuinely unsafe — two writers to
different files drift immediately).

---

## 13. Phases (each ends with something runnable)

| #   | Deliverable                                                                       | Done when                                                            |
| --- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| 0   | `pyproject.toml`, `config.py` (vocab + limits), `.env.example`, `itempipe --help` | `uv run itempipe --help` works                                       |
| 1   | `schema.sql`, `db.py`, `store.py` + `test_store_claim.py`                         | 20-way concurrent claim test passes; fencing + lease expiry tested   |
| 2   | item tools + `test_tools_contract.py`                                             | tools return strings on bad input; fenced tools refuse stale tokens  |
| 3   | answers + pause + reaper                                                          | pause → answer → auto-requeue works                                  |
| 4   | email tool + attempts guard                                                       | `--dry-run` renders MIME; stuck `submitting` lands in `review_state` |
| 5   | `web_search`/`fetch_page`; per-worker Playwright MCP                              | 2 workers browse concurrently with separate profiles                 |
| 6   | prompts + `agents.py` + `dispatch.py`                                             | `itempipe process --count 2 --max 4 --dry-run` on a scratch DB       |
| 7   | supervisor fan-out + `status`/`reap`/`work` CLI                                   | `itempipe run "one pass"` fans out search, drains apply              |
| 8   | `import-org` + cutover                                                            | counts match `jobs.org`; one real submission via Python              |
| 9   | optional: Postgres store behind the same interface, `due` command, `export-org`   | —                                                                    |

---

## 14. Traps to avoid (learned from the Emacs version, plus new ones)

1. **Global "current item"** — was a `defvar` marker shared by every call. Now:
   per-worker `WorkerContext` _and_ a fencing token in the DB, because an
   in-process fix alone still allows a stale worker in another process.
2. **Dupes by regexp** — normalise once into `name_key`, UNIQUE. Never compare
   headings at the edge.
3. **Silent lookup miss** — `cv-jobs--lead-at` returning nil under a `when`
   dropped `CREATED`/`NOTES` on every item. Assert `rowcount`.
4. **Unanswered questions must stay unanswerable** — filter `answer IS NOT
NULL`.
5. **Pause is a state, not a note** — and it must be un-pausable automatically
   once the answer exists, or items silently rot.
6. **Verbatim answers** — keep the raw text (salary strings etc.); the trailing
   hint lines are what let the model pick the right unit.
7. **Large context** — never inline the tracker; `list_items(limit=200)`.
8. **Email subject lines** — ads that demand a specific subject were filtered
   because the tool could not set one; `subject_override` fixes that.
9. **`msmtp -t` vs SMTP** — default to msmtp so no new credentials are needed.
10. **Headless** — the current Chromium is bwrap'd on Wayland; unattended
    workers need `--headless` and a persistent per-worker profile.
11. **Chromium profile lock** — two MCP servers on the same `--user-data-dir`
    fail to start. One profile dir per worker, always.
12. **`asyncio.sleep` in lease renewal must survive a hung browser** — renew
    from a separate task, not between tool calls, or a slow page silently
    expires the lease mid-application.
13. **Double submission is worse than a stuck item.** Prefer `NEEDS_REVIEW` to
    a retry whenever an irreversible step may have run (§6).
14. **Renaming later** — a domain word leaking into code turns the rename into a
    refactor. Enforce §0 in review; the only sanctuaries are `prompts/` and
    config values.
