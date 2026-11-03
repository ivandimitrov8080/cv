# itempipe

Two agents against one SQLite file. `search` finds job leads and records them; `apply`
claims leads from the same queue and drives a browser through the application. Each runs in
as many parallel workers as you ask for.

```sh
nix develop
python -m itempipe init                        # create/upgrade the db, show the queue
python -m itempipe search -n 4                 # until Ctrl-C; see itempipe/search.toml
python -m itempipe apply -n 2
python -m itempipe questions                   # open headings as file:line, for Emacs
(cd py && python -m unittest -v test_itempipe)  # queue tests, no model or network needed
```

That is the whole interface. The program is `itempipe/__init__.py` — one peewee model,
five apply tools and three search tools, two coroutines — plus the two prompts in
`itempipe/prompts/`, `itempipe/status.py`, the progress board, and
`itempipe/search.toml`, which holds **everything about the job market**. The module knows
how to search, never what to look for: no stack, country, site or job title appears in
the Python.

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
  just means looking again. A worker that already holds a claim gets that lead back
  instead of a second one.
* A live worker renews its lease from the database every `LEASE/3`, so a long form cannot
  expire mid-application and be handed to a second agent.
* Every later write is fenced by the claim token, and `begin_submission`/`finish_item`
  check that their own `UPDATE` actually changed the row, so a worker whose lease expired
  can never finish someone else's lead.
* `begin_submission` is written *before* the irreversible submit. A worker that dies
  mid-submit leaves the lead in `NEEDS_REVIEW` on the next claim, never in a second
  application.
* Claiming also picks up rows whose lease expired, and the apply loop waits on
  `claimable()` rather than only `NEW`, so a killed worker's work is picked up too.

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

`apply` starts **one** `playwright-mcp` process, and *that* process owns the browser. It
launches a single Chromium — headed by default, so you can watch it — with the persistent
profile at `/tmp/itempipe-chromium`, so the run has one window, one profile and one set of
logins. `itempipe` itself never launches a browser: a browser of our own plus the one the
server launches is exactly the two-windows bug, so only one process is allowed to start
one.

The server gives each worker a numbered tab in that one window, and every browser tool
call selects the caller's tab under a lock before touching it. So there is one tab per
worker, a worker's tools only ever see its own page, and `browser_tabs`/`browser_close`
are hidden from the model.

* The profile is `/tmp/itempipe-chromium` (`ITEMPIPE_PROFILE` to change it), passed as
  `--user-data-dir`, so logins survive a run. Under `chromium-sandboxed` that path may be
  inside the sandbox's tmpfs; bind-mount a profile path into the wrapper if you want them
  to survive a reboot.
* `ITEMPIPE_HEADLESS=1` adds `--headless`; only do that where there is no display, and
  note that Chromium's headless mode refuses to start with more than one target
  ("Multiple targets are not supported in headless mode"), so headed is the safe default.
* `ITEMPIPE_CHROME=/path/to/chromium` adds `--executable-path`, which is how you point it
  at `chromium-sandboxed` or a system build. Left unset, playwright-mcp uses its own
  browser, which is the path that needs no configuration.
* A second `apply` run on the same profile fails with "Browser is already in use"; the
  message printed says so and names `ITEMPIPE_PROFILE`. A missing display, a locked
  profile and a browser that will not launch each have their own line, taken from the log.
* Spawning one `playwright-mcp` per worker would create one Playwright browser context per
  worker — visibly, a new window for each. One server with a tab pool avoids that: all
  tabs share the browser and its logins. A page that opens a popup moves its worker's tab
  number; the shared browser re-reads the list after every call, so the next tool call
  follows the worker to the page it actually opened.
* The MCP connection itself is short: the official `mcp` SDK's
  `Client(StdioServerParameters(command="playwright-mcp", args=["--user-data-dir", …]))`
  spawns the one server over stdio, handshakes and lists its tools, and each tool is
  wrapped in a LangChain `StructuredTool` whose `args_schema` is the tool's own JSON
  Schema dict — passed through, not rebuilt, so the model sees exactly what the server
  declared. `SharedBrowser` binds each wrapper to one tab and serialises the select+call
  pair. The connection is held open for as long as any worker runs, and closing it closes
  the browser. LangChain 1 can do the same conversion via `langchain.mcp.MCPAdapter`, but
  that namespace is FastMCP underneath (`fastmcp>=4`), so it is not used and neither
  `fastmcp` nor the old `langchain-mcp-adapters` is a dependency. `search` never touches
  MCP.
* playwright-mcp's own stderr — including the browser's — goes to `/tmp/itempipe-mcp.log`
  (`ITEMPIPE_MCP_LOG`) rather than the terminal, which the progress board is drawing on.
  If the browser will not come up, `apply` prints the last lines of that log plus the
  likely fix and exits 1, rather than a traceback.
* Each tool result is capped at `ITEMPIPE_MCP_OUTPUT` (20000) characters, because a page
  snapshot can be longer than the context it is about to land in.
* Tools coming from playwright-mcp answer like the others: a failed call, an `isError`
  result or a dead subprocess comes back as `NOT done: …` rather than an exception, so a
  worker is never killed mid-node by the browser.

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

## Questions, and the leads they hold

`apply` never guesses. Before filling a required free-text or personal-detail field it
calls `lookup_answer`, which reads `application-questions.org` (`ITEMPIPE_QUESTIONS`) —
the same file a human edits: one heading per question, the answer in an `:ANSWER:`
property, `:NOTE:`/`:FORMAT:`/`:SOURCE:` for context, `:DECLINE: t` to refuse the field.
A heading with neither an `:ANSWER:` nor a `:DECLINE:` is an open question.
Every entry — yours and the agent's — has the same shape, three-space drawer and all:

```org
** What is your notice period?
   :PROPERTIES:
   :ANSWER: One month
   :NOTE: From the day the contract is signed.
   :SOURCE: Acme (Senior Java Engineer)
   :END:
```

The file is plain Q/A and nothing about a question is stored on a lead: one answer serves
every application that asks it, and a lead can be waiting on several questions at once.
When a required field asks something no heading answers, the worker calls `save_question`
for that one field and then `finish_item("hold")`. `save_question` writes that block
with an empty `:ANSWER:` and the company in `:SOURCE:`, so answering it is typing after
the colon — and it **only ever appends**, never rewriting, reordering or editing an
existing entry, so nothing you wrote can be lost. The lead moves to `HOLD`: out of the
queue, not archived.

### One question, however it is worded

The file is searched with [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz)'s token-set
ratio, not by comparing strings: word order, filler and the words only one side has are
forgiven, and a heading whose words all appear in the question scores 100 however long
the question is. Two words of the form's question are also searched on their own, because
a long sentence otherwise drowns them; the three closest headings come back.

That is what keeps the file from growing: `** Postal address` answers "What is your full
postal address - Address Line 1, City, State/Province and Zip or Postal Code?" and
`save_question` refuses to append a second heading for a question the file already asks.
Parenthetical asides, links and bare numbers are dropped before matching — a form's
provenance is already in `:SOURCE:`, and it is the part that made ten address headings
out of one field — and an appended heading keeps only the form's own name for the field.

### One heading, every wording: `:ALIASES:`

A short heading is only half of it: forms use different words for the same field. The
synonyms live in the entry, next to the heading, never in the Python:

```org
** Relocation
   :PROPERTIES:
   :ANSWER: No, remote only.
   :ALIASES: relocate, relocating, relocation, realocation, moving, spain, portugal, elsewhere
   :SOURCE: Qualifyze (relocating to Spain), HumanIT (relocation to Portugal)
   :END:
```

That one heading answers "Are you open to relocating to Spain if you are currently based
elsewhere?", "Available for Realocation to Portugal" (typo included), and "Are you
willing to relocate?" alike — which is how two relocations became one question. The same
trick keeps one `** Postal address` covering Hazelcast, Dynatrace, Taulia and Graphwise.

Where several headings fit a question, the one the question spells out wins, then an
answer over a refusal, then the more specific heading — so a form asking just "City"
gets the saved current location rather than the address refusal whose aliases list
`city`.

Matches are deliberately *strict* when they decide something: one wording has to cover
every meaningful word of the other before it counts as the same question, so "experience
with Go" and "experience with Node.js" stay apart, and "current salary" is not answered
by "salary expectation". Loose matches only ever *show* the agent the three nearest
headings; they never answer a field on their own.

### Declining a field

A heading that says `:DECLINE: t` instead of carrying an `:ANSWER:` is a field you will
not answer at all. It counts as settled, not open: `lookup_answer` reports it as DECLINED
and tells the agent to leave the field blank, `save_question` refuses to ask it again (a
reworded question that a declined heading covers is declined too), and a lead held on it
is released like any other answer. Give the entry an `:ANSWER:` as well and that value is
the fallback the agent may type where a form insists on text — still nothing invented. A
form that will not submit without the field is archived with that reason; a lead is never
held for a refusal you have already made.

Keep a declined heading **short and generic** — `** Postal address`, not one form's whole
sentence, with its synonyms in `:ALIASES:`. A two-word heading is contained in every
form's wording of that field, which is exactly what makes one refusal cover all of them.

### Answering from Emacs

The file is the interface; `itempipe` only points at it. `itempipe questions` prints every
open heading as `path:line: …`, so

* `M-x grep RET itempipe questions RET` (or `M-x compile`) makes each line a jump, and
  `C-x \`` walks the open ones: answer where you edit, no other UI to learn;
* `itempipe questions postal` fuzzy-finds the headings matching those words — the same
  search `lookup_answer` uses — which is how you find the entry to decline;
* `itempipe init` reports the tally, the open headings, and groups of headings that ask
  the same thing, so a pile of old variants can be merged by hand.

Tucked into your init file, that is one command:

```elisp
(defun itempipe-questions (words)
  "Jump to the questions `words' describe, from `itempipe questions'."
  (interactive "sWords (empty for every open question): ")
  (grep (string-trim (concat "itempipe questions " words))))
```

Marking the heading at point as a refusal is one key, and writes the drawer for you:

```elisp
(defun itempipe-decline-question (&optional undecline)
  "Mark the question at point `:DECLINE: t' — I will not answer this field.
With a prefix argument, remove the decline instead."
  (interactive "P")
  (unless (derived-mode-p 'org-mode) (user-error "Not an Org buffer"))
  (org-back-to-heading t)
  (if undecline
      (org-entry-delete nil "DECLINE")
    (org-entry-put nil "DECLINE" "t"))
  (message "%s: %s" (if undecline "Undecided" "Declined") (org-get-heading t t t t)))

(with-eval-after-load 'org
  (define-key org-mode-map (kbd "C-c D") #'itempipe-decline-question))
```

`C-c D` on the heading, save, run `itempipe init`: the leads waiting on it come back to
the queue and the field is never asked again — with `:ANSWER:` kept as the value the
agent may type where a form refuses to submit empty (that is what `N/A` is in `Postal
address`). `C-u C-c D` removes the decline.

Which questions a lead is waiting on is bookkeeping, not content: a small `hold` table
maps the lead's id to the question's normalised wording, and the question itself lives
only in the file. That is what lets one lead wait on several questions and two leads wait
on the same one. Fill in an `:ANSWER:` (or mark the heading `:DECLINE: t`) and the next
run releases every lead held on it (`unhold`, at startup); a lead held on two questions
waits for both. `itempipe init` counts both sides and names what each held lead is
waiting for:

```
py/itempipe.db: 41 lead(s), 3 queued, 812 seen row(s), ...
/home/ivand/src/cv/application-questions.org: 24 answered, 3 declined, 2 open; 2 lead(s) on hold
  on hold: HumanIT — waiting on What is your notice period?
```

Each application gets `ITEMPIPE_APPLY_STEPS` (120) LangGraph steps, because a form is
many tool calls. Hitting that limit, a dead tab or a dead MCP session is reported on the
board and puts the lead back in the queue; it never ends the run or the traceback.

## State

`NEW` → claimed as `WORKING` → `ACTIVE` (applied), `HOLD` (waiting on questions only you
can answer), or archived with a reason. `NEEDS_REVIEW` means a submit was interrupted; a
human should look at it.
