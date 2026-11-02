"""Search leads, apply to them: two agents, N workers each, one SQLite queue.

The claim is a compare-and-swap (read the next free row, then UPDATE it guarded by the token
it had), so the database is the lock. Every later write is fenced by that token, and
`submitting` is set before the irreversible step, so a crash goes to review, not a retry.
"""

import argparse, asyncio, os, re, shutil, uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_core.tools import tool
from peewee import (BooleanField, CharField, DateTimeField, IntegrityError, Model,
                    SqliteDatabase, TextField)

ROOT = Path(__file__).resolve().parents[2]
NEW, WORKING, ACTIVE, REVIEW = "NEW", "WORKING", "ACTIVE", "NEEDS_REVIEW"
FENCED = "This lead is no longer yours; stop."
LEASE = timedelta(minutes=30)
DB = Path(os.environ.get("ITEMPIPE_DB", ROOT / "py/itempipe.db"))
PROMPTS, CV = Path(__file__).with_name("prompts"), ROOT / "cv.typ"
ANGLES = ('HN "Who is hiring?"', "YC Work at a Startup", "niche boards", "company careers",
          "practitioner communities", "freelance / contract")
APPLY = ("Claim one lead with claim_next_item, work it to a final state with finish_item, then "
         "stop. If a tool says the lead is no longer yours, stop immediately.")

db = SqliteDatabase(None, pragmas={"journal_mode": "wal", "busy_timeout": 10000})


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


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def open_db(path=DB):
    """Point peewee at `path`, create the table, and migrate an older tracker in place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not db.is_closed():
        db.close()                       # a different --db must not reuse the old connection
    db.init(str(path))
    db.connect()
    Item.create_table(safe=True)
    existing = {c.name for c in db.get_columns("items")}
    if existing != set(Item._meta.fields):      # schema drift: keep the rows, rebuild the table
        shared = ", ".join(n for n in Item._meta.fields if n in existing)
        db.execute_sql("ALTER TABLE items RENAME TO items_legacy")
        Item.create_table()
        db.execute_sql(f"INSERT INTO items ({shared}) SELECT {shared} FROM items_legacy")
        db.execute_sql("DROP TABLE items_legacy")
    return path


def queued():
    return Item.select().where(Item.state == NEW, Item.archived_at.is_null()).count()


def free():
    """Unclaimed, or claimed and abandoned long enough ago to be retryable."""
    return Item.select().where(Item.archived_at.is_null(),
                               (Item.state == NEW) |
                               ((Item.state == WORKING) & (Item.claimed_until < now())))


open_db()   # importing prepares the store; the CLI re-points it when --db is given


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


def tools_for(w=None):
    @tool
    def create_item(name: str, title: str, url: str, notes: str = "") -> str:
        """Record one company, its exact title and the posting's own http(s) URL, with why
        it fits in notes. Duplicates, archived ones included, are refused."""
        name, title, url = clean(name), clean(title), clean(url)
        if not (name and title and url.startswith("http")):
            return "NOT recorded: need a company, an exact title and the posting's own URL."
        try:
            Item.create(name=name, name_key=name.lower(), title=title, url=url,
                        notes=clean(notes) or None)
        except IntegrityError:
            seen = Item.get(Item.name_key == name.lower())
            return f"Already tracked ({'archived' if seen.archived_at else seen.state}): {seen.name}."
        return f"Recorded: {name} — {title} <{url}>."

    @tool
    async def web_search(query: str) -> str:
        """Search the web; then open the individual posting with fetch_page."""
        from ddgs import DDGS
        found = await asyncio.to_thread(lambda: list(DDGS().text(query, max_results=8) or []))
        return "\n\n".join("\n".join(filter(None, (r.get("title"), r.get("href"), r.get("body"))))
                           for r in found) or "No results."

    @tool
    async def fetch_page(url: str) -> str:
        """Fetch one URL as readable text, to check a posting is still open."""
        import httpx, trafilatura
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            html = (await client.get(url)).text
        text = await asyncio.to_thread(trafilatura.extract, html) or html
        return "\n".join(l for l in (p.strip() for p in text.splitlines()) if l)[:8000]

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


async def run(agent_, text):
    out = await agent_.ainvoke({"messages": [{"role": "user", "content": text}]},
                               config={"recursion_limit": 60})
    return str(out["messages"][-1].content).strip()


async def search(brief, workers=4):
    async def one(i):
        return await run(agent("search", tools_for()),
                         f"You are search worker {i + 1} of {workers}. "
                         f"Angle: {ANGLES[i % len(ANGLES)]}.\n\n{brief}")
    return list(await asyncio.gather(*(one(i) for i in range(workers))))


async def browser(i):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    args = ["--headless", "--user-data-dir", str(ROOT / "py/.playwright-profiles" / f"apply-{i}")]
    if chrome := shutil.which("chromium-sandboxed") or shutil.which("chromium"):
        args += ["--executable-path", chrome]
    client = MultiServerMCPClient({"playwright": {"command": "playwright-mcp", "args": args,
                                                 "transport": "stdio"}})
    return list(await client.get_tools())


async def apply(workers=2):
    slots, reports = asyncio.Semaphore(2), []

    async def one(i):
        w = Worker()
        async with slots:
            a = agent("apply", tools_for(w) + await browser(i))
            while before := queued():
                try:
                    reports.append(await run(a, APPLY))
                finally:            # a worker that stopped mid-claim must not hold a lock
                    if item := w.mine():
                        w.end(state=REVIEW if item.submitting else NEW, submitting=False)
                if queued() >= before:
                    return          # no progress: that lead is stuck, do not spin

    await asyncio.gather(*(one(i) for i in range(workers)))
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
        print(f"{path}: {Item.select().count()} lead(s), {queued()} queued")
        return 0
    fn, brief = (search, (args.brief,)) if args.command == "search" else (apply, ())
    print("\n---\n".join(asyncio.run(fn(*brief, workers=args.n))))
