import asyncio

from langchain_core.tools import tool

from .agent import build, render
from .browser import browser
from .db import Claim, Store
from .stream import stream
from .tools import Notes, clean, notes_tools

START = ("Claim one item with claim_row, work it, then finish it with one outcome. "
         "If a tool says the item is not yours, stop immediately.")
FENCED = "This item is no longer yours; stop."


def act_tools(config, store, claim):
    states = dict(config.act.outcomes)

    @tool
    def claim_row() -> str:
        """Claim one item and return its fields. Call this first."""
        row = claim.take()
        if row is None:
            return "No items left. Stop."
        return "\n".join(f"{name}: {getattr(row, name)}" for name in store.columns
                         if getattr(row, name) not in (None, ""))

    @tool
    def begin() -> str:
        """Call immediately before the irreversible action."""
        return "Guard set." if claim.guard() else FENCED

    @tool
    def finish(outcome: str, reason: str = "") -> str:
        """End the claimed item with one of the configured outcomes."""
        if outcome not in states:
            return f"NOT done: outcome must be one of {', '.join(states)}."
        row = claim.mine()
        if row is None:
            return FENCED
        if outcome in config.act.guarded and not row.submitting:
            return "NOT done: call begin immediately before the final action."
        if not claim.end(state=states[outcome], submitting=False):
            return FENCED
        return f"{outcome}: {clean(reason) or 'no reason'}."

    return [claim_row, begin, finish]


async def heartbeat(claim):
    while True:
        await asyncio.sleep(max(1.0, claim.lease.total_seconds() / 3))
        claim.renew()


async def run(config):
    store = Store(config).connect()
    if not config.act.browser:
        return await work(config, store, None)
    async with browser(config) as shared:
        return await work(config, store, shared)


async def work(config, store, shared):
    reports = []

    async def worker(index):
        tag = f"w{index + 1}"
        claim = Claim(store, config.act.lease)
        notes = Notes(config.act.notes) if config.act.notes else None
        extra = shared.tools(await shared.claim()) if shared is not None else []
        agent = build(
            config.model,
            render(config.act.prompt, {"BRIEF": config.brief}),
            [*act_tools(config, store, claim),
             *(notes_tools(notes) if notes is not None else []),
             *extra])
        while claim.count():
            before = claim.count()
            beat = asyncio.create_task(heartbeat(claim))
            try:
                answer = await stream(agent, START, tag, config.act.steps)
                if answer:
                    reports.append(f"{tag}: {clean(answer)[:200]}")
            finally:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)
                if claim.row is not None:
                    claim.end(state=store.new)
            if claim.count() >= before:
                reports.append(f"{tag} stopped: the queue did not move")
                break
        return reports

    await asyncio.gather(*(worker(index) for index in range(config.act.workers)))
    return reports
