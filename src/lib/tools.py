import asyncio
import re
from pathlib import Path

from langchain_core.tools import StructuredTool, tool
from peewee import IntegrityError
from pydantic import create_model

HEADING = re.compile(r"^\*+\s+(.+?)\s*$")
PROPERTY = re.compile(r"^\s*:([A-Z][A-Z0-9_-]*):\s*(.*?)\s*$")
TRACKING = re.compile(r"^(utm_\w+|ref|src|source|gclid|fbclid|mc_cid)$", re.IGNORECASE)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def url_key(url):
    from urllib.parse import urlsplit
    parts = urlsplit(clean(url))
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    keep = [pair for pair in parts.query.split("&")
            if pair and not TRACKING.match(pair.split("=")[0])]
    query = "&".join(sorted(keep))
    return f"{host}{path}" + (f"?{query}" if query else "")


def query_key(query):
    return "q:" + re.sub(r"[^a-z0-9]+", " ", clean(query).lower()).strip()


def refusal(row, action):
    if row is None:
        return f"NOT {action}: another worker has it. Take the next task."
    return f"NOT {action}: already {row.verdict}. Take the next task."


def record_tool(store, memo, session):
    fields = {name: (str, "") for name in store.columns}
    schema = create_model("Row", **fields)

    def record(**values):
        data = {name: clean(value) for name, value in values.items()
                if name in store.columns and clean(value)}
        if not data.get(store.config.key):
            return f"NOT recorded: {store.config.key} is required."
        key = "u:" + url_key(data[store.config.key])
        known = memo.get(key)
        if known is not None and known.verdict == "recorded":
            return "NOT recorded: that one is already tracked."
        try:
            store.row.create(state=store.new, **data)
        except IntegrityError:
            memo.settle(key, "duplicate", data.get("name"))
            return "NOT recorded: the database already holds that row."
        memo.settle(key, "recorded", data.get("name"))
        session.recorded.append(key)
        return "Recorded. This session has its item: stop and write the report."

    return StructuredTool.from_function(
        record, name="record", args_schema=schema,
        description="Record one item. The arguments are the table's own columns.")


def research_tools(memo, session):
    def take(key, kind, action):
        if key in session.claimed:
            return None
        won, row = memo.claim(key, kind)
        if not won:
            return refusal(row, action)
        session.claimed.add(key)
        return None

    @tool
    async def search(query: str) -> str:
        """Search the web once, then open the posting with fetch."""
        from ddgs import DDGS
        key = query_key(query)
        if refused := take(key, "query", "searched"):
            return refused
        found = await asyncio.to_thread(
            lambda: list(DDGS(timeout=15).text(clean(query), max_results=8) or []))
        if not found:
            memo.settle(key, "empty", "no results")
            return "NOT searched: no results. Do not repeat the query; try another task."
        memo.settle(key, "found", f"{len(found)} hits")
        return "\n\n".join(
            "\n".join(filter(None, (hit.get("title"), hit.get("href"), hit.get("body"))))
            for hit in found)[:6000]

    @tool
    async def fetch(url: str) -> str:
        """Fetch one URL as readable text."""
        import httpx
        import trafilatura
        url = clean(url)
        if not url.startswith(("http://", "https://")):
            return "NOT fetched: need an http(s) URL."
        key = "u:" + url_key(url)
        if refused := take(key, "url", "fetched"):
            return refused
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            memo.settle(key, "down", f"{type(exc).__name__}")
            return f"NOT fetched: {type(exc).__name__}: {clean(exc)}."
        if response.status_code >= 400:
            memo.settle(key, "dead", f"HTTP {response.status_code}")
            return f"NOT fetched: HTTP {response.status_code} for {url}."
        text = await asyncio.to_thread(trafilatura.extract, response.text)
        body = "\n".join(line for line in (text or response.text).splitlines() if line.strip())
        memo.settle(key, "open", f"HTTP {response.status_code}")
        return body.strip()[:8000] or "Fetched, but the page has no readable text."

    return [search, fetch]


class Notes:
    def __init__(self, path, limit=3):
        self.path = Path(path)
        self.limit = limit

    def entries(self):
        found = []
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        for line in text.splitlines():
            if match := HEADING.match(line):
                found.append([match.group(1), {}])
            elif found and (match := PROPERTY.match(line)) and match.group(2):
                found[-1][1][match.group(1).upper()] = match.group(2)
        return [(title, properties) for title, properties in found if properties]

    def ranked(self, query):
        try:
            from rapidfuzz import fuzz
        except ImportError:
            fuzz = None
        words = clean(query).lower()
        ranked = []
        for title, properties in self.entries():
            haystack = " ".join([title, *properties.values()]).lower()
            score = (float(fuzz.token_set_ratio(words, haystack)) if fuzz
                     else float(len(set(words.split()) & set(haystack.split()))))
            ranked.append((score, title, properties))
        return sorted(ranked, key=lambda hit: hit[0], reverse=True)[: self.limit]

    def lookup(self, query):
        rows = self.ranked(query)
        if not rows:
            return "No stored answers."
        blocks = []
        for number, (_, title, properties) in enumerate(rows, 1):
            lines = [f"{number}. {title}"]
            lines += [f"   {name.title()}: {value}" for name, value in properties.items()]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def add(self, title, source=""):
        wanted = clean(title)
        for known, _ in self.entries():
            if clean(known).lower() == wanted.lower():
                return f"Already asked: {known!r}."
        block = f"\n** {wanted}\n   :PROPERTIES:\n   :ANSWER:"
        block += f"\n   :SOURCE: {clean(source)}" if clean(source) else ""
        block += "\n   :END:\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as out:
            out.write(block)
        return f"Saved {wanted!r}, waiting for the user to answer it."


def notes_tools(notes):
    @tool
    def lookup(question: str) -> str:
        """Look up the user's stored answers to application questions."""
        return notes.lookup(question)

    @tool
    def ask(question: str) -> str:
        """Save a question no stored answer covers, so the user answers it once."""
        return notes.add(question)

    return [lookup, ask]
