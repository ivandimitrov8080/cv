"""Per-worker tool closures.

`make_apply_tools(store, ctx)` / `make_search_tools(...)` return tools bound to
one `WorkerContext`. No module-level globals: K workers share a process, and the
claim token is supplied by the closure, never by the model.
"""

from typing import Any

from ..config import Config
from ..store import AnswerStore, HostLeases, ItemStore, TargetStore
from ..worker import WorkerContext
from .answers import make_answer_tools
from .email import make_email_tools
from .items import make_item_tools
from .targets import make_target_tools
from .web import make_web_tools

APPLY_ITEMS = {"claim_next_item", "advance_item", "release_item", "archive_item", "begin_submission"}
SEARCH_ITEMS = {"create_item", "list_items"}


def _only(tools: list[Any], names: set[str]) -> list[Any]:
    return [tool for tool in tools if tool.name in names]


def make_apply_tools(items: ItemStore, ctx: WorkerContext, *, answers: AnswerStore | None = None,
                     config: Config | None = None) -> list[Any]:
    cfg = config or items.cfg
    return [
        *_only(make_item_tools(items, ctx, config=cfg), APPLY_ITEMS),
        *make_answer_tools(answers or AnswerStore(items.conn, cfg), items, ctx),
        *make_email_tools(items, ctx, config=cfg),
    ]


def make_search_tools(items: ItemStore, ctx: WorkerContext, *, targets: TargetStore,
                      config: Config | None = None, hosts: HostLeases | None = None,
                      with_web: bool = True) -> list[Any]:
    cfg = config or items.cfg
    tools = _only(make_item_tools(items, ctx, config=cfg), SEARCH_ITEMS)
    tools += make_target_tools(targets, ctx)
    if with_web:
        tools += make_web_tools(config=cfg, hosts=hosts, holder=ctx.worker_id)
    return tools
