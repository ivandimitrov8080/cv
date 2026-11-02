"""Two agents, as many copies as you like.

`search(brief, n)` fans out n independent searches. `apply(store, n)` runs n
workers that claim from the same queue until it is empty. Both are plain
asyncio; one SQLite file is the only coordination.
"""

import asyncio
import shutil
from pathlib import Path

from .config import Config, cv_text
from .store import Store, Worker
from .tools import apply_tools, search_tools

PROMPTS = Path(__file__).with_name("prompts")
CHANNELS = ('HN "Who is hiring?" comments', "YC Work at a Startup", "niche vertical boards",
            "company /careers pages with a mailto", "practitioner communities",
            "B2B contract / freelance")
APPLY_BRIEF = ("Claim one item with claim_next_item, work it to a final state "
               "(advance_item, pause_item_for_answer, archive_item or release_item), then "
               "stop. If a tool says the item is no longer yours, stop immediately and say so.")


def _prompt(name: str, cfg: Config) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return text.replace("{{CV}}", cv_text(cfg))


def _agent(prompt: str, tools: list, cfg: Config):
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    model = ChatOpenAI(model=cfg.model, base_url=cfg.base_url,
                       api_key=cfg.api_key or "missing", temperature=0.2,
                       timeout=180, max_retries=3)
    return create_react_agent(model, tools, prompt=prompt)


async def _run(agent, text: str, cfg: Config) -> str:
    result = await agent.ainvoke({"messages": [{"role": "user", "content": text}]},
                                 config={"recursion_limit": cfg.recursion_limit})
    content = result["messages"][-1].content
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") if isinstance(part, dict) else str(part)
                            for part in content)
    return str(content).strip()


def _slice(brief: str, index: int, total: int) -> str:
    channel = CHANNELS[index % len(CHANNELS)]
    return f"{brief}\n\nAngle {index + 1} of {total}: {channel}. Stay on it."


async def search(store: Store, brief: str, cfg: Config, workers: int | None = None,
                 dry_run: bool = False) -> list[str]:
    total = max(1, workers or cfg.search_workers)
    prompt = _prompt("search", cfg)

    async def one(index: int) -> str:
        tools = search_tools(store, cfg, dry_run)
        return await _run(_agent(prompt, tools, cfg), _slice(brief, index, total), cfg)

    return list(await asyncio.gather(*(one(index) for index in range(total))))


async def _browser_tools(cfg: Config, index: int) -> list:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    profile = Path(cfg.profile_dir) / f"apply-{index}"
    profile.mkdir(parents=True, exist_ok=True)
    args = ["--headless", "--user-data-dir", str(profile)]
    executable = cfg.chromium or shutil.which("chromium-sandboxed") or shutil.which("chromium")
    if executable:
        args += ["--executable-path", executable]
    client = MultiServerMCPClient(
        {"playwright": {"command": "playwright-mcp", "args": args, "transport": "stdio"}})
    return list(await client.get_tools())


async def apply(store: Store, cfg: Config, workers: int | None = None,
                dry_run: bool = False) -> list[str]:
    total = max(1, workers or cfg.apply_workers)
    store.reap()
    if store.queued() == 0:
        return []
    slots = asyncio.Semaphore(max(1, cfg.browser_slots))
    prompt = _prompt("apply", cfg)
    reports: list[str] = []

    async def one(index: int) -> None:
        worker = Worker("apply", index, dry_run)
        async with slots:
            tools = apply_tools(store, worker, cfg)
            if not dry_run:
                tools += await _browser_tools(cfg, index)
            agent = _agent(prompt, tools, cfg)
            stalls = 0
            for _ in range(100):
                before = store.queued()
                if before == 0:
                    return
                try:
                    reports.append(await _run(agent, APPLY_BRIEF, cfg))
                finally:
                    store.settle(worker)
                stalls = 0 if store.queued() < before else stalls + 1
                if stalls >= 2:
                    return

    await asyncio.gather(*(one(index) for index in range(total)))
    return reports
