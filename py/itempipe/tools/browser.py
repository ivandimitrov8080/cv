"""Per-worker Playwright MCP client.

Two hard rules from the Emacs version: one MCP server (one Chromium) per worker,
always with its own `--user-data-dir` (two servers sharing a profile directory
fail to start), and never more than `browser_slots` Chromiums at once.
"""

import asyncio
import shutil
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from ..config import Config
from ..worker import WorkerContext

# One semaphore per event loop: a Semaphore binds to the loop that first uses it.
_slots: "weakref.WeakKeyDictionary[Any, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _semaphore(cfg: Config) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _slots.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, int(cfg.browser_slots)))
        _slots[loop] = semaphore
    return semaphore


def profile_dir(cfg: Config, ctx: WorkerContext) -> Path:
    path = Path(cfg.profile_dir) / f"{ctx.role}-{ctx.index}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_config(cfg: Config, ctx: WorkerContext) -> dict[str, Any]:
    args = ["--headless", "--user-data-dir", str(profile_dir(cfg, ctx))]
    executable = cfg.chromium or shutil.which("chromium-sandboxed") or shutil.which("chromium")
    if executable:
        args += ["--executable-path", executable]
    return {"playwright": {"command": "playwright-mcp", "args": args, "transport": "stdio"}}


@asynccontextmanager
async def browser_slot(cfg: Config, ctx: WorkerContext) -> AsyncIterator[None]:
    """Cap concurrent Chromiums; a dry run never starts one."""
    if ctx.dry_run:
        yield
        return
    async with _semaphore(cfg):
        yield


async def make_browser_tools(cfg: Config, ctx: WorkerContext) -> list[Any]:
    """Tools from this worker's own Playwright MCP server."""
    if ctx.dry_run:
        return []
    from langchain_mcp_adapters.client import MultiServerMCPClient

    return list(await MultiServerMCPClient(server_config(cfg, ctx)).get_tools())
