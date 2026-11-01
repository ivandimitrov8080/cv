"""Web tools: search (`ddgs`) and page extraction (`httpx` + `trafilatura`)."""

import asyncio
from typing import Any
from urllib.parse import urlsplit

from ..config import Config
from .base import make_tool

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or url).lower()
    except ValueError:
        return url.lower()


def search_sync(query: str, max_results: int = 8) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover - older package name
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
    with DDGS() as client:
        return list(client.text(query, max_results=max_results) or [])


async def extract(url: str, *, max_chars: int) -> str:
    import httpx

    async with httpx.AsyncClient(follow_redirects=True, timeout=25.0,
                                 headers={"User-Agent": _UA}) as client:
        response = await client.get(url)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", ""):
            return response.text[:max_chars]
        html = response.text

    def readable() -> str | None:
        try:
            import trafilatura

            return trafilatura.extract(html, include_links=False, favor_recall=True)
        except Exception:
            return None

    text = await asyncio.to_thread(readable) or html
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())[:max_chars]


def make_web_tools(*, config: Config, hosts: Any = None, holder: str = "web") -> list[Any]:
    cfg = config

    @make_tool("web_search", "web_search",
               "Search the web. Returns result titles, URLs and snippets; follow up with "
               "fetch_page on the individual posting, not on the results page.")
    async def web_search(query: str) -> str:
        results = await asyncio.to_thread(search_sync, query)
        if not results:
            return f"No results for {query!r}."
        return "\n\n".join(
            "\n".join(filter(None, ((r.get("title") or "").strip(),
                                    (r.get("href") or r.get("url") or "").strip(),
                                    (r.get("body") or r.get("snippet") or "").strip())))
            for r in results)

    @make_tool("fetch_page", "fetch_page",
               "Fetch one URL and return its readable text, truncated. Use it to verify a "
               "posting is still open before recording it.")
    async def fetch_page(url: str) -> str:
        host = host_of(url)
        if hosts is None:
            return await extract(url, max_chars=cfg.max_page_chars)
        # Host politeness: one worker per origin at a time.
        for _ in range(20):
            if hosts.acquire(host, holder, ttl_seconds=120):
                try:
                    return await extract(url, max_chars=cfg.max_page_chars)
                finally:
                    hosts.release(host, holder)
            await asyncio.sleep(1.0)
        return f"NOT done (host lease): another worker is already on {host}; try again shortly."

    return [web_search, fetch_page]
