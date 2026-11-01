"""Tool plumbing: a tool returns a string and never raises.

The prompts are written against that contract — a tool that raises is a failure
the model never sees.  `make_tool` therefore stacks langchain's `@tool` on a
guard that turns every exception into `NOT done (<step>): <err>`, keeping the
step name so msmtp/sqlite/browser failures stay debuggable.
"""

import functools
import inspect
from typing import Any, Callable

from langchain_core.tools import BaseTool
from langchain_core.tools import tool as _tool


def guard(step: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def ainner(*args: Any, **kwargs: Any) -> str:
                try:
                    return await fn(*args, **kwargs)
                except Exception as err:  # noqa: BLE001 - contract: return, don't raise
                    return f"NOT done ({step}): {err}"

            return ainner

        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> str:
            try:
                return fn(*args, **kwargs)
            except Exception as err:  # noqa: BLE001
                return f"NOT done ({step}): {err}"

        return inner

    return decorate


def make_tool(step: str, name: str, description: str = "") -> Callable[[Callable[..., Any]], BaseTool]:
    """`@make_tool("advance_item", "advance_item", "Mark … done.")`.

    NOTE: tool modules must not use `from __future__ import annotations`, or the
    argument annotations arrive as strings and pydantic cannot build the schema.
    """

    def decorate(fn: Callable[..., Any]) -> BaseTool:
        return _tool(name, description=description or inspect.getdoc(fn) or name)(
            guard(step)(fn)
        )

    return decorate
