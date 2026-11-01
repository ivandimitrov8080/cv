"""Orchestration: search is a fan-out over angles, apply is a queue drain.

Search fans out because a brief decomposes into independent channels. Apply does
not: the number of items is unknown and each is expensive, so a bounded pool of
workers loops `claim -> work -> record` until the queue is empty or the item
budget is spent. Both are plain `asyncio.TaskGroup`s; wrapping the drain in graph
nodes would add a moving part and nothing else.
"""

import asyncio
import contextlib
from typing import Any, Awaitable, Callable, Sequence

from .agents import brief_for, build_apply_worker, build_search_worker, run_to_completion
from .config import Config, default_config
from .store import ItemStore
from .tools.browser import browser_slot, make_browser_tools
from .worker import WorkerContext, liveness, reap

# The channel map from strategies/no-hr-strategy.org, as search angles.
CHANNELS = ('HN "Who is hiring?" comments', "YC Work at a Startup", "niche vertical boards",
            "static /careers pages with mailto", "practitioner communities",
            "B2B contract / freelance")

APPLY_BRIEF = ("Claim exactly one item with claim_next_item, work it to a final state "
               "(advance_item, pause_item_for_answer, archive_item or release_item), then "
               "stop. If a tool says the item is no longer yours, stop immediately.")


def plan_slices(brief: str, count: int) -> list[str]:
    """Split a brief into distinct angles -- deterministically, on purpose.

    A non-deterministic planning step before a fan-out would make a search run
    unreproducible; the channel list is the strategy document's, so the slices
    stay meaningful.
    """
    count = max(1, int(count))
    if count == 1:
        return [brief]
    return [f"{brief}\n\nAngle {i + 1} of {count}: {CHANNELS[i % len(CHANNELS)]}. "
            "Stay on this channel; if it is exhausted, say so instead of drifting."
            for i in range(count)]


def _settle(items: ItemStore, ctx: WorkerContext) -> None:
    """A worker that stopped mid-claim must not leave a silent lock behind."""
    if not ctx.claimed:
        return
    attempt = items.latest_attempt(int(ctx.item_id), ctx.worker_id)
    if attempt is not None and attempt["status"] in ("submitting", "sent"):
        # At-most-once: let the lease expire and the reaper send it to review.
        ctx.clear()
        return
    items.release(int(ctx.item_id), str(ctx.claim_token),
                  "worker stopped without recording an outcome")
    ctx.clear()


async def drain_apply(stores: dict[str, Any], *, cfg: Config | None = None, workers: int | None = None,
                      max_items: int | None = None, dry_run: bool = False, browser: bool = False,
                      make_agent: Callable[[WorkerContext, list[Any]], Any] | None = None,
                      run: Callable[[Any, str], Awaitable[str]] | None = None) -> dict[str, Any]:
    cfg = cfg or default_config()
    items: ItemStore = stores["items"]
    workers = max(1, int(workers or cfg.max_apply_workers))
    run = run or (lambda agent, brief: run_to_completion(agent, brief, cfg=cfg))
    lock, reports, processed = asyncio.Lock(), [], 0

    async def one(index: int) -> None:
        nonlocal processed
        ctx = WorkerContext(role="apply", index=index, dry_run=dry_run, config=cfg)
        async with browser_slot(cfg, ctx), liveness(stores["workers"], ctx, store=items):
            extra = await make_browser_tools(cfg, ctx) if browser and make_agent is None else []
            agent = (make_agent or (lambda c, e: build_apply_worker(
                items, c, cfg=cfg, answers=stores["answers"], extra_tools=e)))(ctx, extra)
            stalls = 0
            while True:
                async with lock:
                    if max_items is not None and processed >= max_items:
                        return
                before = items.status()["queued"]
                if before == 0:
                    return
                text = await run(agent, brief_for("apply", APPLY_BRIEF, index=index,
                                                  total=workers, dry_run=dry_run))
                _settle(items, ctx)
                # Progress is measured on the queue, not on ctx: the agent claims
                # while it runs, and a run that leaves the depth unchanged is a stall.
                progress = items.status()["queued"] < before
                async with lock:
                    processed += progress
                    reports.append((index, text))
                stalls = 0 if progress else stalls + 1
                if stalls >= 2 or dry_run:
                    return

    async with asyncio.TaskGroup() as group:
        for index in range(workers):
            group.create_task(one(index))
    return {"workers": workers, "processed": processed, "dry_run": dry_run,
            "reports": [text for _, text in sorted(reports)]}


async def fan_out_search(stores: dict[str, Any], brief: str, *, cfg: Config | None = None,
                         workers: int | None = None, slices: Sequence[str] | None = None,
                         dry_run: bool = False,
                         make_agent: Callable[[WorkerContext], Any] | None = None,
                         run: Callable[[Any, str], Awaitable[str]] | None = None) -> dict[str, Any]:
    cfg = cfg or default_config()
    items: ItemStore = stores["items"]
    work = list(slices) if slices else plan_slices(brief, workers or cfg.max_search_workers)
    semaphore = asyncio.Semaphore(max(1, min(len(work), cfg.max_search_workers)))
    run = run or (lambda agent, text: run_to_completion(agent, text, cfg=cfg))
    results: list[tuple[int, str]] = []

    async def one(index: int, text: str) -> None:
        ctx = WorkerContext(role="search", index=index, dry_run=dry_run, config=cfg)
        async with semaphore, liveness(stores["workers"], ctx, store=items):
            agent = (make_agent or (lambda c: build_search_worker(
                items, c, targets=stores["targets"], cfg=cfg, hosts=stores["hosts"])))(ctx)
            reply = await run(agent, brief_for("search", text, index=index, total=len(work),
                                               dry_run=dry_run))
            results.append((index, reply))

    async with asyncio.TaskGroup() as group:
        for index, text in enumerate(work):
            group.create_task(one(index, text))
    results.sort()
    return {"slices": work, "reports": [text for _, text in results], "dry_run": dry_run}


def merge_reports(search: dict[str, Any], apply_result: dict[str, Any]) -> str:
    lines = ["# Search", ""]
    for index, (slice_text, report) in enumerate(zip(search["slices"], search["reports"])):
        first_line = (slice_text.splitlines() or [""])[0]
        lines += [f"## Angle {index + 1}", first_line, "", report or "(empty)", ""]
    lines += ["# Apply", ""]
    if not apply_result["reports"]:
        lines.append(f"(nothing drained; {apply_result['processed']} items processed)")
    for report in apply_result["reports"]:
        lines += [report or "(empty)", ""]
    return "\n".join(lines).strip()


async def _reap_forever(conn, cfg: Config, stop: asyncio.Event, interval: float = 60.0) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        with contextlib.suppress(Exception):
            reap(conn, cfg)


async def run_supervisor(stores: dict[str, Any], brief: str, *, cfg: Config | None = None,
                         workers: int | None = None, max_items: int | None = None,
                         dry_run: bool = False, browser: bool = True) -> dict[str, Any]:
    """Fan out search, then drain apply, with the reaper running alongside."""
    cfg = cfg or default_config()
    stop = asyncio.Event()
    reaper = asyncio.create_task(_reap_forever(stores["conn"], cfg, stop))
    try:
        search = await fan_out_search(stores, brief, cfg=cfg, workers=workers, dry_run=dry_run)
        applied = await drain_apply(stores, cfg=cfg, max_items=max_items, dry_run=dry_run,
                                    browser=browser)
    finally:
        stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper
    return {"search": search, "apply": applied, "report": merge_reports(search, applied)}
