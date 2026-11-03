"""Search leads, apply to them: two agents, N workers each, one SQLite queue.

The claim is a compare-and-swap (read the next free row, then UPDATE it guarded by the token
it had), so the database is the lock. Every later write is fenced by that token, and
`submitting` is set before the irreversible step, so a crash goes to review, not a retry.

Search works the same way one level up. A *session* is one conversation that records one
lead and then ends, so no worker ever drags a 60-step transcript around. What outlives it
is the `seen` table: every URL and query a worker has looked at and how it turned out. A
clean session therefore starts blind to the conversation but not blind to the work.

`search` does not finish. Workers are interchangeable: each takes a session's work off a
shared queue — the configured boards first, then queries the config describes — and `seen`
keeps any of it from being done twice. Stop it with Ctrl-C; what it found so far is
printed. What to look for, and how widely, is `search.toml`; this module has no opinion
about stacks, countries or companies.
"""

import argparse, asyncio, contextlib, os, re, sys, tomllib, uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.tools import StructuredTool, tool
from peewee import (BooleanField, CharField, DateTimeField, IntegerField, IntegrityError,
                    Model, SqliteDatabase, TextField, fn)

try:                    # the fuzzy finder the questions file is searched with
    from rapidfuzz import fuzz
except ImportError:     # still usable without it, just less forgiving
    fuzz = None

from . import status

ROOT = Path(__file__).resolve().parents[2]
NEW, WORKING, ACTIVE, REVIEW = "NEW", "WORKING", "ACTIVE", "NEEDS_REVIEW"
HOLD = "HOLD"                       # waiting for the user to answer a question
FENCED = "This lead is no longer yours; stop."
LEASE = timedelta(minutes=30)
RENEW = max(1.0, LEASE.total_seconds() / 3)   # heartbeat: never let a live claim expire
RETRY = timedelta(days=7)           # a dead posting may be a bad deploy: look once more
RETENTION = timedelta(days=int(os.environ.get("ITEMPIPE_SEEN_DAYS", "365")))
DB = Path(os.environ.get("ITEMPIPE_DB", ROOT / "py/itempipe.db"))
PROMPTS, CV = Path(__file__).with_name("prompts"), ROOT / "cv.typ"
SCHEMA = Path(__file__).with_name("schema.sql")     # the whole schema; `init` executes it
QUESTIONS = Path(os.environ.get("ITEMPIPE_QUESTIONS", ROOT / "application-questions.org"))
CONFIG = Path(os.environ.get("ITEMPIPE_CONFIG") or Path(__file__).with_name("search.toml"))
PROFILE = Path(os.environ.get("ITEMPIPE_PROFILE", "/tmp/itempipe-chromium"))
# Only used when set explicitly: playwright-mcp's own default browser is what works here.
CHROME = os.environ.get("ITEMPIPE_CHROME") or ""
MCP_LOG = Path(os.environ.get("ITEMPIPE_MCP_LOG", "/tmp/itempipe-mcp.log"))
MCP_OUTPUT = int(os.environ.get("ITEMPIPE_MCP_OUTPUT", "20000"))  # chars kept per tool call
SEARCH_TRIES = int(os.environ.get("ITEMPIPE_SEARCH_TRIES", "3"))
SESSIONS = int(os.environ.get("ITEMPIPE_SESSIONS", "0"))        # 0 = until you stop it
STEPS = int(os.environ.get("ITEMPIPE_SESSION_STEPS", "26"))     # graph steps per session
APPLY_STEPS = int(os.environ.get("ITEMPIPE_APPLY_STEPS", "120"))  # graph steps per application
BUDGET = int(os.environ.get("ITEMPIPE_TOOL_BUDGET", "12"))      # tool calls per session
SEEDS = int(os.environ.get("ITEMPIPE_BOARD_SEEDS", "2"))        # board tasks per session
PAUSE = float(os.environ.get("ITEMPIPE_PAUSE", "2"))            # breather between sessions
IDLE = float(os.environ.get("ITEMPIPE_IDLE", "300"))            # wait when no query is left
BOARD_TTL = timedelta(days=float(os.environ.get("ITEMPIPE_BOARD_DAYS", "3")))
QUERY_TTL = timedelta(days=float(os.environ.get("ITEMPIPE_QUERY_DAYS", "30")))
KEYWORDS = 2        # words of a form question searched on their own, not the sentence
NEAR = 60           # `lookup_answer` shows headings at least this close (0..100)


@dataclass(frozen=True)
class Task:
    """One unit of queued work: a board to read, or a query to run."""
    kind: str       # board | query
    value: str      # the URL or the query text
    label: str      # where it came from, for the brief


# A `seen` verdict: what one check of one URL or query came to.
CLAIMED, OPEN, DEAD = "claimed", "open", "dead"           # a URL
BOARD = "board"                                          # a URL that is a board, not a posting
SEARCHED, NOQUERY = "searched", "noresults"              # a query
SKIPPED, DUPLICATE, RECORDED = "skipped", "duplicate", "recorded"
QUERY = "query"


def interleave(*groups):
    """Round-robin several tuples into one, so adjacent entries differ in origin.

    Used for regions and cities: a worker walking the result never sees one country twice
    in a row, so it cannot drift into one local market because it started there.
    """
    groups = [tuple(group) for group in groups if group]
    out, i = [], 0
    while any(i < len(group) for group in groups):
        out += [group[i] for group in groups if i < len(group)]
        i += 1
    return tuple(out)


def expand(url, variables):
    """Fill {name} in a board URL from the config's [vars]; leave it alone if it cannot."""
    try:
        return url.format(**variables)
    except (KeyError, IndexError, ValueError):
        return url


@dataclass(frozen=True)
class SearchConfig:
    """What to look for and where: all of it comes from a TOML file.

    Nothing about the job market is in this module — no stack, no country, no site. With
    no config the search is simply empty, and `itempipe search` says so instead of
    inventing a market to look in.
    """
    roles: tuple = ()
    seniority: tuple = ("",)
    domains: tuple = ()
    places: tuple = ()
    cities: tuple = ()
    sites: tuple = ()
    phrases: tuple = ()
    contracts: tuple = ()
    boards: tuple = ()
    notes: str = ""


def load_config(path=None):
    """Read the search config; a missing or broken file leaves an empty one and says why."""
    path = Path(path or CONFIG)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"itempipe: no search config at {path}. Nothing to look for; see the "
              "shipped search.toml for the shape.", file=sys.stderr)
        return SearchConfig()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"itempipe: cannot read {path}: {exc}", file=sys.stderr)
        return SearchConfig()
    search, variables = raw.get("search", {}), raw.get("vars", {})
    boards = [Task(BOARD, expand(url, variables), board.get("label", "board"))
              for board in raw.get("boards", []) for url in board.get("urls", [])]
    return SearchConfig(
        roles=tuple(search.get("roles", ())),
        seniority=tuple(search.get("seniority", ("",))),
        domains=tuple(search.get("domains", ())),
        places=interleave(*search.get("regions", {}).values()),
        cities=interleave(*search.get("cities", {}).values()),
        sites=tuple(search.get("sites", ())),
        phrases=tuple(search.get("phrases", ())),
        contracts=tuple(search.get("contract_phrases", ())),
        boards=tuple(boards),
        notes=" ".join(search.get("notes", "").split()),
    )


CFG = load_config()
ROLES, SENIORITY = CFG.roles, CFG.seniority or ("",)
DOMAINS = CFG.domains
PLACES, CITIES, SITES = CFG.places, CFG.cities, CFG.sites
PHRASES, CONTRACT = CFG.phrases, CFG.contracts
NOTES, SEED_TASKS = CFG.notes, CFG.boards

CANNED = re.compile(r"need more steps to process|sorry, i (?:can'?t|cannot)", re.I)

APPLY = ("Claim one lead with claim_next_item, look answers up with lookup_answer before "
         "filling a form, save anything it cannot answer with save_question, work the lead "
         "to a final state with finish_item, then stop. If a tool says the lead is no "
         "longer yours, stop immediately. You share one browser with the other workers: "
         "work only in the tab your own tools are on, and never switch, open or close tabs.")

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
    url = CharField()                       # unique by item_url_key in schema.sql
    notes = TextField(null=True)
    state = CharField(default=NEW)
    submitting = BooleanField(default=False)
    advanced_on = DateTimeField(null=True)
    archived_at = DateTimeField(null=True)
    archive_reason = CharField(null=True)
    claim_token = CharField(null=True)
    claimed_until = DateTimeField(null=True)

    class Meta:
        database = db                       # the DDL itself is itempipe/schema.sql;
                                            # `unique=True` here is documentation only


class Seen(Model):
    """Where the workers have been: one row per page or query, shared by every session.

    Not a lead — nothing claims or applies to a `seen` row. Its job is to stop the tenth
    worker opening the page the first one already read, and to survive the session that
    read it.
    """
    key = CharField(unique=True)            # normalised URL or query: the dedupe key
    kind = CharField(default="url")         # url | query | board
    url = TextField(null=True)              # as first written, for the brief
    verdict = CharField(default=CLAIMED)
    note = CharField(null=True)             # HTTP status, hit count, or the company
    label = CharField(null=True)            # which task queue entry wrote it
    session = CharField(null=True)          # the session that last wrote the verdict
    checked_at = DateTimeField(default=now)

    class Meta:
        database = db


class Hold(Model):
    """One question a held lead is waiting on: the link, never the question.

    The question itself lives in `application-questions.org` and nowhere else; this table
    only says which of those headings a waiting lead needs answered, so one lead can wait
    on several, and two leads can wait on the same one. It is bookkeeping, not content:
    the file stays the single place a question is written or read.
    """
    item = IntegerField()                   # the lead, by id
    key = CharField()                       # question_key() of the heading

    class Meta:
        database = db
        indexes = ((('item', 'key'), True),)


def truthy(value):
    """A switch read from text: unset, 0, false, no and off all mean no."""
    return str(value or "").strip().lower() not in ("", "0", "false", "no", "off")


def flag(name):
    """An env var read as a switch (see `truthy`)."""
    return truthy(os.environ.get(name))


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def open_db(path=DB):
    """Point peewee at `path` and connect. It creates no tables and alters nothing.

    The schema is `itempipe/schema.sql`, executed by `init_db` and by nothing else, so a
    missing or stale database is not patched up on the way in: the first query fails, and
    that is the point. A schema change is an edit to that one file plus a re-`init`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not db.is_closed():
        db.close()
    db.init(str(path))
    db.connect()
    return path


def init_db(path=DB):
    """`itempipe init`: execute `schema.sql` as written, then hand back the path.

    One file, one pass, no conditionals — no statement text lives in this module and the
    file remembers no earlier shape of the database. Run it against a database that
    already holds the tables and SQLite says "table item already exists": delete the file
    and init again. Nothing here tries to be clever about that.
    """
    path = open_db(path)
    sql = SCHEMA.read_text(encoding="utf-8")       # read whole: executescript splits it
    db.connection().executescript(sql)              # sqlite3 does the splitting, not us
    return path


def queued():
    return Item.select().where(Item.state == NEW, Item.archived_at.is_null()).count()


def free():
    """Unclaimed, or claimed and abandoned long enough ago to be retryable."""
    return Item.select().where(Item.archived_at.is_null(),
                               (Item.state == NEW) |
                               ((Item.state == WORKING) & (Item.claimed_until < now())))


def claimable():
    """Leads a worker could claim right now, including expired but unfinished claims.

    `queued()` is only the NEW rows. Using it as the apply loop's stop condition would
    leave a crashed worker's expired claims invisible, so `apply` would exit without
    ever giving another worker the chance to pick them up.
    """
    return free().count()


def tally():
    """Counts per state for the board's footer; archived leads are not counted."""
    counts = {state: 0 for state in (NEW, WORKING, ACTIVE, HOLD, REVIEW)}
    rows = (Item.select(Item.state, fn.COUNT(Item.id).alias("n"))
            .where(Item.archived_at.is_null()).group_by(Item.state).tuples())
    for state, count in rows:
        counts[state] = count
    return counts


open_db()   # connects so `import itempipe` is usable; no DDL. The CLI re-points it at
            # --db, and `init` is the only thing that executes schema.sql


@dataclass
class Worker:
    """One claim: the token stored in the row is the only authority to write it.

    An apply conversation always holds at most one claim. If the model calls
    `claim_next_item` a second time, it gets the lead it already has rather than a
    second row: two tabs may never work on two claims from one agent, and one worker
    can never accidentally leave its first lead claimed while moving on to another.
    """
    item: Item | None = None

    def claim(self):
        # A submit that outlived its lease may have gone through: review it, never retry it.
        (Item.update(state=REVIEW, submitting=False, claim_token=None, claimed_until=None)
         .where(Item.state == WORKING, Item.submitting == True, Item.claimed_until < now())
         .execute())
        if self.item is not None:
            mine = self.mine()
            if mine is not None:
                return (f"Already claimed: {mine.name} <{mine.url}>. Finish this lead "
                        "before claiming another; one worker applies to one lead at a time.")
            self.item = None        # the old claim was lost or completed elsewhere
        token = uuid.uuid4().hex
        for _ in range(24):         # compare-and-swap; losing the race means looking again
            row = free().order_by(Item.id).first()
            if row is None:
                return "No leads left. Stop."
            token_guard = (Item.claim_token.is_null() if row.claim_token is None
                           else Item.claim_token == row.claim_token)
            won = (Item.update(state=WORKING, claim_token=token, claimed_until=now() + LEASE)
                   .where(Item.id == row.id, Item.state == row.state, token_guard).execute())
            if won:
                self.item = Item.get(Item.claim_token == token)
                return (f"Id: {self.item.id}\nName: {self.item.name}\n"
                        f"Title: {self.item.title}\nUrl: {self.item.url}\n"
                        f"Notes: {self.item.notes or '-'}")
        return "Too much contention. Stop."

    def renew(self):
        """Push the lease out while this worker is still alive.

        A form can take longer than one LEASE. Without a heartbeat the row becomes
        claimable again half-way through, and two workers can submit the same job.
        Renewal is fenced by the token, so a worker that lost its claim cannot revive it.
        """
        if self.item is None:
            return False
        return (Item.update(claimed_until=now() + LEASE)
                .where(Item.id == self.item.id, Item.state == WORKING,
                       Item.claim_token == self.item.claim_token).execute()) == 1

    def mine(self):
        if self.item is None:
            return None
        if not self.renew():
            self.item = None
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
                "retry straight away. Use fetch_page on a URL you already have, or run a "
                "different query, and leave this one for later.")
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

    `claimed` is a lease, and a session may always continue what it claimed itself. After
    that it depends on what the row is: a board gains postings, so it comes back after
    BOARD_TTL; a query comes back after QUERY_TTL because the results have moved on; a
    dead posting gets one more look after a week, since a 404 can be a bad deploy; a
    recorded lead never comes back.
    """
    if row.verdict == RECORDED:
        return False
    if row.verdict == CLAIMED:
        return row.session == session or row.checked_at < now() - LEASE
    if row.session == session:
        return False
    if row.kind == BOARD:
        return row.checked_at < now() - BOARD_TTL
    if row.kind == QUERY:
        return row.checked_at < now() - QUERY_TTL
    if row.verdict == DEAD:
        return row.checked_at < now() - RETRY
    return True


def claim_seen(key, kind="url", url=None, label=None, session=None):
    """Try to be the one who checks `key`. Returns (won, row).

    won=True means go ahead: nobody holds it, or the holder's verdict has gone stale.
    won=False means the row is settled (or another worker is on it) and `row` says how.
    """
    for _ in range(2):
        try:
            Seen.create(key=key, kind=kind, url=clean(url) or None, label=label,
                        session=session)
            return True, None
        except IntegrityError:
            row = Seen.get_or_none(Seen.key == key)
            if row is None:                 # deleted under us: try to create it again
                continue
            if not retryable(row, session):
                return False, row
            if (Seen.update(verdict=CLAIMED, note=None, checked_at=now(), session=session,
                            label=label)
                    .where(Seen.key == key, Seen.verdict == row.verdict).execute()):
                return True, None
            return False, Seen.get_or_none(Seen.key == key)
    return False, None


def settle(key, verdict, note=None, session=None, kind="url", url=None, label=None):
    """Write what the check learned, so a later session reads it instead of retrying."""
    fields = dict(verdict=verdict, note=clean(note)[:250] or None, session=session,
                  checked_at=now())
    if Seen.update(**fields).where(Seen.key == key).execute():
        return True
    try:                # create_item may be the first thing to touch this URL
        Seen.create(key=key, kind=kind, url=clean(url) or None, label=label, **fields)
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


def recent_rows(k=10):
    """What the run has settled lately, newest first: the clean session's memory."""
    return Seen.select().where(Seen.kind != QUERY).order_by(Seen.checked_at.desc()).limit(k)


def query_space():
    """Every query this config would ever make, in a fixed order.

    Built entirely from the config — roles × seniority × places × phrasing, industries
    crossed with every role, the sites small companies post on, and city by city. This is
    what keeps a run going long after the queued boards are read, without the module
    knowing what a job is.
    """
    for level in SENIORITY:
        for role in ROLES:
            for place in PLACES:
                for tail in PHRASES:
                    yield f"{level}{role} {place} {tail}"
                for tail in CONTRACT:
                    yield f"{level}{role} {place} {tail}"
    for domain in DOMAINS:
        for role in ROLES:
            for place in PLACES:
                for tail in PHRASES:
                    yield f"{role} {domain} {place} {tail}"
    for host in SITES:
        for level in SENIORITY:
            for role in ROLES:
                yield f"site:{host} {level}{role} remote"
        for place in PLACES:
            for role in ROLES:
                yield f"site:{host} {role} {place}"
    for role in ROLES:
        for city in CITIES:
            yield f'"{role}" "{city}" careers'


@lru_cache(maxsize=None)
def space_for():
    return tuple(query_space())


def next_tasks(sess, boards=SEEDS, queries=3):
    """Claim this session's work from the shared queue.

    Board tasks first, then queries nobody has run inside QUERY_TTL. Claiming is the same
    compare-and-swap as everywhere else, so two workers planning at the same moment cannot
    come away with the same entry, and a worker is never tied to one board or one region.
    """
    tasks = []
    for task in SEED_TASKS:
        if sum(t.kind == BOARD for t in tasks) >= boards:
            break
        won, _ = claim_seen(url_key(task.value), kind=BOARD, url=task.value,
                            label=task.label, session=sess.token)
        if won:
            tasks.append(task)
    settled = {row.key for row in
               Seen.select(Seen.key, Seen.kind, Seen.verdict, Seen.session, Seen.checked_at)
               .where(Seen.kind == QUERY) if not retryable(row, sess.token)}
    wanted = max(1, queries - len(tasks))
    for query in space_for():
        if wanted == 0:
            break
        if query_key(query) in settled:
            continue
        won, _ = claim_seen(query_key(query), kind=QUERY, url=query, label=sess.tag,
                            session=sess.token)
        if won:
            tasks.append(Task(QUERY, query, "search"))
            wanted -= 1
    return tasks


def reconcile(sess):
    """Hand back what the session claimed and never resolved, and settle what it read.

    A task claimed at plan time and never touched is released outright — the row is
    deleted, so the next session can have it — and a page it opened without recording was
    judged, so it becomes SKIPPED and comes back on that kind's own schedule.
    """
    released = (Seen.delete().where(Seen.session == sess.token,
                                    Seen.verdict == CLAIMED).execute())
    settled = (Seen.update(verdict=SKIPPED, note="not recorded", checked_at=now())
               .where(Seen.session == sess.token, Seen.verdict == OPEN).execute())
    return released + settled


def prune(older=RETENTION):
    """Forget browsing memory older than `older`. Leads are never touched."""
    return Seen.delete().where(Seen.checked_at < now() - older).execute()


def remember_leads():
    """Say in `seen` what the tracker already knows.

    Without this, every existing lead is a posting waiting to be re-found, verified and
    refused as a duplicate — the most expensive way there is to learn nothing.
    """
    known = {row.key for row in Seen.select(Seen.key)}
    made = 0
    for name, url in Item.select(Item.name, Item.url).tuples():
        key = url_key(url)
        if key in known:
            continue
        known.add(key)
        try:
            Seen.create(key=key, kind="url", url=url, verdict=RECORDED, note=name)
            made += 1
        except IntegrityError:
            pass
    return made


# -- the questions file ------------------------------------------------------
#
# `application-questions.org` is the answer store, and it belongs to the human: one heading
# per question, the answer in an `:ANSWER:` property, or `:DECLINE: t` where the answer is
# "I will not say". Nothing about a question is stored on a lead — the file is plain Q/A,
# one answer serving every form that asks it — so a lead can wait on several of them at
# once. `lookup_answer` reads the file; `save_question` and `unhold` write to it, which
# makes it both the memory and the inbox.

QUESTIONS_HEAD = """#+TITLE: Application Questions and Answers
#+STARTUP: indent

# One entry per question, with the answer in an :ANSWER: property.
# Questions are phrased generically (<Company>, <N> days, ...) so the same
# answer serves every application. :SOURCE: lists the forms that asked it.
# :DECLINE: t instead of an answer means you will not answer that field: the
# agent leaves it blank and never invents a value, and no lead is held for it.
# :ALIASES: lists the other wordings one heading answers to (relocating, move,
# Portugal), so a short general heading covers every form that asks the field.
# A heading with neither an :ANSWER: nor a :DECLINE: is still open: `itempipe
# apply` appends it when a form asks what this file cannot answer, and holds.
#
# Questions are matched by meaning, not by wording: word order, filler words and
# parenthetical asides do not matter, so one short heading ("Postal address")
# answers every form that asks about it. `itempipe questions` lists the open
# ones as file:line, and `itempipe questions postal` finds a heading by words.
"""

HEADING = re.compile(r"^(\*+)\s+(.*?)\s*$")
PROPERTY = re.compile(r"^\s*:([A-Za-z][\w@#%+-]*):\s*(.*?)\s*$")
# Words that make a question a question rather than name its subject. Dropped before two
# questions are compared, so "Are you open to relocating?" and "Relocating?" agree.
STOPWORDS = frozenset("""a able about an and any are as at available based be been by
    can consider considering could current currently do does enter for from has have how
    i if in is it its me my of on open or please prefer preferred provide should specify
    tell that the their them this to us was we were what when where which who why will
    willing with would you your yours""".split())
# The subset `question_key` folds with, frozen: the key is a hold's link to a heading, so
# it must keep meaning what it meant when the hold was written. Widening STOPWORDS above
# changes matching; it must not change a key.
KEY_STOPWORDS = frozenset("""a an and any are as at be been by can could do does for from
    has have how i if in is it its me my of on or please should tell that the their
    them this to us was we were what when where which who why will with would you your
    yours""".split())


@dataclass(frozen=True)
class Question:
    """One heading of the questions file: what was asked, and the user's answer to it.

    `decline` is the user's refusal to answer at all (`:DECLINE: t`): it settles the
    question without supplying a value, so nothing is invented and no lead waits on it.
    `aliases` are the other wordings this one heading answers to (`:ALIASES:`), so a short
    generic heading covers every form that asks the field its own way.
    """
    title: str
    answer: str = ""
    decline: bool = False
    note: str = ""
    source: str = ""
    aliases: str = ""
    level: int = 2
    line: int = 0              # where the heading is, so a report can point at it

    @property
    def settled(self):
        """Answered, or deliberately declined: either way the user has decided."""
        return bool(self.answer or self.decline)

    @property
    def key(self):
        return question_key(self.title)


NOISE = re.compile(r"\([^)]*\)|\[[^\]]*\]|https?://\S+")


def tokens_of(text):
    """The words that carry the meaning of a question.

    Lowercased, stopwords gone, and the paperwork dropped: parenthetical asides ("all
    marked required on the … application form"), bracketed links and bare numbers are
    provenance, not question. Without that, one address field saved from ten forms looks
    like ten different questions instead of one.
    """
    text = NOISE.sub(" ", str(text or "").lower())
    return [token for token in re.findall(r"[a-z][a-z0-9]*", text)
            if token not in STOPWORDS]


def question_key(text):
    """One heading, one identity: case, punctuation, stopwords and spacing folded away.

    Kept apart from `tokens_of` on purpose, and folded with the frozen KEY_STOPWORDS: the
    key is a hold's link to a heading, so it must keep meaning what it meant when the
    hold was written — a `hold` table written by an older version still points here.
    """
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return " ".join(word for word in words if word not in KEY_STOPWORDS)


def questions(path=None):
    """Every question in the file, in file order: answered, declined and open alike.

    A heading is a question; a `*` section title is not, so it is left out. Org is read
    by hand rather than by a parser because only two things matter here: the heading, and
    the drawer under it — whatever else the entry holds is the user's business.
    """
    try:
        text = Path(path or QUESTIONS).read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if match := HEADING.match(line):
            found.append(Question(clean(match.group(2)), line=number,
                                  level=len(match.group(1))))
            continue
        if not found or not (match := PROPERTY.match(line)):
            continue
        name, value = match.group(1).upper(), match.group(2)
        if not value or name in ("PROPERTIES", "END"):
            continue
        entry = found[-1]
        if name == "ANSWER":
            found[-1] = replace(entry, answer=value)
        elif name == "DECLINE":
            found[-1] = replace(entry, decline=truthy(value))
        elif name == "ALIASES":           # other wordings this heading answers to
            found[-1] = replace(entry, aliases=f"{entry.aliases} {value}".strip())
        elif name in ("NOTE", "FORMAT"):
            note = f"{entry.note}; {value}" if entry.note else value
            found[-1] = replace(entry, note=note)
        elif name == "SOURCE":
            found[-1] = replace(entry, source=value)
    return [entry for entry in found if entry.level > 1]      # a `*` heading is a section


def similarity(a, b):
    """How alike two questions are, 0..100, by words rather than by string.

    rapidfuzz's token-set ratio compares the two sets of words, so word order, filler and
    the words only one side has are forgiven, and a heading whose words are all in the
    question scores 100 however long the question is. That is what lets one short heading
    answer every form that words the field its own way. Without rapidfuzz the same idea
    is computed as containment, so the reader keeps working on a bare python.
    """
    words_a, words_b = tokens_of(a), tokens_of(b)
    if not words_a or not words_b:
        return 0.0
    if fuzz is not None:
        return float(fuzz.token_set_ratio(" ".join(words_a), " ".join(words_b)))
    return 100.0 * len(set(words_a) & set(words_b)) / min(len(set(words_a)),
                                                          len(set(words_b)))


def keywords(text, n=KEYWORDS):
    """The n words a question is really about: rare in the file first, longest next.

    A form asks "What is your full postal address - Address Line 1, City, State/Province
    and Zip or Postal Code?". Searching that sentence finds nothing; searching its two
    most telling words finds the heading that already answers it. Rarity is a crude
    inverse document frequency over the headings in the file, so words this file uses
    everywhere (address, experience) give way to the ones that say which question it is.
    """
    words = tokens_of(text)
    if len(words) <= n:
        return words
    titles = [entry.title.lower() for entry in questions()]
    counts = {word: sum(1 for title in titles if word in title) for word in words}
    ranked = sorted(enumerate(words),
                    key=lambda pair: (counts[pair[1]], -len(pair[1]), pair[0]))
    return [word for _, word in ranked[:n]]


def words_of(item):
    """The words a question answers to: its own, plus the `:ALIASES:` it lists.

    Takes a `Question` or a plain string. This is what makes one short heading able to
    cover the dozen wordings a dozen forms use for the same field — the synonyms live in
    the file, next to the heading, not in the Python.
    """
    if isinstance(item, Question):
        return set(tokens_of(item.title)) | set(tokens_of(item.aliases))
    return set(tokens_of(item))


def closest(query="", limit=3, entries=None, min_score=NEAR):
    """The questions nearest in meaning to `query`, best first.

    The fuzzy finder on its own already ignores word order and extra words; the two
    keywords are tried as a second query because a form's long sentence otherwise drowns
    them. Among equally close headings, the one sharing more of the question's words wins,
    and a declined heading wins a tie: "I will not answer that" is the decision the agent
    must not talk itself out of.
    """
    rows = list(entries if entries is not None else questions())
    words = tokens_of(query)
    if not rows or not words:
        return rows[:limit]
    needles = [" ".join(words), " ".join(keywords(query))]
    asked = set(words)
    ranked = []
    for entry in rows:
        known = words_of(entry)
        score = max(similarity(needle, " ".join(sorted(known))) for needle in needles)
        if score >= min_score:
            shared = len(asked & known)
            ranked.append((score, shared, entry.decline, entry))
    ranked.sort(key=lambda hit: hit[:3], reverse=True)
    return [hit[-1] for hit in ranked[:limit]]


def same_question(a, b):
    """Is one of these two questions just a shorter way of asking the other?

    Deliberately strict: every meaningful word of one has to appear in the other, once
    parentheticals, numbers and the filler words are gone. That keeps "experience with
    Go" and "experience with Node.js" apart, and "current salary" away from "salary
    expectation". It is the predicate that decides whether a question is saved twice, so
    a false yes either asks the user twice or answers a field they never answered.

    A short heading is therefore the safe shape: what it says is contained in any form's
    longer wording of the same field, while a heading that carries one form's paperwork
    matches nothing but that form. `:ALIASES:` adds the synonyms it needs — one word per
    wording, `relocating` as well as `relocation`.
    """
    words_a, words_b = words_of(a), words_of(b)
    return bool(words_a and words_b) and (words_a <= words_b or words_b <= words_a)


def answered(query="", limit=3):
    """The settled questions nearest in meaning to `query`, best first.

    Declined entries are included: "the user will not answer this" is a decision the
    agent has to obey, and showing it is what stops that field being asked about again.
    A heading the user has not decided on never matches.
    """
    return closest(query, limit, entries=[entry for entry in questions() if entry.settled])


def best_answer(query):
    """The one settled entry that is the same question as `query`, if there is one.

    This is what refuses a hold, so it asks `same_question` rather than settling for the
    closest heading (`lookup_answer` shows those): a question is only taken as answered
    when the file's own wording covers every word of it.

    Where several headings qualify, the one whose *title* the question spells out wins
    (an alias-only match is the weakest), then an answer over a refusal, then the more
    specific heading. So a form asking just "City" gets the saved city, not the address
    refusal that happens to list `city` among its aliases.
    """
    words = set(tokens_of(query))
    if not words:
        return None

    def rank(entry):
        return (set(tokens_of(entry.title)) <= words,      # the heading, spelled out
                not entry.decline,                          # an answer beats a refusal
                len(words_of(entry)))                       # the more specific one

    found = [entry for entry in questions()
             if entry.settled and same_question(query, entry)]
    return max(found, key=rank) if found else None


def repeats():
    """Groups of headings that ask the same thing, so a pile of variants can be merged.

    Read-only: `save_question` only ever appends, so merging is the user's edit. Until
    then every variant already answers to the others, because matching is by the words of
    a question and not by its wording.
    """
    groups = []
    for entry in questions():
        for group in groups:
            if any(same_question(entry, other) for other in group):
                group.append(entry)
                break
        else:
            groups.append([entry])
    return [group for group in groups if len(group) > 1]


def declined_note(entry):
    """What the agent is told when the user has refused a field."""
    fallback = (f" Where a form insists on text, {entry.answer!r} is the value the user "
                "allows." if entry.answer else "")
    return (f"Declined in {QUESTIONS.name} — {entry.title!r} — the user will not answer "
            "this: do not ask again and never invent a value; leave the field blank."
            f"{fallback} If the form will not submit without it, finish_item(\"archive\", "
            "\"required field declined\").")


def answer_text(query="", limit=3):
    """What `lookup_answer` hands the model: the three nearest questions, best first.

    A declined entry is reported as DECLINED rather than as a value: the field is left
    blank (or given the quoted fallback where the form insists), never invented.
    """
    rows = answered(query, limit)
    if rows:
        blocks = []
        for number, entry in enumerate(rows, 1):
            if entry.decline:
                block = (f"{number}. {entry.title}\n   Answer: DECLINED — the user will not "
                         "answer this. Never invent a value; leave the field blank")
                block += (f", or enter {entry.answer!r} where the form insists."
                          if entry.answer else ".")
            else:
                block = f"{number}. {entry.title}\n   Answer: {entry.answer}"
            if entry.note:
                block += f"\n   Note: {entry.note}"
            blocks.append(block)
        return "\n\n".join(blocks)
    hint = ('Do not invent one: call save_question with the form\'s question verbatim, '
            'then finish_item("hold") so the lead waits for the user instead of being '
            "abandoned.")
    if not any(entry.settled for entry in questions()):
        return f"No stored answers yet. {hint}"
    return f"No stored answer matches {clean(query)!r}. {hint}"


def open_questions():
    """The headings the user has neither answered nor declined: the to-do list."""
    return [entry for entry in questions() if not entry.settled]


def field_label(question):
    """The form's own name for the field: the question without its paperwork.

    A form writes "Address Line 1: / City: / State/Province: / Zip/Postal Code: (all
    marked required on the Dynatrace SAP SuccessFactors application form)". The
    parenthetical is provenance, and `:SOURCE:` already holds it, so the heading keeps the
    part that names the field. That is what stops the tenth address field from becoming
    the tenth address heading.
    """
    text = re.sub(r"\s*\([^)]*\)", " ", clean(question)).strip(" :;-–—/")
    return clean(text) or clean(question)


def entry_text(question, source=""):
    """One entry, in exactly the shape the file already uses.

    The same heading/drawer layout as the answers a human writes, with three-space
    indentation, an empty `:ANSWER:` to fill in, and the form that asked it in `:SOURCE:`:

        ** What is your notice period?
           :PROPERTIES:
           :ANSWER:
           :SOURCE: Acme (Senior Java Engineer)
           :END:
    """
    lines = [f"** {clean(question)}", "   :PROPERTIES:", "   :ANSWER:"]
    if clean(source):
        lines.append(f"   :SOURCE: {clean(source)}")
    lines.append("   :END:")
    return "\n".join(lines) + "\n"


def ask(question, source=""):
    """Append one unanswered question to the file. Returns (entry, was_new).

    The file is only ever appended to — never rewritten — so nothing already in it can be
    lost, and two workers adding questions at once cannot clobber each other. A question
    the file already asks is returned as it stands rather than appended again: the same
    wording, a longer or shorter one (`same_question`), or a heading the user has already
    answered or declined. That is what stops a form from growing a second Address Line 1.
    """
    question, source = clean(question), clean(source)
    for entry in questions():
        if same_question(question, entry):
            return entry, False
    label = field_label(question)
    path = Path(QUESTIONS)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8") as out:      # 'a' is the whole safety story
        if fresh:
            out.write(QUESTIONS_HEAD)
        out.write("\n" + entry_text(label, source))
        out.flush()
    return Question(label, source=source), True


def hold(item, keys):
    """Record that a lead is waiting on these question keys."""
    for key in keys:
        with contextlib.suppress(IntegrityError):
            Hold.create(item=item, key=key)


def drop_holds(ident=None):
    """Forget a lead's waits, or every wait, once they no longer apply."""
    query = Hold.delete()
    if ident is not None:
        query = query.where(Hold.item == ident)
    return query.execute()


def waiting(ident):
    """The questions a held lead still needs answered: open in the file, and its own."""
    open_now = {entry.key: entry for entry in open_questions()}
    keys = [row.key for row in Hold.select(Hold.key).where(Hold.item == ident)]
    return [open_now[key] for key in keys if key in open_now]


def unhold():
    """Release every held lead that no longer has an open question waiting on it.

    Answering a heading releases exactly the leads that were held for it, and a lead held
    on two questions waits until both are answered. Called at startup: hold, you answer,
    run again.
    """
    back = 0
    for item in list(Item.select().where(Item.state == HOLD)):
        if waiting(item.id):
            continue
        drop_holds(item.id)
        back += (Item.update(state=NEW)
                 .where(Item.id == item.id, Item.state == HOLD).execute())
    return back


@dataclass
class Session:
    """One search conversation.

    All of its memory is in `seen`; this is only the bookkeeping it needs while it runs:
    which worker it belongs to, what it recorded, and how much of its budget it has spent.
    """
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


def field_of(message, name, default=None):
    """Read one field off a message that may be a LangChain object or a plain dict."""
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def text_of(content):
    """Message content as one string: a tool result may be a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = (block if isinstance(block, str) else str(field_of(block, "text", "") or "")
                 for block in content)
        return "\n".join(part for part in parts if part)
    return str(content or "")


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
    sess = sess or Session()

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
        except IntegrityError:      # the same name, or now the same URL as well
            found = (Item.get_or_none(Item.name_key == lower)
                     or Item.get_or_none(Item.url == url))
            if found is None:       # neither key matches: never blame a lead there isn't
                return "NOT recorded: the database refused it as a duplicate."
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
        won, row = claim_seen(key, kind=QUERY, url=text, label=sess.tag, session=sess.token)
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
                    settle(key, SEARCHED, f"{len(found)} hits", sess.token, kind=QUERY)
                    return hits_text(found)[:6000] + budget_note(sess)
                reason = "no results"
            if attempt + 1 < SEARCH_TRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
        settle(key, NOQUERY, reason, sess.token, kind=QUERY)
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
        row = Seen.get_or_none(Seen.key == key)
        kind = row.kind if row is not None else "url"     # boards stay boards
        won, held = claim_seen(key, kind=kind, url=url, label=sess.tag, session=sess.token)
        if not won:
            return seen_refusal(held, "fetched") + budget_note(sess)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            settle(key, OPEN, f"{type(exc).__name__}", sess.token, kind=kind)
            return (f"NOT fetched: {type(exc).__name__}: {clean(exc)}. The site may be "
                    "down or slow; take another board and come back in a later session."
                    + budget_note(sess))
        if response.status_code >= 400:
            settle(key, DEAD, f"HTTP {response.status_code}", sess.token, kind=kind)
            return (f"NOT fetched: HTTP {response.status_code} for {url}. The posting is "
                    "probably gone or behind a login, so record nothing from it."
                    + budget_note(sess))
        try:
            text = await asyncio.to_thread(trafilatura.extract, response.text)
        except Exception:               # unparseable html: hand over the raw text
            text = None
        body = "\n".join(l for l in ((text or response.text).splitlines()) if l.strip())
        settle(key, OPEN, f"HTTP {response.status_code}", sess.token, kind=kind)
        return ((body.strip()[:8000] or "Fetched, but the page has no readable text.")
                + budget_note(sess))

    @tool
    def claim_next_item() -> str:
        """Claim the next lead and return it with its URL. Call this first."""
        return w.claim()

    @tool
    def lookup_answer(query: str = "") -> str:
        """Look up the user's own answers to application questions. Call it before filling
        any required free-text or personal-detail field. Pass the two most meaningful words
        of the form's question — "salary expectation", "notice period", "postal address" —
        because the saved questions are worded differently, so keywords match where whole
        sentences do not. Use the answer as written, filling in <Company> or <N>. An entry
        marked DECLINED is the user refusing that field: leave it blank (or type the value
        it quotes), never invent one, and do not hold the lead for it."""
        return answer_text(query)

    def remember(question, item):
        """Save one unanswered question and record that this lead waits on it.

        Returns (saved, message): `saved` is False when the file already answers it, in
        which case the model is told to use that answer rather than hold the lead — and
        also when the user has declined the field, so nothing is asked twice.
        """
        question = clean(question)
        if not question_key(question):
            return False, "NOT saved: give the form's question verbatim, as it is worded."
        if len(question) > 300:      # `clean` has already folded the whitespace away
            return False, ("NOT saved: one field's question, verbatim, not a summary of "
                           "several. Call this once per field.")
        if found := best_answer(question):
            if found.decline:
                return False, declined_note(found)
            return False, (f"Already answered in {QUESTIONS.name} — {found.title}: "
                           f"{found.answer}. Use it; do not hold for this one.")
        entry, new = ask(question, f"{item.name} ({item.title})")
        if entry.decline:           # a shorter way of asking a field the user refuses
            return False, declined_note(entry)
        hold(item.id, [entry.key])
        if new:
            return True, (f"Saved: {entry.title!r} in {QUESTIONS.name}. The user answers "
                          "it once, for every application.")
        return True, (f"Already asked in {QUESTIONS.name} — {entry.title!r} (line "
                      f"{entry.line}); this lead waits on that one, and nothing is "
                      "asked twice.")

    @tool
    def save_question(question: str) -> str:
        """Save one required form question that the stored answers do not cover, so the
        user can answer it once for every application. One question per call, verbatim and
        on one line — never a summary of several fields. Then finish_item("hold") when you
        have saved them all; a lead may wait on several. Never invent an answer, and never
        archive a lead over a question the user can answer."""
        item = w.mine()
        if item is None:
            return FENCED
        return remember(question, item)[1]

    @tool
    def begin_submission() -> str:
        """Call immediately before the final submit, so a crash goes to review, not a retry."""
        item = w.mine()
        if item is None:
            return FENCED
        guarded = (Item.update(submitting=True, claimed_until=now() + LEASE)
                   .where(Item.id == item.id, Item.state == WORKING,
                          Item.claim_token == item.claim_token).execute())
        # The update is the fence. Reporting success when it changed no row would let
        # the model submit a form after another worker had already taken the lead.
        return "Submission guard set." if guarded == 1 else FENCED

    @tool
    def finish_item(outcome: str, reason: str = "") -> str:
        """End the claimed lead: "applied" (after begin_submission), "retry" (site down,
        timeout), "hold" (required fields ask what the stored answers cannot answer — save
        each one with save_question first; the lead waits for the user instead of being
        lost) or "archive" (the lead cannot be applied to at all)."""
        item = w.mine()
        if item is None:
            return FENCED
        ident = item.id
        message = f"{item.name}: {outcome} ({clean(reason) or 'no reason'})."
        if outcome == "applied":
            if not item.submitting:
                return "NOT done: call begin_submission immediately before the final submit."
            ended = w.end(state=ACTIVE, submitting=False, advanced_on=now())
        elif outcome == "retry":
            ended = w.end(state=REVIEW if item.submitting else NEW, submitting=False)
        elif outcome == "hold":
            if clean(reason):
                saved, note = remember(reason, item)
                if not saved:
                    return note          # an answer exists: finish the form instead
            open_now = waiting(ident)
            if not open_now:
                return ("NOT held: no unanswered question is saved for this lead. Call "
                        "save_question for each one the form asks, then finish_item(\"hold\").")
            if item.submitting:
                ended = w.end(state=REVIEW, submitting=False)
                if not ended:
                    return FENCED
                return (f"{item.name}: the submission had already started, so it needs "
                        f"review; its question(s) are in {QUESTIONS.name}.")
            ended = w.end(state=HOLD, submitting=False)
            message = (f"{item.name}: on hold until the user answers {len(open_now)} "
                       f"question(s) in {QUESTIONS.name}. It goes back in the queue on "
                       "the next run, once every one of them is answered.")
        elif outcome == "archive":
            ended = w.end(archived_at=now(), submitting=False,
                          archive_reason=clean(reason) or "abandoned")
        else:                   # a typo must not archive a lead
            return ('NOT done: outcome must be "applied", "retry", "hold" or "archive"; '
                    "nothing was changed and the lead is still yours.")
        if not ended:
            return FENCED
        if outcome != "hold":
            drop_holds(ident)   # a finished lead waits on nothing
        return message

    return ([claim_next_item, lookup_answer, save_question, begin_submission, finish_item]
            if w else [create_item, web_search, fetch_page])


def agent(role, tool_list):
    """One agent: LangChain 1's `create_agent`, which takes the prompt as a system message.

    `create_agent(model, tools, *, system_prompt=...)` replaces LangGraph's
    `create_react_agent`, whose `prompt=` keyword became `system_prompt=`.
    """
    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI
    model = ChatOpenAI(model=os.environ.get("ITEMPIPE_MODEL", "deepseek-chat"),
                       base_url=os.environ.get("ITEMPIPE_BASE_URL", "https://api.deepseek.com/v1"),
                       api_key=os.environ.get("DEEPSEEK_API_KEY", "missing"), timeout=180)
    prompt = (PROMPTS / f"{role}.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{{CV}}", CV.read_text()).replace("{{NOTES}}", NOTES)
    return create_agent(model, tool_list, system_prompt=prompt)


async def run(agent_, text, tag="·", finish=True, limit=60):
    """Stream the graph's node updates, so every tool call shows up while it happens.

    LangChain 1 agents stream the same shape as before — one mapping per node, holding
    that node's `messages` — but a tool result is now a list of content blocks rather
    than a bare string, so the text is flattened before it reaches the board.

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
                content = text_of(field_of(message, "content"))
                if field_of(message, "type") == "tool":
                    status.worker(tag, "result", content)
                elif calls := field_of(message, "tool_calls"):
                    made += len(calls)
                    for call in calls:
                        status.worker(tag, "call", f"{call['name']} {call['args']}")
                elif content:
                    answer = content
    if not answer.strip() or CANNED.search(answer):
        answer = (f"[no report: the model stopped without one after {made} tool call(s); "
                  "see the items in the tracker]")
    if finish:
        status.finished(tag, answer)
    return answer.strip()


def session_brief(brief, i, workers, n, tasks, recorded):
    """What one clean session is told: its tasks, and what is already settled."""
    plan = [f"- fetch {task.value} and read it for postings that fit" if task.kind == BOARD
            else f'- search "{task.value}" and open the postings it turns up'
            for task in tasks]
    source = ", ".join(sorted({t.label for t in tasks if t.kind == BOARD})) or "the search space"
    settled = "\n".join(
        f"- {row.url}{f' ({row.note})' if row.note else ''} — {row.verdict}"
        for row in recent_rows()) or "- nothing yet"
    return f"""You are search worker {i + 1} of {workers}, session {n}.

This session is one lead long: verify one posting, call create_item once, then stop and
write your report. A fresh session starts immediately after you with a clean memory, so
do not survey a whole board and do not save findings up.

Your work, taken from the shared queue ({source}):
{chr(10).join(plan)}

Already settled, by this run or an earlier one. A tool that answers "already checked" is
pointing you elsewhere, not failing:
{settled}

Recorded by this worker so far: {', '.join(recorded) or 'nothing'}

{brief}"""


async def search(brief, workers=4, sessions=SESSIONS, progress=None):
    """Search until you stop it.

    The workers are interchangeable: each takes a session's work off the shared queue
    (`next_tasks`) — the default boards first, then queries from the space nobody has run
    lately — records at most one lead, and hands what it learned to `seen`. Because `seen`
    remembers every URL and query, the queue keeps moving instead of re-finding the same
    postings, which is what lets it run for hours. Ctrl-C stops it and prints the log.
    """
    progress = progress if progress is not None else []
    remember_leads()

    async def one(i):
        tag, total, fails, n = f"w{i + 1}", [], 0, 0
        status.worker(tag, "state", "waiting for tasks", state="searching")
        while sessions <= 0 or n < sessions:
            n += 1
            sess = Session(tag=tag)
            try:
                tasks = next_tasks(sess)
                if not tasks:
                    status.worker(tag, "state", "queue empty: waiting for boards to "
                                  "refresh", state="idle")
                    await asyncio.sleep(IDLE)
                    continue
                boards = sum(task.kind == BOARD for task in tasks)
                status.worker(tag, "state", f"session {n}: {boards} board(s), "
                              f"{len(tasks) - boards} search(es)", state="searching")
                report = await run(agent("search", tools_for(sess=sess)),
                                   session_brief(brief, i, workers, n, tasks, total),
                                   tag=tag, finish=False, limit=STEPS)
                total += sess.recorded
                progress.append(f"[{tag}] session {n}: "
                                + (", ".join(sess.recorded) or
                                   (clean(report)[:160] or "nothing")))
                fails = 0 if sess.recorded else fails + 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:    # one bad session is not the end of the run
                fails += 1
                status.worker(tag, "state", f"{type(exc).__name__}: {clean(exc)}",
                              state="searching")
                await asyncio.sleep(min(60.0, PAUSE * 2 ** min(fails, 5)))
            finally:
                reconcile(sess)         # unused claims handed back, opened pages judged
            await asyncio.sleep(PAUSE)
        progress.append(f"[{tag}] stopped after {n} session(s): "
                        f"{len(total)} recorded: {', '.join(total) or 'none'}")

    results = await asyncio.gather(*(one(i) for i in range(workers)),
                                   return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            progress.append(f"[itempipe] worker crashed: {type(result).__name__}: "
                            f"{clean(result)}")
    return progress


def _tail(lines=14):
    try:
        text = MCP_LOG.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def chrome_hint(log):
    """Pick the likely cause out of the browser's own words, so the fix is obvious."""
    text = (log or "").lower()
    if "singletonlock" in text or "profile appears to be in use" in text \
            or "browser is already in use" in text:
        return (f"the profile {PROFILE} is in use by another Chromium. Close it, or set "
                "ITEMPIPE_PROFILE elsewhere.")
    if "no usable sandbox" in text or "running as root" in text:
        return "Chromium refuses its sandbox here; run it with --no-sandbox."
    if "cannot open display" in text or "missing x server" in text or "ozone" in text:
        return ("Chromium wants a display. Run under a graphical session (or through "
                "chromium-sandboxed, which binds the Wayland socket), or set "
                "ITEMPIPE_HEADLESS=1.")
    if "executable doesn't exist" in text or "executable doesn't exist" in text \
            or "is not found" in text:
        return ("Playwright cannot find a browser to launch. Run `npx @playwright/mcp "
                "install-browser chromium`, or set ITEMPIPE_CHROME=/path/to/chromium.")
    if "multiple targets are not supported" in text:
        return ("Chromium's headless mode cannot open more than one tab at startup, and "
                "every worker needs its own tab. Unset ITEMPIPE_HEADLESS to run headed.")
    if "error while loading shared libraries" in text or "bwrap:" in text:
        return "Chromium (or the sandbox wrapper) could not start: see the log."
    if "permission denied" in text:
        return f"Chromium cannot write {PROFILE}; check its owner and the sandbox binds."
    return "No recognised cause; the log below is the whole story."


def startup_message(exc):
    """What to print when no browser came up: the cause, the fix, and the log's tail."""
    detail = _tail()
    return "\n".join(filter(None, (
        f"playwright-mcp could not start a browser: {type(exc).__name__}: {clean(exc)}",
        f"  {chrome_hint(detail)}",
        f"  log: {MCP_LOG}",
        detail)))


def mcp_config():
    """The single playwright-mcp server, which owns the one browser and window.

    Exactly the fields `mcp.StdioServerParameters` takes, so it can be passed straight to
    it. This process is the *only* thing that launches a browser: a browser we launched
    ourselves and a browser it launched would be two windows, so there is only ever this
    one. The name of the binary is `playwright-mcp`.

    The environment is inherited whole because playwright-mcp needs more of it than the
    SDK's safe default (HOME, PATH): a display, a runtime dir, the sandbox binds.
    """
    args = []
    if CHROME:                  # opt-in: otherwise its own browser is used
        args += ["--executable-path", CHROME]
    if flag("ITEMPIPE_HEADLESS"):
        args.append("--headless")
    return {"command": "playwright-mcp", "args": args, "env": dict(os.environ)}


TAB_LINE = re.compile(r"^\s*-\s*(\d+):\s*(.*)$", re.M)
HIDDEN_BROWSER_TOOLS = frozenset({"browser_tabs", "browser_close"})


def result_text(result):
    """Flatten an MCP CallToolResult's content blocks into one string."""
    parts = []
    for block in field_of(result, "content") or ():
        if (text := field_of(block, "text")) is not None:
            parts.append(text_of(text))
        else:                   # a screenshot or a resource: name it, never inline it
            parts.append(f"[{field_of(block, 'type', 'other')} content]")
    return "\n".join(part for part in parts if part).strip()


def result_failed(result):
    return bool(field_of(result, "is_error") or field_of(result, "isError"))


def tab_indices(listing):
    """The tab numbers in playwright-mcp's `browser_tabs` listing."""
    return [int(match.group(1)) for match in TAB_LINE.finditer(listing or "")]


def current_tab(listing):
    """The number of the tab marked `(current)`, or None when the listing has none."""
    for match in TAB_LINE.finditer(listing or ""):
        if "(current)" in match.group(2):
            return int(match.group(1))
    return None


@dataclass
class Tab:
    """One worker's seat in the shared browser.

    `index` is mutable on purpose: a page that opens a popup, or a tab that crashes and is
    removed, moves where this worker's page sits in playwright-mcp's numbered list. The
    browser re-reads the list after each call and keeps this number pointing at the page
    the worker was actually driving.
    """
    browser: "SharedBrowser"
    index: int


def mcp_tool(tool, client, invoke=None):
    """One MCP tool as a LangChain tool that calls back through the shared session.

    The server's own JSON Schema is handed straight over as `args_schema`: LangChain takes
    a plain schema dict and gives it to the model verbatim, so there is no pydantic model
    to build and nothing that can drift from what the server declared.

    `invoke` is how a worker's tools select that worker's tab immediately before the
    call. It is passed separately so tests can still exercise the MCP adaptation without
    a browser, and so the same client can serve all workers safely.

    Like every other tool here it answers instead of raising: a tool that fails, or a
    server that has gone away, comes back as a `NOT done: …` sentence the model can act
    on, not an exception that ends the worker mid-node.
    """
    name = field_of(tool, "name")
    # `inputSchema` is what the protocol calls it; the snake_case spelling covers SDK
    # models that expose the field by its python name instead of its wire alias.
    schema = (field_of(tool, "input_schema") or field_of(tool, "inputSchema")
              or {"type": "object", "properties": {}})

    async def call(**arguments):
        try:
            result = await (invoke(name, arguments) if invoke is not None
                            else client.call_tool(name, arguments))
        except Exception as exc:        # a dead subprocess must not kill the worker
            return f"NOT done: {type(exc).__name__}: {clean(exc)}. The browser may be gone."
        text = result_text(result)
        if result_failed(result):
            return f"NOT done: {text or 'the tool reported an error without saying why.'}"
        if len(text) > MCP_OUTPUT:      # a page snapshot can outgrow the context it lands in
            text = text[:MCP_OUTPUT] + "\n[truncated]"
        return text or "The tool ran but returned no text."

    return StructuredTool(name=name, description=field_of(tool, "description") or name,
                          args_schema=schema, coroutine=call)


class SharedBrowser:
    """One playwright-mcp connection and one tab per worker.

    A playwright-mcp process *launches* a browser — that is what it is for. One server per
    worker therefore meant one browser (and one window) per worker, on top of anything we
    started ourselves. Instead there is one server for the whole run, and each worker is
    assigned a tab in its one browser. Every tool call selects that tab and runs under one
    lock, so two workers can never drive the same page even though they share the process.
    """

    def __init__(self, client, remote_tools):
        self.client = client
        self.remote_tools = list(remote_tools)
        self.lock = asyncio.Lock()
        self.started = False

    async def _tabs(self, action, **arguments):
        result = await self.client.call_tool("browser_tabs",
                                             {"action": action, **arguments})
        if result_failed(result):
            raise RuntimeError(result_text(result) or f"browser_tabs {action} failed")
        return result_text(result)

    async def claim_tab(self):
        """Give a worker its own tab, reusing the browser's initial tab for the first."""
        if not any(field_of(tool, "name") == "browser_tabs" for tool in self.remote_tools):
            raise Startup("this playwright-mcp offers no browser_tabs tool, so one tab per "
                          "worker cannot be guaranteed. Upgrade playwright-mcp; a single "
                          "shared tab would let two agents work the same page.")
        async with self.lock:
            if not self.started:
                listing = await self._tabs("list")
                found = tab_indices(listing)
                if found:
                    await self._tabs("select", index=found[0])
                    self.started = True
                    return Tab(self, found[0])
            await self._tabs("new")
            listing = await self._tabs("list")
            index = current_tab(listing)
            if index is None:
                found = tab_indices(listing)
                if not found:
                    raise RuntimeError("playwright-mcp opened no tab")
                index = found[-1]
            self.started = True
            return Tab(self, index)

    async def run(self, tab, name, arguments):
        """Select `tab`, call one browser tool, and keep the selection atomic.

        The worker's page can move: a `target=_blank` link opens a new tab, a crashed tab
        disappears and shifts the numbering. So the tab list is re-read after the call,
        while the lock is still held, and `tab.index` follows the page that was just
        driven. Otherwise the next call would select whatever had slid into that slot.
        """
        async with self.lock:
            await self._tabs("select", index=tab.index)
            result = await self.client.call_tool(name, arguments)
            with contextlib.suppress(Exception):
                moved = current_tab(await self._tabs("list"))
                if moved is not None:
                    tab.index = moved
            return result

    def tools(self, tab):
        """The browser tools, bound to `tab`, with tab management hidden from the model."""
        return [mcp_tool(tool, self.client,
                         invoke=lambda name, arguments, tab=tab: self.run(tab, name, arguments))
                for tool in self.remote_tools
                if field_of(tool, "name") not in HIDDEN_BROWSER_TOOLS]


@contextlib.asynccontextmanager
async def browser():
    """The one playwright-mcp process, which owns the one browser and window.

    The client is the official `mcp` SDK — `mcp.Client` spawns the server over stdio,
    does the initialize handshake and lists its tools. The server launches the browser
    itself (persistent, at PROFILE) and `SharedBrowser` hands every worker a tab in it.
    Nothing else starts a browser: one server, one window, one profile, one set of logins.

    The subprocess has to outlive all workers, so this is a context manager rather than a
    function returning a list. Server stderr — which is where the browser's own complaints
    land — goes to MCP_LOG instead of the terminal, which the progress board is drawing on.
    """
    try:
        from mcp import Client, StdioServerParameters, stdio_client
    except ImportError as exc:      # no MCP client here: a sentence, not a traceback
        raise Startup(f"the MCP client is missing ({exc}). `itempipe apply` needs the "
                      "official `mcp` SDK (`uv pip install mcp`); `itempipe search` "
                      "does not.") from exc
    params = StdioServerParameters(**mcp_config())
    PROFILE.mkdir(parents=True, exist_ok=True)
    MCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(MCP_LOG, "a", encoding="utf-8", errors="replace") as errlog:
        # Building the transport here is the only way to move the server's own stderr
        # aside; `Client(params)` would send it to our stderr, over the board. The
        # try/except wraps only the handshake, so a worker's own failure is not
        # dressed up as a startup error.
        async with contextlib.AsyncExitStack() as stack:
            try:
                client = await stack.enter_async_context(
                    Client(stdio_client(params, errlog=errlog)))
                remote = await client.list_tools()
            except Exception as exc:        # no browser at all: say why, and stop
                raise Startup(startup_message(exc)) from exc
            yield SharedBrowser(client, remote.tools)


async def heartbeat(w):
    """Renew a live claim while its agent is still thinking or filling a form."""
    while True:
        await asyncio.sleep(RENEW)
        try:
            w.renew()
        except Exception:       # a transient DB lock must not kill the heartbeat
            pass


async def apply(workers=2):
    """Apply to the queue in parallel, one lead at a time per worker.

    One playwright-mcp process owns the browser and the window; `SharedBrowser` gives
    each worker its own tab in it. Claims are compare-and-swap, kept alive by a heartbeat,
    and every write is fenced by the claim token, so two workers can never submit the same
    lead.

    Nothing that goes wrong with one lead is allowed to end the run: a graph that hits the
    recursion limit, a crashed tab or a dead MCP session is reported on the board, the
    claim is released, and the worker takes the next lead. The MCP client tears itself
    down inside an anyio task group, which re-raises any escaping exception as an
    `ExceptionGroup`, so the catching happens *inside* the `browser()` block and again
    around it — the group never reaches the terminal.
    """
    reports = []
    try:
        async with browser() as shared:
            async def one(i):
                tag, w = f"w{i + 1}", Worker()
                try:
                    status.worker(tag, "state", "attaching to browser", state="browser")
                    tab = await shared.claim_tab()
                    status.worker(tag, "state", f"ready in tab {tab.index}", state="browser")
                    a = agent("apply", tools_for(w) + shared.tools(tab))
                    stuck = 0
                    while claimable():
                        before, failed = claimable(), False
                        beat = asyncio.create_task(heartbeat(w))
                        try:
                            reports.append(await run(a, APPLY, tag=tag,
                                                     limit=APPLY_STEPS))
                        except BaseException as exc:   # one stuck lead, not the run
                            if isinstance(exc, (asyncio.CancelledError,
                                                KeyboardInterrupt, SystemExit)):
                                raise
                            failed = True
                            note = f"{type(exc).__name__}: {clean(exc)}"
                            status.worker(tag, "state", note, state="retry")
                            reports.append(f"[{tag}] gave up on a lead: {note}")
                        finally:  # a worker that stopped mid-claim must not hold a lock
                            beat.cancel()
                            await asyncio.gather(beat, return_exceptions=True)
                            if item := w.mine():
                                drop_holds(item.id)
                                w.end(state=REVIEW if item.submitting else NEW,
                                      submitting=False)
                        stuck = stuck + 1 if failed else 0
                        if stuck >= 3:      # three in a row: this is not going to work
                            reports.append(f"[{tag}] stopping after {stuck} failed "
                                           "leads in a row; the queue is untouched")
                            return
                        # Compare claimable work, not only NEW: an expired claim that was
                        # just released is progress too, and a stuck lead is not.
                        if not failed and claimable() >= before:
                            return
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt,
                                        SystemExit)):
                        raise
                    note = f"{type(exc).__name__}: {clean(exc)}"
                    status.worker(tag, "state", f"stopped: {note}", state="done")
                    reports.append(f"[{tag}] stopped: {note}")

            results = await asyncio.gather(*(one(i) for i in range(workers)),
                                           return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError):
                    reports.append(f"[itempipe] worker crashed: {type(result).__name__}: "
                                   f"{clean(result)}")
    finally:
        # The MCP server owns the browser: closing its stdio session closes the window.
        status.worker("··", "state", "closing browser")
    held = list(Item.select().where(Item.state == HOLD))
    if held:                                 # the run is over, but not for these
        reports.append(f"{len(held)} lead(s) on hold: answer the open questions in "
                       f"{QUESTIONS} and they go back in the queue "
                       "(`itempipe questions` lists them with line numbers):")
        for item in held:
            waiting_on = ", ".join(entry.title for entry in waiting(item.id)) or "?"
            reports.append(f"  {item.name} — waiting on {waiting_on}")
    return reports


def question_state(entry):
    """One word for where a heading stands, for a report line."""
    return "declined" if entry.decline else ("answered" if entry.answer else "open")


def print_questions(limit=10):
    """Report the questions file: its tally, the holds, the open to-dos and the repeats.

    Headings are printed as `path:line: …`, which is the format Emacs' grep and compile
    modes turn into jumps — `itempipe questions` is how you get from the tracker to the
    line you edit, and the file stays the only place a question is answered.
    """
    stored = questions()
    print(f"{QUESTIONS}: {sum(1 for q in stored if q.answer and not q.decline)} "
          f"answered, {sum(1 for q in stored if q.decline)} declined, "
          f"{sum(1 for q in stored if not q.settled)} open; "
          f"{Item.select().where(Item.state == HOLD).count()} lead(s) on hold")
    for item in Item.select().where(Item.state == HOLD):
        on = ", ".join(entry.title for entry in waiting(item.id)) or "no open question"
        print(f"  on hold: {item.name} — waiting on {on}")
    pending = open_questions()
    for entry in pending[:limit]:
        print(f"  open {QUESTIONS}:{entry.line}: {entry.title}")
    if len(pending) > limit:
        print(f"  ... {len(pending) - limit} more open: itempipe questions")
    groups = repeats()
    for group in groups[:5]:               # `save_question` cannot merge them: only you can
        first = group[0]
        for entry in group[1:]:
            print(f"  repeat {QUESTIONS}:{entry.line}: {entry.title}")
            print(f"     same as {QUESTIONS}:{first.line}: {first.title}")
    if len(groups) > 5:
        print(f"  ... {len(groups) - 5} more group(s) of repeats")


def main(argv=None):
    p = argparse.ArgumentParser(prog="itempipe", description=__doc__.splitlines()[0])
    p.add_argument("--db", default=str(DB))
    p.add_argument("command", choices=["init", "search", "apply", "questions"])
    p.add_argument("brief", nargs="*", default=[],
                   help="search brief, or words to find a question by (questions)")
    p.add_argument("-n", type=int, default=2, help="parallel workers")
    args = p.parse_args(argv)
    brief = " ".join(args.brief)
    if fuzz is None:            # the fuzzy finder is what makes the matching "by meaning"
        print("itempipe: rapidfuzz is missing, so questions match on shared words only; "
              "`uv sync` in py/ (or `nix develop`) installs it.", file=sys.stderr)
    path = init_db(args.db) if args.command == "init" else open_db(args.db)
    back = unhold()                 # answers given since the last run release their leads
    if args.command == "init":
        forgotten, learned = prune(), remember_leads()
        unread = sum(1 for task in SEED_TASKS
                     if Seen.get_or_none(Seen.key == url_key(task.value)) is None)
        print(f"{path}: {Item.select().count()} lead(s), {queued()} queued, "
              f"{Seen.select().count()} seen row(s), "
              f"{unread}/{len(SEED_TASKS)} queued board(s) unread, "
              f"{len(space_for())} quer(y/ies) in the config"
              + (f", {forgotten} old one(s) forgotten" if forgotten else "")
              + (f", {learned} lead(s) marked recorded" if learned else "")
              + (f", {back} lead(s) back from hold" if back else ""))
        print_questions()
        return 0
    if args.command == "questions":
        # `path:line: …` on stdout, so in Emacs `M-x grep RET itempipe questions RET`
        # (or `M-x compile`) makes every heading a jump: answer where you edit.
        rows = closest(brief, limit=10) if brief else open_questions()
        for entry in rows:
            print(f"{QUESTIONS}:{entry.line}: {question_state(entry)} {entry.title}")
        print(f"{len(rows)} question(s)" + (f" matching {brief!r}" if brief else
                                            " open (add :ANSWER: or :DECLINE: t)"),
              file=sys.stderr)
        return 0
    if args.command == "search" and not (SEED_TASKS or space_for()):
        print(f"itempipe: {CONFIG} has no roles, regions or boards to search for. "
              "Add some, or point ITEMPIPE_CONFIG at another file.", file=sys.stderr)
        return 1

    status.open_board(f"itempipe {args.command}" +
                      (" — Ctrl-C to stop" if args.command == "search" else ""), tally)
    if back:
        print(f"itempipe: {back} held lead(s) now have an answer and are back in the queue.",
              file=sys.stderr)

    progress: list[str] = []

    async def tick(every=1.0):
        """Keep the tally, the clock and the spinners current while workers run."""
        while True:
            status.refresh()
            await asyncio.sleep(every)

    async def go():
        poller = asyncio.create_task(tick())
        try:
            if args.command == "search":
                return await search(brief, workers=args.n, progress=progress)
            return await apply(workers=args.n)
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)

    try:
        reports = asyncio.run(go())     # a KeyboardInterrupt still restores the tty
    except Startup as exc:              # no browser: a sentence, not a traceback
        print(f"itempipe: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:           # search runs until you stop it
        reports = progress
    finally:
        status.close_board()
    print("\n".join(reports) or "nothing recorded")
    return 0
