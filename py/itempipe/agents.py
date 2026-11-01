"""Agent construction: prompts, model, and the two worker kinds.

LangChain/LangGraph is imported lazily so the store, tools and CLI work (and are
testable) without the model stack.
"""

from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import Config, default_config
from .store import AnswerStore, HostLeases, ItemStore, TargetStore
from .tools import make_apply_tools, make_search_tools
from .worker import WorkerContext

PROMPTS = Path(__file__).with_name("prompts")


def parse_prompt(text: str) -> tuple[dict[str, str], str]:
    """Split `--- key: value ---` front matter from the body (no YAML dep)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    for end, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            meta = {}
            for entry in lines[1:end]:
                key, _, value = entry.strip().partition(":")
                if key and value:
                    meta[key] = value.strip()
            return meta, "\n".join(lines[end + 1:]).lstrip("\n")
    return {}, text


def load_prompt(name: str, cfg: Config | None = None) -> tuple[dict[str, str], str]:
    cfg = cfg or default_config()
    meta, body = parse_prompt((PROMPTS / f"{name}.md").read_text(encoding="utf-8"))
    return meta, body.replace("{{CV}}", cfg.cv_text() or "(CV text unavailable)")


def build_model(cfg: Config, meta: dict[str, str] | None = None) -> Any:
    """Per-role model from the prompt front matter, Deepseek is OpenAI-compatible."""
    meta = meta or {}
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as err:  # pragma: no cover
        raise RuntimeError("install the project deps (nix develop) to run agents") from err
    try:
        temperature = float(meta.get("temperature", cfg.temperature))
    except ValueError:
        temperature = cfg.temperature
    return ChatOpenAI(model=meta.get("model") or cfg.model, base_url=cfg.base_url,
                      api_key=cfg.api_key or "missing", temperature=temperature,
                      timeout=180, max_retries=3)


def build_agent(model: Any, tools: Sequence[Any], prompt: str) -> Any:
    try:
        from langgraph.prebuilt import create_react_agent
    except ImportError as err:  # pragma: no cover
        raise RuntimeError("install the project deps (nix develop) to run agents") from err
    return create_react_agent(model, list(tools), prompt=prompt)


def build_apply_worker(items: ItemStore, ctx: WorkerContext, *, cfg: Config | None = None,
                       answers: AnswerStore | None = None, extra_tools: Iterable[Any] = (),
                       model: Any | None = None) -> Any:
    cfg = cfg or items.cfg
    meta, body = load_prompt("apply", cfg)
    tools = [*make_apply_tools(items, ctx, answers=answers, config=cfg), *extra_tools]
    return build_agent(model or build_model(cfg, meta), tools, body)


def build_search_worker(items: ItemStore, ctx: WorkerContext, *, targets: TargetStore,
                        cfg: Config | None = None, hosts: HostLeases | None = None,
                        with_web: bool = True, model: Any | None = None) -> Any:
    cfg = cfg or items.cfg
    meta, body = load_prompt("search", cfg)
    tools = make_search_tools(items, ctx, targets=targets, config=cfg, hosts=hosts,
                              with_web=with_web)
    return build_agent(model or build_model(cfg, meta), tools, body)


async def run_to_completion(agent: Any, brief: str, *, cfg: Config | None = None) -> str:
    """Sub-agents cannot ask questions: only the last AI message comes back."""
    cfg = cfg or default_config()
    result = await agent.ainvoke({"messages": [{"role": "user", "content": brief}]},
                                 config={"recursion_limit": cfg.recursion_limit})
    messages = result.get("messages", []) if isinstance(result, dict) else list(result)
    for message in reversed(messages):
        kind = getattr(message, "type", None) or (
            message.get("type") if isinstance(message, dict) else None)
        if kind not in ("ai", "assistant"):
            continue
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p)
                                for p in content)
        return str(content or "").strip()
    return ""


def brief_for(role: str, text: str, *, index: int, total: int, dry_run: bool) -> str:
    """The self-contained brief a sub-agent sees (it sees nothing else)."""
    verb = "report what you recorded and what was a duplicate" if role == "search" else \
        "work items one at a time until the queue is empty or your share is done"
    suite = " This is a dry run: nothing may be sent or submitted." if dry_run else ""
    return (f"You are {role} worker {index + 1} of {total}. {verb}.{suite}\n\n{text}").strip()
