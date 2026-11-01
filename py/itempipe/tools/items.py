"""Item tools: create, claim, advance, release, archive, submission guard, list.

The model sees parameterless signatures for the fenced operations; `item_id`
and `claim_token` come from the per-worker closure, never from the model.
"""

from typing import Any

from ..config import Config
from ..store import ItemStore
from ..worker import WorkerContext
from .base import make_tool

CREATE = """Record one subject worth working on, with the exact title and the http(s)
URL of that individual posting. Call this the moment you decide it is worth
pursuing: a lead that is not recorded is lost. Give the name, the exact
advertised title, and the URL of that specific posting -- never a search-results
page, never a board's front page. Names already tracked, in any state, are
refused with that state reported: treat such a refusal as a duplicate, report
it, and do not retry it under a different spelling of the name."""


def make_item_tools(store: ItemStore, ctx: WorkerContext, *, config: Config | None = None,
                    dry_run: bool | None = None) -> list[Any]:
    cfg = config or store.cfg
    dry = ctx.dry_run if dry_run is None else dry_run

    def drop_claim(reason: str) -> None:
        """A rehearsal must not leave a row locked: undo the claim it made."""
        if ctx.claimed:
            store.release(int(ctx.item_id), str(ctx.claim_token), reason)
        ctx.clear()

    @make_tool("create_item", "create_item", CREATE)
    def create_item(name: str, title: str, url: str, location: str = "",
                    contact: str = "", notes: str = "", size: str = "",
                    language: str = "") -> str:
        return store.create(
            name=name, title=title, url=url, location=location, contact=contact,
            notes=notes, size=size, language=language, dry_run=dry,
        )["message"]

    @make_tool("claim_next_item", "claim_next_item",
               "Return the next item from the pipeline with its state, url, location, "
               "size, language and contact. Call this first to choose what to work on.")
    def claim_next_item() -> str:
        if ctx.claimed:
            return (f"Already holding item {ctx.item_id}; finish it with advance_item, "
                    "release_item or archive_item before claiming another.")
        item = store.claim_next(ctx.worker_id)
        if item is None:
            return f"No {cfg.initial_state} items left in the pipeline."
        ctx.adopt(item)
        return store.format_item(item)

    @make_tool("advance_item", "advance_item",
               "Mark the item you claimed as successfully completed and schedule the "
               "follow-up. Call once the application has been submitted.")
    def advance_item() -> str:
        if problem := ctx.require_claim():
            return problem
        if dry:
            item = store.get(int(ctx.item_id)) or {}
            name, title = item.get("name", "?"), item.get("title", "?")
            drop_claim("dry run: advance_item")
            return (f"DRY RUN: would mark {name} — {title} as {cfg.success_state} and "
                    f"schedule a follow-up in {cfg.follow_up_days} days.")
        ok, message = store.advance(int(ctx.item_id), str(ctx.claim_token),
                                    worker_id=ctx.worker_id)
        if ok:
            ctx.clear()
        return message

    @make_tool("release_item", "release_item",
               "Return the claimed item to the queue after a retryable failure (browser "
               "died, page timed out, site unreachable). Never use this after an "
               "irreversible step that may have run. The reason is logged.")
    def release_item(reason: str) -> str:
        if problem := ctx.require_claim():
            return problem
        if dry:
            item_id = ctx.item_id
            drop_claim(f"dry run: release_item ({reason})")
            return f"DRY RUN: would release item {item_id} back to the queue: {reason}"
        ok, message = store.release(int(ctx.item_id), str(ctx.claim_token), reason)
        if ok:
            ctx.clear()
        return message

    @make_tool("archive_item", "archive_item",
               "Archive the claimed item with a reason when the work cannot be completed. "
               "Use this instead of advance_item.")
    def archive_item(reason: str = "") -> str:
        if problem := ctx.require_claim():
            return problem
        if dry:
            name = (store.get(int(ctx.item_id)) or {}).get("name", "?")
            drop_claim(f"dry run: archive_item ({reason or 'abandoned'})")
            return f"DRY RUN: would archive {name}: {reason or 'abandoned'}"
        ok, message = store.archive(int(ctx.item_id), token=str(ctx.claim_token), reason=reason)
        if ok:
            ctx.clear()
        return message

    @make_tool("begin_submission", "begin_submission",
               "Record that the irreversible final submit is about to run. Call it "
               "immediately before the final submit action, so a crashed worker leaves "
               "the item in review instead of applying twice.")
    def begin_submission() -> str:
        if problem := ctx.require_claim():
            return problem
        if dry:
            return "DRY RUN: would record the submission guard; nothing was sent."
        _, message = store.begin_submission(int(ctx.item_id), str(ctx.claim_token), ctx.worker_id)
        return message

    @make_tool("list_items", "list_items",
               "List tracked items as `Name — STATE` lines (archived rows excluded). Use "
               "this instead of any inline copy of the tracker.")
    def list_items(states: str = "", limit: int = 200) -> str:
        wanted = [s.strip().upper() for s in states.replace(",", " ").split() if s.strip()]
        rows = store.list_items(states=wanted or None, limit=int(limit))
        if not rows:
            return f"No items in {'/'.join(wanted) if wanted else 'any state'}."
        return "\n".join(f"{row['name']} — {row['state']}" for row in rows)

    return [create_item, claim_next_item, advance_item, release_item, archive_item,
            begin_submission, list_items]
