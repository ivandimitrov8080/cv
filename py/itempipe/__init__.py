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

import argparse, asyncio, contextlib, os, re, shutil, sys, time, tomllib, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from langchain_core.tools import StructuredTool, tool
from peewee import (BooleanField, CharField, DateTimeField, IntegrityError, Model,
                    SqliteDatabase, TextField, fn)

from . import status

ROOT = Path(__file__).resolve().parents[2]
NEW, WORKING, ACTIVE, REVIEW = "NEW", "WORKING", "ACTIVE", "NEEDS_REVIEW"
HOLD = "HOLD"                       # waiting for the user to answer a question
FENCED = "This lead is no longer yours; stop."
LEASE = timedelta(minutes=30)
RETRY = timedelta(days=7)           # a dead posting may be a bad deploy: look once more
RETENTION = timedelta(days=int(os.environ.get("ITEMPIPE_SEEN_DAYS", "365")))
DB = Path(os.environ.get("ITEMPIPE_DB", ROOT / "py/itempipe.db"))
PROMPTS, CV = Path(__file__).with_name("prompts"), ROOT / "cv.typ"
QUESTIONS = Path(os.environ.get("ITEMPIPE_QUESTIONS", ROOT / "application-questions.org"))
CONFIG = Path(os.environ.get("ITEMPIPE_CONFIG") or Path(__file__).with_name("search.toml"))
CDP_PORT = int(os.environ.get("ITEMPIPE_CDP_PORT", "9222"))
CHROME = shutil.which("chromium-sandboxed") or shutil.which("chromium")
PROFILE = Path(os.environ.get("ITEMPIPE_PROFILE", "/tmp/itempipe-chromium"))
CHROME_LOG = Path(os.environ.get("ITEMPIPE_CHROME_LOG", f"/tmp/itempipe-chromium-{CDP_PORT}.log"))
MCP_LOG = Path(os.environ.get("ITEMPIPE_MCP_LOG", f"/tmp/itempipe-mcp-{CDP_PORT}.log"))
MCP_OUTPUT = int(os.environ.get("ITEMPIPE_MCP_OUTPUT", "20000"))  # chars kept per tool call
SEARCH_TRIES = int(os.environ.get("ITEMPIPE_SEARCH_TRIES", "3"))
SESSIONS = int(os.environ.get("ITEMPIPE_SESSIONS", "0"))        # 0 = until you stop it
STEPS = int(os.environ.get("ITEMPIPE_SESSION_STEPS", "26"))     # graph steps per session
BUDGET = int(os.environ.get("ITEMPIPE_TOOL_BUDGET", "12"))      # tool calls per session
SEEDS = int(os.environ.get("ITEMPIPE_BOARD_SEEDS", "2"))        # board tasks per session
PAUSE = float(os.environ.get("ITEMPIPE_PAUSE", "2"))            # breather between sessions
IDLE = float(os.environ.get("ITEMPIPE_IDLE", "300"))            # wait when no query is left
BOARD_TTL = timedelta(days=float(os.environ.get("ITEMPIPE_BOARD_DAYS", "3")))
QUERY_TTL = timedelta(days=float(os.environ.get("ITEMPIPE_QUERY_DAYS", "30")))


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
         "filling a form, work it to a final state with finish_item, then stop. If a tool "
         "says the lead is no longer yours, stop immediately. You share one browser with the "
         "other workers: work only in the tab your own tools are on, and never switch, open "
         "or close tabs.")

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
    question = TextField(null=True)         # what a HOLD is waiting for an answer to
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
    kind = CharField(default="url")         # url | query | board
    url = TextField(null=True)              # as first written, for the brief
    verdict = CharField(default=CLAIMED)
    note = CharField(null=True)             # HTTP status, hit count, or the company
    label = CharField(null=True)            # which task queue entry wrote it
    session = CharField(null=True)          # the session that last wrote the verdict
    checked_at = DateTimeField(default=now)

    class Meta:
        database = db


def flag(name):
    """An env var read as a switch: unset, 0, false, no, off all mean no."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def migrate():
    """`seen.angle` is now `seen.label`; rename it in place on older databases."""
    columns = {row[1] for row in db.execute_sql("PRAGMA table_info(seen)")}
    if columns and "angle" in columns and "label" not in columns:
        db.execute_sql("ALTER TABLE seen RENAME COLUMN angle TO label")
    lead = {row[1] for row in db.execute_sql("PRAGMA table_info(items)")}
    if lead and "question" not in lead:      # a hold needs somewhere to keep its question
        db.execute_sql("ALTER TABLE items ADD COLUMN question TEXT")


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
    migrate()
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
    counts = {state: 0 for state in (NEW, WORKING, ACTIVE, HOLD, REVIEW)}
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
# per question, the answer in an `:ANSWER:` property. The agent reads it through
# `lookup_answer` and writes to it when it has to hold a lead, so the file is both the
# memory and the inbox. A heading without `:ANSWER:` is a question waiting to be answered.

QUESTIONS_HEAD = """#+TITLE: Application Questions and Answers

# One entry per question, with the answer in an :ANSWER: property.
# Questions are phrased generically (<Company>, <N> days, ...) so the same
# answer serves every application. :SOURCE: lists the forms that asked it.
# A heading with no :ANSWER: was written by `itempipe apply` and is still open:
# fill it in and the lead it blocked goes back in the queue on the next run.
"""

HEADING = re.compile(r"^\*+\s+(.*?)\s*$")
PROPERTY = re.compile(r"^\s*:([A-Za-z][\w@#%+-]*):\s*(.*?)\s*$")
STOPWORDS = frozenset("""a an and any are as at be been by can could do does for from
    has have how i if in is it its me my of on or please should tell that the their
    them this to us was we were what when where which who why will with would you your
    yours""".split())


@dataclass(frozen=True)
class Question:
    """One heading of the questions file: what was asked, and the user's answer to it."""
    title: str
    answer: str = ""
    note: str = ""
    source: str = ""

    @property
    def key(self):
        return question_key(self.title)


def tokens_of(text):
    """The words that carry the meaning of a question: lowercase, no stopwords."""
    return [token for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
            if token not in STOPWORDS]


def question_key(text):
    """One question, one identity: case, punctuation, stopwords and spacing folded away."""
    return " ".join(tokens_of(text))


def questions(path=None):
    """Every entry in the questions file, in file order: answered and open alike.

    Org is read by hand rather than by a parser because only two things matter here: the
    heading, and the properties under it. A heading with no properties at all (`*
    Recurring answers`) is a section title, and simply matches nothing.
    """
    try:
        text = Path(path or QUESTIONS).read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        if match := HEADING.match(line):
            found.append(Question(clean(match.group(1))))
            continue
        if not found or not (match := PROPERTY.match(line)):
            continue
        name, value = match.group(1).upper(), match.group(2)
        if not value or name in ("PROPERTIES", "END"):
            continue
        entry = found[-1]
        if name == "ANSWER":
            found[-1] = Question(entry.title, value, entry.note, entry.source)
        elif name == "NOTE":
            found[-1] = Question(entry.title, entry.answer, value, entry.source)
        elif name == "SOURCE":
            found[-1] = Question(entry.title, entry.answer, entry.note, value)
    return found


def answer_score(title, tokens):
    """3 per matching token, plus 1 when every token matched: specific beats broad."""
    lowered = str(title or "").lower()
    hits = sum(1 for token in tokens if token in lowered)
    return hits * 3 + (1 if tokens and hits == len(tokens) else 0)


def answered(query="", limit=5):
    """The answered questions that best match `query`, best first. Unanswered never match."""
    tokens = tokens_of(query)
    rows = [entry for entry in questions() if entry.answer]
    if not tokens:
        return rows[:limit]
    ranked = sorted(((answer_score(entry.title, tokens), entry) for entry in rows),
                    key=lambda pair: pair[0], reverse=True)
    return [entry for score, entry in ranked if score >= 3][:limit]


def best_answer(query):
    """The one stored answer that covers every word of `query`, if there is one.

    Stricter than `answered` on purpose: this is what refuses a hold, so a partial match
    (a salary question against "current salary") must not be taken as an answer.
    """
    tokens = tokens_of(query)
    if not tokens:
        return None
    full = [entry for entry in questions()
            if entry.answer and all(token in entry.title.lower() for token in tokens)]
    return max(full, key=lambda entry: len(entry.title)) if full else None


def answer_text(query="", limit=5):
    """What `lookup_answer` hands the model: numbered questions with their answers."""
    rows = answered(query, limit)
    if rows:
        blocks = []
        for number, entry in enumerate(rows, 1):
            block = f"{number}. {entry.title}\n   Answer: {entry.answer}"
            if entry.note:
                block += f"\n   Note: {entry.note}"
            blocks.append(block)
        return "\n\n".join(blocks)
    hint = ('Do not invent one: call finish_item("hold", "<the form\'s question, verbatim>") '
            "so it is saved for the user and the lead waits instead of being abandoned.")
    if not any(entry.answer for entry in questions()):
        return f"No stored answers yet. {hint}"
    return f"No stored answer matches {clean(query)!r}. {hint}"


def ask(question, source=""):
    """Write an unanswered question to the file. Returns (entry, was_new).

    Idempotent by wording: a question already in the file comes back unchanged, so two
    workers stopping on the same field leave one heading for the user, not two.
    """
    question = clean(question)
    known = {entry.key: entry for entry in questions()}
    if (found := known.get(question_key(question))) is not None:
        return found, False
    path = Path(QUESTIONS)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = QUESTIONS_HEAD
    if text and not text.endswith("\n"):
        text += "\n"
    block = (f"\n** {question}\n:PROPERTIES:\n"
             f":SOURCE:{f' {clean(source)}' if clean(source) else ''}\n:END:\n")
    path.write_text(text + block, encoding="utf-8")
    return Question(question, source=clean(source)), True


def unhold():
    """Put held leads back in the queue once the file answers what held them there.

    Matched on the question's own words, then on any answer that covers all of them, so
    a heading the user reworded still releases the lead. Called at startup: the loop is
    "hold, you answer, run again".
    """
    keys = {entry.key for entry in questions() if entry.answer}
    back = 0
    for item in list(Item.select().where(Item.state == HOLD)):
        if not item.question:
            continue
        if question_key(item.question) in keys or best_answer(item.question):
            back += (Item.update(state=NEW, question=None)
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
        sentences do not. Use the answer as written, filling in <Company> or <N>."""
        return answer_text(query)

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
        timeout), "hold" (a required field asks something lookup_answer cannot answer — put
        the form's question verbatim in reason; the lead waits for the user instead of
        being lost) or "archive" (the lead cannot be applied to at all)."""
        item = w.mine()
        if item is None:
            return FENCED
        if outcome == "applied":
            if not item.submitting:
                return "NOT done: call begin_submission immediately before the final submit."
            w.end(state=ACTIVE, submitting=False, advanced_on=now())
        elif outcome == "retry":
            w.end(state=REVIEW if item.submitting else NEW, submitting=False)
        elif outcome == "hold":
            question = clean(reason)
            if not question_key(question):
                return ("NOT done: put the form's unanswered question verbatim in reason, "
                        "so the user can answer it once for every application.")
            if found := best_answer(question):
                return (f"NOT held: {QUESTIONS.name} already answers that — "
                        f"{found.title}: {found.answer}. Use it and finish the application.")
            if item.submitting:
                w.end(state=REVIEW, submitting=False, question=question)
                return (f"{item.name}: the submission had already started, so it needs "
                        f"review; the question is saved in {QUESTIONS.name}.")
            entry, _ = ask(question, f"{item.name} ({item.title})")
            w.end(state=HOLD, submitting=False, question=question)
            return (f"{item.name}: on hold until you answer {entry.title!r} in "
                    f"{QUESTIONS.name}. It goes back in the queue on the next run.")
        else:
            w.end(archived_at=now(), submitting=False,
                  archive_reason=clean(reason) or "abandoned")
        return f"{item.name}: {outcome} ({clean(reason) or 'no reason'})."

    return [claim_next_item, lookup_answer, begin_submission, finish_item] if w else [
        create_item, web_search, fetch_page]


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
        return ("Chromium wants a display. Run under a graphical session (or through "
                "chromium-sandboxed, which binds the Wayland socket), or set "
                "ITEMPIPE_HEADLESS=1.")
    if "address already in use" in text or "devtoolsactiveport" in text:
        return (f"CDP port {CDP_PORT} is taken. Kill the other browser, or set "
                "ITEMPIPE_CDP_PORT.")
    if "multiple targets are not supported" in text:
        return ("Chromium's headless mode cannot open more than one tab at startup. "
                "Unset ITEMPIPE_HEADLESS to run headed.")
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
    """Start one Chromium — headed unless ITEMPIPE_HEADLESS is set — that every worker's
    playwright-mcp attaches to.

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
    if flag("ITEMPIPE_HEADLESS"):        # headed by default: headless is opt-in
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
    """One worker's MCP server: no browser of its own, a CDP seat in the shared one.

    Exactly the fields `mcp.StdioServerParameters` takes, so it can be passed straight to
    it. The environment is inherited whole because playwright-mcp needs more of it than
    the SDK's safe default (HOME, PATH): a display, a runtime dir, the sandbox binds.
    """
    return {"command": "playwright-mcp",
            "args": ["--cdp-endpoint", endpoint, "--cdp-timeout", "30000"],
            "env": dict(os.environ)}


def mcp_tool(tool, client):
    """One MCP tool as a LangChain tool that calls back through the worker's session.

    The server's own JSON Schema is handed straight over as `args_schema`: LangChain takes
    a plain schema dict and gives it to the model verbatim, so there is no pydantic model
    to build and nothing that can drift from what the server declared.

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
            result = await client.call_tool(name, arguments)
        except Exception as exc:        # a dead subprocess must not kill the worker
            return f"NOT done: {type(exc).__name__}: {clean(exc)}. The browser may be gone."
        parts = []
        for block in field_of(result, "content") or ():
            if (text := field_of(block, "text")) is not None:
                parts.append(text_of(text))
            else:               # a screenshot or a resource: name it, never inline it
                parts.append(f"[{field_of(block, 'type', 'other')} content]")
        text = "\n".join(part for part in parts if part).strip()
        if field_of(result, "is_error") or field_of(result, "isError"):
            return f"NOT done: {text or 'the tool reported an error without saying why.'}"
        if len(text) > MCP_OUTPUT:      # a page snapshot can outgrow the context it lands in
            text = text[:MCP_OUTPUT] + "\n[truncated]"
        return text or "The tool ran but returned no text."

    return StructuredTool(name=name, description=field_of(tool, "description") or name,
                          args_schema=schema, coroutine=call)


@contextlib.asynccontextmanager
async def browser(endpoint):
    """A worker's own playwright-mcp subprocess, held open while that worker runs.

    The client is the official `mcp` SDK — `mcp.Client` spawns the server over stdio,
    does the initialize handshake and lists its tools — and each tool is converted to a
    LangChain tool here. LangChain 1's `langchain.mcp` would do the same conversion, but
    it is FastMCP underneath (`fastmcp>=4`), which this environment does not have and
    does not need: the protocol is the SDK's, the adaptation is twenty lines.

    The subprocess has to outlive the loop that uses the tools, so this is a context
    manager rather than a function returning a list. Server stderr goes to MCP_LOG
    instead of the terminal, which the progress board is busy drawing on.
    """
    try:
        from mcp import Client, StdioServerParameters, stdio_client
    except ImportError as exc:      # no MCP client here: a sentence, not a traceback
        raise Startup(f"the MCP client is missing ({exc}). `itempipe apply` needs the "
                      "official `mcp` SDK (`uv pip install mcp`); `itempipe search` "
                      "does not.") from exc
    params = StdioServerParameters(**mcp_config(endpoint))
    MCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(MCP_LOG, "a", encoding="utf-8", errors="replace") as errlog:
        # Building the transport here is the only way to move the server's own stderr
        # aside; `Client(params)` would send it to our stderr, over the board.
        async with Client(stdio_client(params, errlog=errlog)) as client:
            remote = await client.list_tools()
            yield [mcp_tool(tool, client) for tool in remote.tools]


async def apply(workers=2):
    slots, reports = asyncio.Semaphore(2), []
    proc = await chromium()
    try:
        async def one(i):
            tag, w = f"w{i + 1}", Worker()
            async with slots:
                status.worker(tag, "state", "attaching to browser", state="browser")
                async with browser(cdp_endpoint()) as mcp_tools:
                    a = agent("apply", tools_for(w) + mcp_tools)
                    while before := queued():
                        try:
                            reports.append(await run(a, APPLY, tag=tag))
                        finally:    # a worker that stopped mid-claim must not hold a lock
                            if item := w.mine():
                                w.end(state=REVIEW if item.submitting else NEW,
                                      submitting=False)
                        if queued() >= before:
                            return  # no progress: that lead is stuck, do not spin

        await asyncio.gather(*(one(i) for i in range(workers)))
    finally:
        if proc is not None:
            status.worker("··", "state", "closing browser")
            await stop_proc(proc)            # tolerates a browser that already died
    held = Item.select().where(Item.state == HOLD).count()
    if held:                                 # the run is over, but not for these
        reports.append(f"{held} lead(s) on hold: answer the open questions in "
                       f"{QUESTIONS} and they go back in the queue.")
    return reports


def main(argv=None):
    p = argparse.ArgumentParser(prog="itempipe", description=__doc__.splitlines()[0])
    p.add_argument("--db", default=str(DB))
    p.add_argument("command", choices=["init", "search", "apply"])
    p.add_argument("brief", nargs="?", default="")
    p.add_argument("-n", type=int, default=2, help="parallel workers")
    args = p.parse_args(argv)
    path = open_db(args.db)
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
        stored = questions()
        print(f"{QUESTIONS}: {sum(1 for q in stored if q.answer)} answered, "
              f"{sum(1 for q in stored if not q.answer)} open; "
              f"{Item.select().where(Item.state == HOLD).count()} lead(s) on hold")
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
                return await search(args.brief, workers=args.n, progress=progress)
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
