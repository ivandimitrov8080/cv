import asyncio
import contextlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import StructuredTool

TAB_LINE = re.compile(r"^\s*-\s*(\d+):\s*(.*)$", re.M)
HIDDEN = frozenset({"browser_tabs", "browser_close"})


def field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def text_of(result):
    parts = []
    for block in field(result, "content") or ():
        if (text := field(block, "text")) is not None:
            parts.append(str(text))
        else:
            parts.append(f"[{field(block, 'type', 'other')}]")
    return "\n".join(part for part in parts if part).strip()


def failed(result):
    return bool(field(result, "is_error") or field(result, "isError"))


def tabs_in(listing):
    return [int(match.group(1)) for match in TAB_LINE.finditer(listing or "")]


def current_tab(listing):
    for match in TAB_LINE.finditer(listing or ""):
        if "(current)" in match.group(2):
            return int(match.group(1))
    return None


@dataclass
class Tab:
    browser: "Browser"
    index: int


class Browser:
    def __init__(self, client, remote, limit=20000):
        self.client = client
        self.remote = list(remote)
        self.limit = limit
        self.lock = asyncio.Lock()
        self.started = False

    async def call(self, action, **arguments):
        result = await self.client.call_tool("browser_tabs", {"action": action, **arguments})
        if failed(result):
            raise RuntimeError(text_of(result) or f"browser_tabs {action} failed")
        return text_of(result)

    async def claim(self):
        if not any(field(tool, "name") == "browser_tabs" for tool in self.remote):
            raise RuntimeError("playwright-mcp offers no browser_tabs; one tab per worker "
                               "cannot be guaranteed")
        async with self.lock:
            if not self.started:
                found = tabs_in(await self.call("list"))
                if found:
                    await self.call("select", index=found[0])
                    self.started = True
                    return Tab(self, found[0])
            await self.call("new")
            listing = await self.call("list")
            index = current_tab(listing)
            if index is None:
                found = tabs_in(listing)
                if not found:
                    raise RuntimeError("playwright-mcp opened no tab")
                index = found[-1]
            self.started = True
            return Tab(self, index)

    async def run(self, tab, name, arguments):
        async with self.lock:
            await self.call("select", index=tab.index)
            result = await self.client.call_tool(name, arguments)
            with contextlib.suppress(Exception):
                moved = current_tab(await self.call("list"))
                if moved is not None:
                    tab.index = moved
            return result

    def wrap(self, tool, tab):
        name = field(tool, "name")
        schema = (field(tool, "input_schema") or field(tool, "inputSchema")
                  or {"type": "object", "properties": {}})

        async def call(**arguments):
            try:
                result = await self.run(tab, name, arguments)
            except Exception as exc:
                return f"NOT done: {type(exc).__name__}: {exc}"
            text = text_of(result)
            if failed(result):
                return f"NOT done: {text or 'the tool failed without saying why'}"
            if len(text) > self.limit:
                text = text[: self.limit] + "\n[truncated]"
            return text or "The tool ran but returned no text."

        return StructuredTool(name=name, description=field(tool, "description") or name,
                              args_schema=schema, coroutine=call)

    def tools(self, tab):
        return [self.wrap(tool, tab) for tool in self.remote
                if field(tool, "name") not in HIDDEN]


def params():
    from mcp import StdioServerParameters
    arguments = []
    if os.environ.get("WEBPIPE_CHROME"):
        arguments += ["--executable-path", os.environ["WEBPIPE_CHROME"]]
    if os.environ.get("WEBPIPE_HEADLESS"):
        arguments += ["--headless"]
    return StdioServerParameters(command="playwright-mcp", args=arguments,
                                 env=dict(os.environ))


@contextlib.asynccontextmanager
async def browser(config):
    from mcp import Client, stdio_client
    Path(config.act.profile).mkdir(parents=True, exist_ok=True)
    Path(config.act.mcp_log).parent.mkdir(parents=True, exist_ok=True)
    with open(config.act.mcp_log, "a", encoding="utf-8", errors="replace") as errlog:
        async with contextlib.AsyncExitStack() as stack:
            try:
                client = await stack.enter_async_context(
                    Client(stdio_client(params(), errlog=errlog)))
                remote = await client.list_tools()
            except Exception as exc:
                raise RuntimeError(
                    f"playwright-mcp could not start: {type(exc).__name__}: {exc} "
                    f"(log: {config.act.mcp_log})") from exc
            yield Browser(client, remote.tools, config.act.mcp_output)
