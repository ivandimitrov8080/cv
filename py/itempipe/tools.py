"""The tools the two agents get.

A tool never raises: the prompt can only react to strings, so every failure is
returned as `NOT done (tool_name): ...`. Item ids and claim tokens come from the
per-worker closure, never from the model.
"""

import asyncio
import functools
import inspect
import mimetypes
import re
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path

from langchain_core.tools import tool as _tool

from .config import Config
from .store import FENCED, Store, Worker, line

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_send_lock: asyncio.Lock | None = None
_last_sent = 0.0


def tool(fn):
    """Return a LangChain tool that reports exceptions instead of raising."""
    @functools.wraps(fn)
    async def guarded(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        except Exception as err:  # noqa: BLE001 - contract: return, don't raise
            return f"NOT done ({fn.__name__}): {err}"

    return _tool(fn.__name__, description=(fn.__doc__ or fn.__name__).strip())(guarded)


# -- web -------------------------------------------------------------------
def web_search_sync(query: str) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as client:
        return list(client.text(query, max_results=8) or [])


async def fetch_text(url: str, max_chars: int) -> str:
    import httpx

    async with httpx.AsyncClient(follow_redirects=True, timeout=25.0,
                                 headers={"User-Agent": _UA}) as client:
        response = await client.get(url)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", ""):
            return response.text[:max_chars]
        html = response.text

    def readable() -> str:
        try:
            import trafilatura

            return trafilatura.extract(html, include_links=False, favor_recall=True) or html
        except Exception:
            return html

    text = await asyncio.to_thread(readable)
    return "\n".join(part for part in (line.strip() for line in text.splitlines()) if part)[:max_chars]


def search_tools(store: Store, cfg: Config, dry_run: bool = False) -> list:
    @tool
    def create_item(name: str, title: str, url: str, notes: str = "") -> str:
        """Record one company worth applying to: exact job title, the posting's own
        http(s) URL, and why it fits in notes. Duplicates are refused with their state."""
        return store.create(name, title, url, notes, dry_run=dry_run)

    @tool
    def list_items(state: str = "") -> str:
        """List tracked leads as `Name — STATE` lines. Filter with e.g. "NEW,ACTIVE"."""
        wanted = state.replace(",", " ").upper().split()
        rows = [row for row in store.rows() if not wanted or row["state"] in wanted]
        return "\n".join(f"{row['name']} — {row['state']}" for row in rows) or "Nothing tracked."

    @tool
    async def web_search(query: str) -> str:
        """Search the web; follow up with fetch_page on the individual posting."""
        results = await asyncio.to_thread(web_search_sync, query)
        return "\n\n".join("\n".join(filter(None, (r.get("title"), r.get("href"), r.get("body"))))
                           for r in results) or f"No results for {query!r}."

    @tool
    async def fetch_page(url: str) -> str:
        """Fetch one URL as readable text, truncated. Use it to verify a posting is open."""
        return await fetch_text(url, cfg.max_page_chars)

    return [create_item, list_items, web_search, fetch_page]


# -- email -----------------------------------------------------------------
def _render(cfg: Config, email: str, name: str, title: str) -> EmailMessage:
    text = Path(cfg.email_template).read_text(encoding="utf-8")
    if "<<" in text:
        raise RuntimeError(f"write your application email in {cfg.email_template} first")
    first, _, body = text.partition("\n")
    if not first.startswith("Subject:"):
        raise RuntimeError(f"the first line of {cfg.email_template} must be `Subject: ...`")
    values = {"position": line(title), "company": line(name)}
    fill = lambda value: _PLACEHOLDER.sub(lambda m: values[m.group(1)], value)  # noqa: E731
    message = EmailMessage()
    message["To"] = line(email)
    message["Subject"] = fill(first[len("Subject:"):].strip())
    message.set_content(fill(body))
    for path in cfg.attachments:
        data = Path(path).read_bytes()
        maintype, _, subtype = (mimetypes.guess_type(path)[0] or
                                "application/octet-stream").partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=Path(path).name)
    return message


def _deliver(cfg: Config, message: EmailMessage) -> None:
    result = subprocess.run(["msmtp", "-a", cfg.msmtp_account, "-t"],
                            input=message.as_bytes(), capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or
                           f"msmtp exited {result.returncode}")


async def _send(cfg: Config, message: EmailMessage) -> None:
    global _send_lock, _last_sent
    _send_lock = _send_lock or asyncio.Lock()
    async with _send_lock:
        wait = _last_sent + cfg.email_min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        await asyncio.to_thread(_deliver, cfg, message)
        _last_sent = time.monotonic()


def apply_tools(store: Store, worker: Worker, cfg: Config) -> list:
    @tool
    def claim_next_item() -> str:
        """Claim the next NEW lead and return it with its URL. Call this first."""
        return store.claim(worker)

    @tool
    def lookup_answer(query: str = "") -> str:
        """Look up the user's pre-written answers; pass 2 meaningful keywords, not the
        form's full wording. Never invent an answer."""
        return store.lookup(query)

    @tool
    def pause_item_for_answer(question: str) -> str:
        """Save a required question the user must answer, and take this item out of the
        queue until they do. Pass the question verbatim."""
        return store.pause(worker, question)

    @tool
    def begin_submission() -> str:
        """Call immediately before the final submit (or application email). If this
        worker dies mid-submit, the item goes to review instead of applying twice."""
        return store.begin_submission(worker)

    @tool
    def advance_item() -> str:
        """Mark the claimed lead successfully applied to, after begin_submission."""
        return store.advance(worker)

    @tool
    def release_item(reason: str) -> str:
        """Retry later: browser died, page timed out, site unreachable. Never after an
        irreversible step."""
        return store.release(worker, reason)

    @tool
    def archive_item(reason: str = "") -> str:
        """Archive the lead when it cannot be applied to (closed, wrong fit, multiple
        jobs, etc.). Give a short reason."""
        return store.archive(worker, reason)

    @tool
    async def send_templated_email(email: str, name: str, title: str) -> str:
        """Send the user's pre-written application email. Only company and position are
        filled in; never alter the body. Prefer a real application form."""
        if worker.dry_run:
            return f"DRY RUN: would email {email} about {title} at {name}."
        try:
            message = _render(cfg, email, name, title)
        except Exception as err:  # noqa: BLE001
            return f"Application email NOT sent (rendering): {err}"
        guard = store.begin_submission(worker)
        if guard == FENCED:
            return guard
        try:
            await _send(cfg, message)
        except Exception as err:  # noqa: BLE001
            return f"Application email NOT sent (sending): {err}"
        return f"Application email sent to {email} for {title} at {name}."

    return [claim_next_item, lookup_answer, pause_item_for_answer, begin_submission,
            advance_item, release_item, archive_item, send_templated_email]
