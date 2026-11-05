import asyncio
import sys
from dataclasses import dataclass, field

from .agent import build, render
from .db import Memo, Store
from .stream import stream
from .tools import clean, query_key, record_tool, research_tools, url_key


@dataclass
class Session:
    tag: str
    claimed: set = field(default_factory=set)
    recorded: list = field(default_factory=list)


def plan(config, memo, session):
    tasks = []
    for url in config.ingest.seeds:
        if sum(kind == "url" for kind, _ in tasks) >= config.ingest.seeds_per_session:
            break
        key = "u:" + url_key(url)
        if memo.claim(key, "url")[0]:
            session.claimed.add(key)
            tasks.append(("url", url))
    for query in config.ingest.queries:
        if sum(kind == "query" for kind, _ in tasks) >= config.ingest.queries_per_session:
            break
        key = query_key(query)
        if memo.claim(key, "query")[0]:
            session.claimed.add(key)
            tasks.append(("query", query))
    return tasks


def message(tasks):
    lines = "\n".join(f"- read {value}" if kind == "url" else f'- search "{value}"'
                      for kind, value in tasks)
    return f"""This session records at most one item, then stops.

Start here and open what it turns up:
{lines}

Call record once you have verified one item. Then stop and write one line saying what
you found."""


async def run(config):
    store = Store(config).connect()
    memo = Memo(store, config.ingest.ttl)
    memo.forget()
    reports = []

    async def worker(index):
        tag = f"w{index + 1}"
        while True:
            session = Session(tag)
            tasks = plan(config, memo, session)
            if not tasks:
                print(f"{tag} idle: nothing left to look at", file=sys.stderr)
                await asyncio.sleep(config.ingest.idle)
                continue
            agent = build(
                config.model,
                render(config.ingest.prompt, {"BRIEF": config.brief}),
                [record_tool(store, memo, session), *research_tools(memo, session)])
            try:
                answer = await stream(agent, message(tasks), tag, config.ingest.steps)
                reports.append(f"{tag}: {clean(answer)[:200] or 'no report'}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"{tag} session failed: {clean(exc)}", file=sys.stderr)
            finally:
                memo.release(session.claimed)
            await asyncio.sleep(config.ingest.pause)

    await asyncio.gather(*(worker(index) for index in range(config.ingest.workers)),
                         return_exceptions=True)
    return reports
