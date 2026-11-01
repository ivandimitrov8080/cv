"""Templated application email.

The body is never generated here: only `{{position}}` and `{{company}}` are
substituted, and the failing *step* is part of the returned string because that
is what made msmtp failures debuggable.

At-most-once: the `submitting` attempt is written *before* msmtp runs, so a crash
mid-send lands the item in review instead of sending a second copy.
"""

import asyncio
import mimetypes
import re
import smtplib
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ..config import Config
from ..store import ItemStore
from ..worker import WorkerContext
from .base import make_tool

_PLACEHOLDER = re.compile(r"\{\{([a-z]+)\}\}")

# One process sends one email at a time, spaced by email_min_interval.
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None
_last_sent = 0.0


class EmailError(Exception):
    def __init__(self, step: str, err: Any):
        super().__init__(str(err))
        self.step, self.err = step, err


def _fill(text: str, mapping: dict[str, str], step: str) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in mapping:
            raise EmailError(step, f"unsupported placeholder {{{{{key}}}}} in the email template")
        return mapping[key]

    return _PLACEHOLDER.sub(replace, text)


def render(cfg: Config, *, email: str, name: str, title: str,
           subject_override: str | None = None) -> EmailMessage:
    """Build the MIME message; raises EmailError(step, err) with the failing step."""
    if "@" not in (email or ""):
        raise EmailError("reading the template", f"not an email address: {email!r}")
    try:
        text = Path(cfg.email_template).read_text(encoding="utf-8")
    except OSError as err:
        raise EmailError("reading the template", err) from err
    if "<<<" in text:
        raise EmailError("reading the template",
                         f"write your application email in {cfg.email_template} first")

    step = "parsing the template"
    first_line, _, body_text = text.partition("\n")
    if not first_line.startswith("Subject:"):
        raise EmailError(step, f"the first line of {cfg.email_template} must be `Subject: ...`")
    values = {"position": title, "company": name}
    subject = _fill(subject_override or first_line[len("Subject:"):].strip(), values, step)
    body = _fill(body_text, values, step)

    message = EmailMessage()
    message["To"] = email
    message["Subject"] = subject
    message.set_content(body)
    for path in cfg.attachments:
        try:
            data = Path(path).read_bytes()
        except OSError as err:
            raise EmailError("attaching the CV", f"attachment {path} is missing ({err})") from err
        maintype, _, subtype = (mimetypes.guess_type(path)[0] or "application/octet-stream").partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                               filename=Path(path).name)
    return message


def deliver(cfg: Config, message: EmailMessage) -> None:
    """msmtp -t by default (no new credentials); SMTP_SSL only when configured."""
    if cfg.smtp_host:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port) as smtp:
            smtp.send_message(message)
        return
    result = subprocess.run(["msmtp", "-a", cfg.msmtp_account, "-t"],
                            input=message.as_bytes(), capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip() or f"msmtp exited {result.returncode}"
        raise RuntimeError(detail)


def _loop_lock() -> asyncio.Lock:
    """A lock bound to the *current* loop (CLIs and tests may run several)."""
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


async def _wait_turn(cfg: Config) -> None:
    """Space sends out: parallel workers sending at once is a spam signal."""
    global _last_sent
    remaining = float(cfg.email_min_interval) - (time.monotonic() - _last_sent)
    if _last_sent and remaining > 0:
        await asyncio.sleep(remaining)


SEND = """Send the user's pre-written application email to an application address
found on the site. Only the position and company are filled in; never write or
alter the body. Prefer this over an application form."""


def make_email_tools(items: ItemStore, ctx: WorkerContext, *, config: Config | None = None) -> list[Any]:
    cfg = config or items.cfg

    @make_tool("send_templated_email", "send_templated_email", SEND)
    async def send_templated_email(email: str, name: str, title: str,
                                   subject_override: str = "") -> str:
        global _last_sent
        if problem := ctx.require_claim():
            return problem
        try:
            message = render(cfg, email=email, name=name, title=title,
                             subject_override=subject_override or None)
        except EmailError as err:
            return f"Application email NOT sent ({err.step}): {err.err}"

        if ctx.dry_run:
            names = ", ".join(Path(p).name for p in cfg.attachments) or "none"
            return (f"DRY RUN: rendered MIME to {email}, subject {message['Subject']!r}, "
                    f"attachments: {names}. Nothing sent.")

        item_id, token = int(ctx.item_id), str(ctx.claim_token)
        ok, message_guard = items.begin_submission(item_id, token, ctx.worker_id)
        if not ok:
            return message_guard
        try:
            async with _loop_lock():
                await _wait_turn(cfg)
                await asyncio.to_thread(deliver, cfg, message)
                _last_sent = time.monotonic()
        except Exception as err:  # noqa: BLE001 - reported, never raised
            return f"Application email NOT sent (sending): {err}"
        items.mark_sent(item_id, ctx.worker_id)
        return f"Application email sent to {email} for {title} at {name}."

    return [send_templated_email]


def reset_throttle() -> None:
    """Tests only: forget the last send time and the loop-bound lock."""
    global _last_sent, _lock, _lock_loop
    _last_sent, _lock, _lock_loop = 0.0, None, None
