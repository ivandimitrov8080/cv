"""Research-target tools: the search-side shared TODO.

N searchers must not research the same subject twice; the `targets` table is the
lock, and an unusable target tells every other searcher to skip it.
"""

from typing import Any

from ..store import TargetStore
from ..worker import WorkerContext
from .base import make_tool


def make_target_tools(targets: TargetStore, ctx: WorkerContext, *, config=None) -> list[Any]:
    @make_tool("claim_research_target", "claim_research_target",
               "Call this before researching a subject. It atomically marks the subject "
               "as being researched so other workers skip it. If it reports that nothing "
               "could be claimed, pick another subject.")
    def claim_research_target(name: str) -> str:
        ok, message = targets.claim(name, ctx.worker_id)
        return message if ok else f"Nothing to claim for {name}: {message} Pick another subject."

    @make_tool("mark_target_unusable", "mark_target_unusable",
               "Mark a subject unusable with a short reason (posting closed, not hiring, "
               "irrelevant) so other searchers do not spend time on it.")
    def mark_target_unusable(name: str, reason: str) -> str:
        return targets.mark_unusable(name, reason)

    return [claim_research_target, mark_target_unusable]
