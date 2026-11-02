"""Search leads, apply to them: two agents, N workers each, one SQLite queue.

The claim is a compare-and-swap (read the next free row, then UPDATE it guarded by the token
it had), so the database is the lock. Every later write is fenced by that token, and
`submitting` is set before the irreversible step, so a crash goes to review, not a retry.

Search works the same way one level up. A *session* is one conversation that records one
lead and then ends, so no worker ever drags a 60-step transcript around. What outlives it
is the `seen` table: every URL and query a worker has looked at and how it turned out. A
clean session therefore starts blind to the conversation but not blind to the work.
"""

import argparse, asyncio, contextlib, os, re, shutil, sys, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.tools import tool
from peewee import (BooleanField, CharField, DateTimeField, IntegrityError, Model,
                    SqliteDatabase, TextField, fn)

from . import status

ROOT = Path(__file__).resolve().parents[2]
NEW, WORKING, ACTIVE, REVIEW = "NEW", "WORKING", "ACTIVE", "NEEDS_REVIEW"
FENCED = "This lead is no longer yours; stop."
LEASE = timedelta(minutes=30)
RETRY = timedelta(days=7)           # dead page / empty query: one more look after a week
RETENTION = timedelta(days=int(os.environ.get("ITEMPIPE_SEEN_DAYS", "365")))
DB = Path(os.environ.get("ITEMPIPE_DB", ROOT / "py/itempipe.db"))
PROMPTS, CV = Path(__file__).with_name("prompts"), ROOT / "cv.typ"
CDP_PORT = int(os.environ.get("ITEMPIPE_CDP_PORT", "9222"))
CHROME = shutil.which("chromium-sandboxed") or shutil.which("chromium")
PROFILE = Path(os.environ.get("ITEMPIPE_PROFILE", "/tmp/itempipe-chromium"))
CHROME_LOG = Path(os.environ.get("ITEMPIPE_CHROME_LOG", f"/tmp/itempipe-chromium-{CDP_PORT}.log"))
SEARCH_TRIES = int(os.environ.get("ITEMPIPE_SEARCH_TRIES", "3"))
SESSIONS = int(os.environ.get("ITEMPIPE_SESSIONS", "6"))        # hard stop per worker
STEPS = int(os.environ.get("ITEMPIPE_SESSION_STEPS", "26"))     # graph steps per session
BUDGET = int(os.environ.get("ITEMPIPE_TOOL_BUDGET", "12"))      # tool calls per session
SEEDS = int(os.environ.get("ITEMPIPE_BOARD_SEEDS", "3"))        # boards handed to a session
HN_THREAD = os.environ.get("ITEMPIPE_HN_THREAD", "49922569")    # this month's who-is-hiring

ANGLES = ('HN "Who is hiring?"', "YC Work at a Startup", "niche boards", "company careers",
          "practitioner communities", "freelance / contract")

# Where each angle starts. A worker is handed the first few boards it has not opened yet;
# when the list runs out the angle is done. An empty list means the angle is search-driven
# (find a company, then its own careers page), so its sessions start from a query instead.
ANGLE_BOARDS = {
    'HN "Who is hiring?"': [
        f"https://news.ycombinator.com/item?id={HN_THREAD}",
        "https://hnhiring.com/technologies/java",
        "https://hnhiring.com/technologies/spring",
        "https://hnhiring.com/technologies/react",
    ],
    "YC Work at a Startup": [
        "https://www.ycombinator.com/jobs/role/software-engineer/remote",
        "https://www.ycombinator.com/jobs/role/back-end-engineer/remote",
        "https://www.ycombinator.com/jobs/role/full-stack/remote",
    ],
    "niche boards": [
        "https://weworkremotely.com/categories/remote-programming-jobs",
        "https://remoteok.com/remote-java-jobs",
        "https://remotive.com/remote-jobs/software-dev",
        "https://justjoin.it/all/java",
    ],
    "company careers": [],
    "practitioner communities": [
        "https://lobste.rs/t/jobs",
        "https://old.reddit.com/r/forhire/new/",
    ],
    "freelance / contract": [
        "https://weworkremotely.com/categories/remote-contract-jobs",
        "https://old.reddit.com/r/forhire/search?q=java&restrict_sr=on&sort=new",
    ],
}

# A `seen` verdict: what one check of one URL or query came to.
CLAIMED, OPEN, DEAD = "claimed", "open", "dead"           # a URL
SEARCHED, NOQUERY = "searched", "noresults"              # a query
SKIPPED, DUPLICATE, RECORDED = "skipped", "duplicate", "recorded"

CANNED = re.compile(r"need more steps to process|sorry, i (?:can'?t|cannot)", re.I)

APPLY = ("Claim one lead with claim_next_item, work it to a final state with finish_item, then "
         "stop. If a tool says the lead is no longer yours, stop immediately. You share one "
         "browser with the other workers: work only in the tab your own tools are on, and "
         "never switch, open or close tabs.")

db = SqliteDatabase(None, pragmas={"journal_mode": "wal", "busy_timeout": 10000})


class Startup(RuntimeError):
    """The browser could not be had; the message says what to do about it."""


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Item(Model):
    """A lead. `submitting` is the at-most-once flag; the claim token fences every write."""
    name = CharField()
    name_key = CharField(unique=True)       # lowercased name: the dedupe key
    title = CharField()
    url = CharField()
    notes = TextField(null=True)
    state = CharField(default=NEW)
    submitting = BooleanField(default=False)
    advanced_on = DateTimeField(null=True)
    archived_at = DateTimeField(null=True)
    archive_reason = CharField(null=True)
    claim_token = CharField(null=True)
    claimed_until = DateTimeField(null=True)

    class Meta:
        database = db


class Seen(Model):
    """Where the workers have been: one row per page or query, shared by every session.

    Not a lead — nothing claims or applies to a `seen` row. Its job is to stop the tenth
    worker opening the page the first one already read, and to survive the session that
    read it.
    """
    key = CharField(unique=True)            # normalised URL or query: the dedupe key
    kind = CharField(default="url")         # url | query
    url = TextField(null=True)              # as first written, for the brief
    verdict = CharField(default=CLAIMED)
    note = CharField(null=True)             # HTTP status, hit count, or the company
    angle = CharField(null=True)
    session = CharField(null=True)          # the session that last wrote the verdict
    checked_at = DateTimeField(default=now)

    class Meta:
        database = db


def flag(name):
    """An env var read as a switch: unset, 0, false, no, off all mean no."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def open_db(path=DB):
    """Point peewee at `path` and make sure both tables exist."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not db.is_closed():
        db.close()
    db.init(str(path))
    db.connect()
    Item.create_table(safe=True)
    Seen.create_table(safe=True)
    return path


def queued():
    return Item.select().where(Item.state == NEW, Item.archived_at.is_null()).count()


def free():
    """Unclaimed, or claimed and abandoned long enough ago to be retryable."""
    return Item.select().where(Item.archived_at.is_null(),
                               (Item.state == NEW) |
                               ((Item.state == WORKING) & (Item.claimed_until < now())))


def tally():
    """Counts per state for the board's footer; archived leads are not counted."""
    counts = {state: 0 for state in (NEW, WORKING, ACTIVE, REVIEW)}
    rows = (Item.select(Item.state, fn.COUNT(Item.id).alias("n"))
            .where(Item.archived_at.is_null()).group_by(Item.state).tuples())
    for state, count in rows:
        counts[state] = count
    return counts


open_db()   # so `import itempipe` is usable; the CLI re-points it at --db


@dataclass
class Worker:
    """One claim: the token stored in the row is the only authority to write it."""
    item: Item | None = None

    def claim(self):
        # A submit that outlived its lease may have gone through: review it, never retry it.
        (Item.update(state=REVIEW, submitting=False, claim_token=None, claimed_until=None)
         .where(Item.state == WORKING, Item.submitting == True, Item.claimed_until < now())
         .execute())
        token = uuid.uuid4().hex
        for _ in range(24):         # compare-and-swap; losing the race means looking again
            row = free().order_by(Item.id).first()
            if row is None:
                return "No leads left. Stop."
            guard = (Item.claim_token.is_null() if row.claim_token is None
                     else Item.claim_token == row.claim_token)
            won = (Item.update(state=WORKING, claim_token=token, claimed_until=now() + LEASE)
                   .where(Item.id == row.id, guard).execute())
            if won:
                self.item = Item.get(Item.claim_token == token)
                return (f"Id: {self.item.id}\nName: {self.item.name}\n"
                        f"Title: {self.item.title}\nUrl: {self.item.url}\n"
                        f"Notes: {self.item.notes or '-'}")
        return "Too much contention. Stop."

    def mine(self):
        if self.item is None:
            return None
        return Item.get_or_none(Item.id == self.item.id, Item.state == WORKING,
                                Item.claim_token == self.item.claim_token)

    def end(self, **fields):
        """Update the row and drop the claim, both fenced by the token we hold."""
        if self.item is None:
            return False
        done = (Item.update(claim_token=None, claimed_until=None, **fields)
                .where(Item.id == self.item.id, Item.state == WORKING,
                       Item.claim_token == self.item.claim_token).execute())
        self.item = None
        return done == 1


def hits_text(found):
    """Render ddgs rows for the model; empty string when nothing was found."""
    return "\n\n".join(
        "\n".join(filter(None, (row.get("title"), row.get("href"), row.get("body"))))
        for row in found)


def search_failure(reason, query, tries):
    """The message the agent gets instead of an exception: what happened, what to do."""
    if "rate" in reason.lower() or "429" in reason:
        return (f"NOT searched: the search backends are rate limiting us ({reason}). Do not "
                "retry straight away. Use fetch_page on a URL you already have, or work "
                "another angle, and leave this query for later.")
    return (f"NOT searched: no results for {query!r} after {tries} tries ({reason}). Try a "
            "shorter or differently worded query, a company name, or site: a specific "
            "board. Do not repeat the same query. Finding nothing here is not a failure.")


def url_key(url):
    """One page, one identity: no scheme, no www, no trailing slash, no trackers."""
    u = urlsplit(clean(url))
    host = (u.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", u.path).rstrip("/") or "/"
    keep = [p for p in u.query.split("&") if p and not
            re.match(r"(utm_|ref=|src=|source=|gh_src|gclid|fbclid|mc_cid)", p, re.I)]
    query = "&".join(sorted(keep))
    return f"{host}{path}" + (f"?{query}" if query else "")


def query_key(query):
    """One query, one identity: case, punctuation and whitespace folded away."""
    return "q:" + re.sub(r"[^a-z0-9]+", " ", clean(query).lower()).strip()


def retryable(row, session=None):
    """May this row be looked at again?

    `claimed` is a lease, because a worker can die mid-fetch. A dead page or an empty
    query gets one more look after a week — a 404 can be a bad deploy. Everything else is
    fair game in the *next* session, which is what makes a clean session per lead cheap.
    A recorded lead is never revisited.
    """
    if row.verdict == RECORDED:
        return False
    if row.verdict == CLAIMED:
        return row.checked_at < now() - LEASE
    if row.verdict in (DEAD, NOQUERY):
        return row.checked_at < now() - RETRY
    return row.session != session


def claim_seen(key, kind="url", url=None, angle=None, session=None):
    """Try to be the one who checks `key`. Returns (won, row).

    won=True means go ahead: nobody holds it, or the holder's verdict has gone stale.
    won=False means the row is settled (or another worker is on it) and `row` says how.
    """
    for _ in range(2):
        try:
            Seen.create(key=key, kind=kind, url=clean(url) or None, angle=angle,
                        session=session)
            return True, None
        except IntegrityError:
            row = Seen.get_or_none(Seen.key == key)
            if row is None:                 # deleted under us: try to create it again
                continue
            if not retryable(row, session):
                return False, row
            if (Seen.update(verdict=CLAIMED, note=None, checked_at=now(), session=session,
                            angle=angle)
                    .where(Seen.key == key, Seen.verdict == row.verdict).execute()):
                return True, None
            return False, Seen.get_or_none(Seen.key == key)
    return False, None


def settle(key, verdict, note=None, session=None, kind="url", url=None):
    """Write what the check learned, so a later session reads it instead of retrying."""
    fields = dict(verdict=verdict, note=clean(note)[:250] or None, session=session,
                  checked_at=now())
    if Seen.update(**fields).where(Seen.key == key).execute():
        return True
    try:                # create_item may be the first thing to touch this URL
        Seen.create(key=key, kind=kind, url=clean(url) or None, **fields)
    except IntegrityError:      # another worker won the insert; its verdict stands
        pass
    return False


def seen_refusal(row, action):
    """What the model is told instead of a page or query it may not repeat."""
    if row is None or row.verdict == CLAIMED:
        return (f"NOT {action}: another worker is checking that right now. Take the next "
                "board or query.")
    what = f"{row.verdict}{f' ({row.note})' if row.note else ''}"
    return (f"NOT {action}: already checked — {what}. Do not retry it; take the next board "
            "or query, and record whatever you have already verified.")


def recent_rows(angle, k=12):
    """What this angle has settled, newest first: the clean session's memory."""
    return (Seen.select().where(Seen.angle == angle)
            .order_by(Seen.checked_at.desc()).limit(k))


def frontier(angle, offset=0, k=SEEDS):
    """The boards this angle has not opened yet, rotated by `offset` so that two workers
    sharing one angle do not start on the same page."""
    boards = [u for u in ANGLE_BOARDS.get(angle, ())
              if (row := Seen.get_or_none(Seen.key == url_key(u))) is None
              or (row.verdict == DEAD and retryable(row))]
    return (boards[offset:] + boards[:offset])[:k] if boards else []


def reconcile(sess):
    """Settle what the session touched and did not finish.

    A page it opened and did not record was judged, not forgotten, and a claim left
    hanging by a session that died is handed back at once instead of after the lease.
    """
    return (Seen.update(verdict=SKIPPED, note="not recorded", checked_at=now())
            .where(Seen.session == sess.token, Seen.verdict.in_((OPEN, CLAIMED))).execute())


def prune(older=RETENTION):
    """Forget browsing memory older than `older`. Leads are never touched."""
    return Seen.delete().where(Seen.checked_at < now() - older).execute()


@dataclass
class Session:
    """One search conversation.

    All of its memory is in `seen`; this is only the bookkeeping it needs while it runs:
    which angle, which lead it recorded, and how much of its tool budget it has spent.
    """
    angle: str
    tag: str = "·"
    token: str = ""
    recorded: list = field(default_factory=list)
    calls: int = 0

    def __post_init__(self):
        self.token = self.token or uuid.uuid4().hex

    def spend(self):
        self.calls += 1
        return self.calls


def budget_note(sess):
    """Say the budget is nearly gone, so the model writes up instead of drifting."""
    left = BUDGET - sess.calls
    if left <= 0:
        return ("\n\nBUDGET SPENT: no more tools. Write the report now and say plainly "
                "what you did not get to.")
    if left <= 3:
        return (f"\n\nBUDGET: {left} tool call(s) left. Record the best lead you have "
                "verified, then stop.")
    return ""


def spent(sess):
    """A session is one lead long: after the first one, every tool says so."""
    if sess.recorded:
        return ("SESSION OVER: this session already recorded its lead. Stop calling tools "
                "and write your report — a fresh session continues after you. If you "
                "verified a second lead, name it in the report so it is not lost.")
    return None


def tools_for(w=None, sess=None):
    """Search tools when `w` is None, apply tools when it is a Worker.

    No search tool raises, and every URL or query one touches is written to `seen`, so the
    memory of where a worker has been outlives the conversation it was written in.
    """
    sess = sess or Session(angle="·")

    @tool
    def create_item(name: str, title: str, url: str, notes: str = "") -> str:
        """Record one company, its exact title and the posting's own http(s) URL, with why
        it fits in notes. Duplicates, archived ones included, are refused."""
        if stop := spent(sess):
            return stop
        name, title, url = clean(name), clean(title), clean(url)
        if not (name and title and url.startswith("http")):
            return "NOT recorded: need a company, an exact title and the posting's own URL."
        key, lower = url_key(url), name.lower()
        already = Seen.get_or_none(Seen.key == key, Seen.verdict == RECORDED)
        if already is not None:
            return (f"Already recorded as {already.note or 'a lead'} — do not record it "
                    "again.")
        try:
            Item.create(name=name, name_key=lower, title=title, url=url,
                        notes=clean(notes) or None)
        except IntegrityError:
            found = Item.get(Item.name_key == lower)
            settle(key, DUPLICATE, found.name, sess.token, url=url)
            return (f"Already tracked ({'archived' if found.archived_at else found.state}): "
                    f"{found.name}.")
        settle(key, RECORDED, name, sess.token, url=url)
        sess.recorded.append(name)
        return (f"Recorded: {name} — {title} <{url}>. That is this session's one lead: "
                "stop now and write your report.")

    @tool
    async def web_search(query: str) -> str:
        """Search the web once; then open the individual posting with fetch_page."""
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException
        if stop := spent(sess):
            return stop
        sess.spend()
        text, key = clean(query), query_key(query)
        won, row = claim_seen(key, kind="query", url=text, angle=sess.angle,
                              session=sess.token)
        if not won:
            return seen_refusal(row, "searched") + budget_note(sess)
        reason = "no engine answered"
        for attempt in range(SEARCH_TRIES):
            try:                        # ddgs raises where it could return an empty list
                found = await asyncio.to_thread(
                    lambda: list(DDGS(timeout=15).text(text, max_results=8) or []))
            except DDGSException as exc:
                reason = clean(exc) or "no results"
            except Exception as exc:    # a broken engine must not kill the worker
                reason = f"{type(exc).__name__}: {clean(exc)}"
            else:
                if found:
                    settle(key, SEARCHED, f"{len(found)} hits", sess.token, kind="query")
                    return hits_text(found)[:6000] + budget_note(sess)
                reason = "no results"
            if attempt + 1 < SEARCH_TRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
        settle(key, NOQUERY, reason, sess.token, kind="query")
        return search_failure(reason, text, SEARCH_TRIES) + budget_note(sess)

    @tool
    async def fetch_page(url: str) -> str:
        """Fetch one URL as readable text, to check a posting is still open."""
        import httpx, trafilatura
        if stop := spent(sess):
            return stop
        url = clean(url)
        if not url.startswith(("http://", "https://")):
            return "NOT fetched: need an http(s) URL."
        sess.spend()
        key = url_key(url)
        won, row = claim_seen(key, kind="url", url=url, angle=sess.angle, session=sess.token)
        if not won:
            return seen_refusal(row, "fetched") + budget_note(sess)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            settle(key, OPEN, f"{type(exc).__name__}", sess.token)
            return (f"NOT fetched: {type(exc).__name__}: {clean(exc)}. The site may be "
                    "down or slow; take another board and come back in a later session."
                    + budget_note(sess))
        if response.status_code >= 400:
            settle(key, DEAD, f"HTTP {response.status_code}", sess.token)
            return (f"NOT fetched: HTTP {response.status_code} for {url}. The posting is "
                    "probably gone or behind a login, so record nothing from it."
                    + budget_note(sess))
        try:
            text = await asyncio.to_thread(trafilatura.extract, response.text)
        except Exception:               # unparseable html: hand over the raw text
            text = None
        body = "\n".join(l for l in ((text or response.text).splitlines()) if l.strip())
        settle(key, OPEN, f"HTTP {response.status_code}", sess.token)
        return ((body.strip()[:8000] or "Fetched, but the page has no readable text.")
                + budget_note(sess))

    @tool
    def claim_next_item() -> str:
        """Claim the next lead and return it with its URL. Call this first."""
        return w.claim()

    @tool
    def begin_submission() -> str:
        """Call immediately before the final submit, so a crash goes to review, not a retry."""
        item = w.mine()
        if item is None:
            return FENCED
        Item.update(submitting=True).where(Item.id == item.id,
                                           Item.claim_token == item.claim_token).execute()
        return "Submission guard set."

    @tool
    def finish_item(outcome: str, reason: str = "") -> str:
        """End the claimed lead: "applied" (after begin_submission), "retry" (site down,
        timeout) or "archive" (cannot apply; put the blocker, including any question you
        cannot answer, in reason)."""
        item = w.mine()
        if item is None:
            return FENCED
        if outcome == "applied":
            if not item.submitting:
                return "NOT done: call begin_submission immediately before the final submit."
            w.end(state=ACTIVE, submitting=False, advanced_on=now())
        elif outcome == "retry":
            w.end(state=REVIEW if item.submitting else NEW, submitting=False)
        else:
            w.end(archived_at=now(), submitting=False,
                  archive_reason=clean(reason) or "abandoned")
        return f"{item.name}: {outcome} ({clean(reason) or 'no reason'})."

    return [claim_next_item, begin_submission, finish_item] if w else [
        create_item, web_search, fetch_page]


def agent(role, tool_list):
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent
    model = ChatOpenAI(model=os.environ.get("ITEMPIPE_MODEL", "deepseek-chat"),
                       base_url=os.environ.get("ITEMPIPE_BASE_URL", "https://api.deepseek.com/v1"),
                       api_key=os.environ.get("DEEPSEEK_API_KEY", "missing"), timeout=180)
    prompt = (PROMPTS / f"{role}.md").read_text(encoding="utf-8").replace("{{CV}}", CV.read_text())
    return create_react_agent(model, tool_list, prompt=prompt)


async def run(agent_, text, tag="·", finish=True, limit=60):
    """Stream the graph's node updates, so every tool call shows up while it happens.

    A model that stops with a canned "need more steps" sentence instead of a report is
    named as such: the old code promoted that sentence to "the report" and marked the row
    done, which is how a run of ten workers looked like it had explained itself.
    """
    answer, made = "", 0
    async for update in agent_.astream({"messages": [{"role": "user", "content": text}]},
                                       config={"recursion_limit": limit},
                                       stream_mode="updates"):
        for payload in update.values():
            for message in (payload or {}).get("messages", []):
                if message.type == "tool":
                    status.worker(tag, "result", message.content)
                elif calls := getattr(message, "tool_calls", None):
                    made += len(calls)
                    for call in calls:
                        status.worker(tag, "call", f"{call['name']} {call['args']}")
                elif message.content:
                    answer = str(message.content)
    if not answer.strip() or CANNED.search(answer):
        answer = (f"[no report: the model stopped without one after {made} tool call(s); "
                  "see the items in the tracker]")
    if finish:
        status.finished(tag, answer)
    return answer.strip()


def session_brief(brief, i, workers, n, angle, boards, recorded):
    """What one clean session is told: the angle, where to start, and what is settled."""
    start = ("\n".join(f"- {url}" for url in boards) if boards else
             "- no fixed board on this angle: web_search for it, then open the posting")
    recent = "\n".join(
        f"- {row.url}{f' ({row.note})' if row.note else ''} — {row.verdict}"
        for row in recent_rows(angle)) or "- nothing yet"
    return f"""You are search worker {i + 1} of {workers}, session {n} on angle: {angle}.

This session is one lead long: verify one posting, call create_item once, then stop and
write your report. A fresh session starts immediately after you with a clean memory, so
do not survey a whole board and do not save findings up.

Start here, fetching each once:
{start}

Already settled by this run or an earlier one. A tool that answers "already checked" is
pointing you elsewhere, not failing:
{recent}

Recorded by this worker so far: {', '.join(recorded) or 'nothing'}

{brief}"""


async def search(brief, workers=4):
    """Each session starts from a clean context and ends after one recorded lead.

    The durable part is the `seen` table, not the model: which boards were opened, which
    postings were dead, which queries came back empty and which leads were recorded all
    outlive the conversation, and are shared with every other worker.
    """
    async def one(i):
        tag, angle, total, notes = f"w{i + 1}", ANGLES[i % len(ANGLES)], [], []
        offset = i // len(ANGLES)       # workers sharing an angle start further along
        miss = 0                        # sessions in a row that recorded nothing
        status.worker(tag, "state", f"angle: {angle}", state="searching")
        try:
            for n in range(SESSIONS):
                sess = Session(angle=angle, tag=tag)
                boards = frontier(angle, offset)
                if not boards and ANGLE_BOARDS.get(angle):
                    break               # the boards this angle starts from are all opened
                status.worker(tag, "state", f"{angle}: session {n + 1}, "
                              f"{len(boards) or 'no'} board(s)", state="searching")
                try:
                    notes.append(await run(
                        agent("search", tools_for(sess=sess)),
                        session_brief(brief, i, workers, n + 1, angle, boards, total),
                        tag=tag, finish=False, limit=STEPS))
                finally:
                    reconcile(sess)     # pages opened but not recorded were judged
                total += sess.recorded
                miss = 0 if sess.recorded else miss + 1
                if miss >= 2:           # two empty sessions: this angle is worked out
                    break
        except Exception as exc:        # one worker must not take the others down
            status.worker(tag, "state", f"{type(exc).__name__}: {clean(exc)}")
        best = max((text for text in notes if text), key=len, default="")
        status.finished(tag, f"{len(total)} recorded: {', '.join(total) or 'none'}")
        return (f"[{tag}] {angle} — {len(total)} recorded: {', '.join(total) or 'none'}"
                + (f"\n\n{best}" if best else ""))

    reports = await asyncio.gather(*(one(i) for i in range(workers)), return_exceptions=True)
    return [report if isinstance(report, str)
            else f"[worker crashed: {type(report).__name__}: {clean(report)}]"
            for report in reports]


def cdp_endpoint(port=CDP_PORT):
    return f"http://127.0.0.1:{port}"


async def _listening(endpoint, timeout=0.5):
    """True if something is already answering CDP there."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return (await client.get(f"{endpoint}/json/version")).is_success
    except Exception:
        return False


def _tail(lines=14):
    try:
        text = CHROME_LOG.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def chrome_hint(log):
    """Pick the likely cause out of chromium's own words, so the fix is obvious."""
    text = (log or "").lower()
    if "singletonlock" in text or "profile appears to be in use" in text:
        return (f"the profile {PROFILE} is in use by another Chromium. Close it, or set "
                "ITEMPIPE_PROFILE elsewhere.")
    if "no usable sandbox" in text or "running as root" in text:
        return "Chromium refuses its sandbox here; run it with --no-sandbox."
    if "cannot open display" in text or "missing x server" in text or "ozone" in text:
        return ("Chromium wants a display. Keep --headless, or run under a session with "
                "ITEMPIPE_HEADED=1.")
    if "address already in use" in text or "devtoolsactiveport" in text:
        return (f"CDP port {CDP_PORT} is taken. Kill the other browser, or set "
                "ITEMPIPE_CDP_PORT.")
    if "error while loading shared libraries" in text or "bwrap:" in text:
        return "Chromium (or the sandbox wrapper) could not start: see the log."
    if "permission denied" in text:
        return f"Chromium cannot write {PROFILE}; check its owner and the sandbox binds."
    return "No recognised cause; the log below is the whole story."


async def stop_proc(proc, timeout=5.0):
    """Terminate, then kill. A process that is already gone is not an error."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, ChildProcessError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout)
        return
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
    with contextlib.suppress(ProcessLookupError, ChildProcessError):
        proc.kill()
    with contextlib.suppress(ProcessLookupError, ChildProcessError):
        await asyncio.wait_for(proc.wait(), timeout)


async def chromium(port=CDP_PORT, timeout=20.0):
    """Start one headless Chromium that every worker's playwright-mcp attaches to.

    Returns the process when we started it, or None when a Chromium was already
    listening (a previous run, or one you started yourself): in that case it is not
    ours to kill. Every worker gets its own MCP session against this browser, so each
    holds its own tab; nothing else is shared but the process and its profile.

    Chromium's stderr goes to CHROME_LOG rather than the terminal, because a browser
    that refuses to start is silent on stdout and its last lines name the reason.
    """
    endpoint = cdp_endpoint(port)
    if await _listening(endpoint):
        return None
    if CHROME is None:
        raise Startup("no chromium on PATH; install chromium or point PATH at it.")
    PROFILE.mkdir(parents=True, exist_ok=True)
    CHROME_LOG.parent.mkdir(parents=True, exist_ok=True)
    args = [CHROME, f"--remote-debugging-port={port}", "--user-data-dir", str(PROFILE),
            "--no-first-run", "--no-default-browser-check", "about:blank"]
    if not flag("ITEMPIPE_HEADED"):
        args.insert(1, "--headless")
    with open(CHROME_LOG, "wb") as stderr:      # fresh log each run; the child keeps the fd
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=stderr)
    deadline, pause = time.monotonic() + timeout, 0.25
    while time.monotonic() < deadline:
        if await _listening(endpoint):
            return proc
        if proc.returncode is not None:         # it gave up; the log says why
            break
        await asyncio.sleep(pause)
    await stop_proc(proc)                       # kill() on a reaped process raises
    detail = _tail()
    how = (f"exited with {proc.returncode}" if proc.returncode is not None
           else f"was still running after {timeout:.0f}s")
    raise Startup("\n".join(filter(None, (
        f"chromium {how} without opening {endpoint}.",
        f"  {chrome_hint(detail)}",
        f"  log: {CHROME_LOG}",
        detail))))


def mcp_config(endpoint):
    """One worker's MCP server: no browser of its own, a CDP seat in the shared one."""
    return {"command": "playwright-mcp",
            "args": ["--cdp-endpoint", endpoint, "--cdp-timeout", "30000"],
            "transport": "stdio"}


async def browser(endpoint):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    client = MultiServerMCPClient({"playwright": mcp_config(endpoint)})
    return list(await client.get_tools())


async def apply(workers=2):
    slots, reports = asyncio.Semaphore(2), []
    proc = await chromium()
    try:
        async def one(i):
            tag, w = f"w{i + 1}", Worker()
            async with slots:
                status.worker(tag, "state", "attaching to browser", state="browser")
                a = agent("apply", tools_for(w) + await browser(cdp_endpoint()))
                while before := queued():
                    try:
                        reports.append(await run(a, APPLY, tag=tag))
                    finally:        # a worker that stopped mid-claim must not hold a lock
                        if item := w.mine():
                            w.end(state=REVIEW if item.submitting else NEW, submitting=False)
                    if queued() >= before:
                        return      # no progress: that lead is stuck, do not spin

        await asyncio.gather(*(one(i) for i in range(workers)))
    finally:
        if proc is not None:
            status.worker("··", "state", "closing browser")
            await stop_proc(proc)            # tolerates a browser that already died
    return reports


def main(argv=None):
    p = argparse.ArgumentParser(prog="itempipe", description=__doc__.splitlines()[0])
    p.add_argument("--db", default=str(DB))
    p.add_argument("command", choices=["init", "search", "apply"])
    p.add_argument("brief", nargs="?", default="")
    p.add_argument("-n", type=int, default=2, help="parallel workers")
    args = p.parse_args(argv)
    path = open_db(args.db)
    if args.command == "init":
        forgotten = prune()
        print(f"{path}: {Item.select().count()} lead(s), {queued()} queued, "
              f"{Seen.select().count()} seen row(s)"
              + (f", {forgotten} old one(s) forgotten" if forgotten else ""))
        return 0

    status.open_board(f"itempipe {args.command}", tally)

    async def tick(every=1.0):
        """Keep the tally, the clock and the spinners current while workers run."""
        while True:
            status.refresh()
            await asyncio.sleep(every)

    async def go():
        poller = asyncio.create_task(tick())
        try:
            if args.command == "search":
                return await search(args.brief, workers=args.n)
            return await apply(workers=args.n)
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)

    try:
        reports = asyncio.run(go())     # a KeyboardInterrupt still restores the tty
    except Startup as exc:              # no browser: a sentence, not a traceback
        print(f"itempipe: {exc}", file=sys.stderr)
        return 1
    finally:
        status.close_board()
    print("\n---\n".join(reports))
    return 0
