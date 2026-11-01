"""Answer tools: `lookup_answer` is read-only and safe under any concurrency."""

from typing import Any

from ..store import AnswerStore, ItemStore
from ..worker import WorkerContext
from .base import make_tool

LOOKUP = """Look up the user's pre-written answers to application questions. Call this
before filling any required free-text field. Pass the 2 most meaningful words of
the form's question, not its full wording, e.g. "salary expectation" or "notice
period": the stored questions are worded differently, so keywords match where
sentences do not. The result is plain text: numbered questions, each followed by
its answer. Use the answer as written, substituting placeholders like <Company>
or <N> days. If nothing matches, do not invent an answer: call
pause_item_for_answer instead."""


def make_answer_tools(answers: AnswerStore, items: ItemStore, ctx: WorkerContext,
                      *, config=None) -> list[Any]:
    @make_tool("lookup_answer", "lookup_answer", LOOKUP)
    def lookup_answer(query: str = "") -> str:
        return answers.search(query or None)

    @make_tool("pause_item_for_answer", "pause_item_for_answer",
               "Put the claimed item on hold when a required free-text field asks "
               "something lookup_answer has no answer for. Pass the question verbatim. "
               "It is saved for the user and the item leaves the queue until answered. "
               "Never invent an answer.")
    def pause_item_for_answer(question: str) -> str:
        if problem := ctx.require_claim():
            return problem
        if ctx.dry_run:
            item_id = ctx.item_id
            if ctx.claimed:
                items.release(int(ctx.item_id), str(ctx.claim_token), "dry run: pause")
            ctx.clear()
            return f"DRY RUN: would save the question unanswered and pause item {item_id}: {question}"
        ok, message = items.pause(int(ctx.item_id), str(ctx.claim_token), question)
        if ok:
            ctx.clear()
        return message

    return [lookup_answer, pause_item_for_answer]
