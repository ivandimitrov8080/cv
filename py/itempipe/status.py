"""A live progress board, drawn on stderr.

The interface is a handful of module-level functions, one board per process:

    open_board("itempipe apply", counts=tally)
    worker("w1", "call", "fetch_page {'url': ...}")
    worker("w1", "result", "...", state="applying")
    finished("w1", "Acme: applied")
    refresh()                   # re-read the counts, tick the clock
    close_board()

`counts` is any callable returning `{state: count}`, so this module never imports the
database or anything else from the project. Every function is a no-op before
`open_board`, so callers never have to check whether the board exists.

With no tty, or without `rich` installed, the same calls print plain lines on stderr;
stdout is never touched, so it stays free for the actual report.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from typing import Callable

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.table import Table
    from rich.text import Text

    RICH = True
except ImportError:                     # a nicety, never a hard dependency
    RICH = False

ICON = {"call": "→", "result": "←", "state": "·"}
LABEL = {"NEW": "new", "WORKING": "working", "ACTIVE": "active", "HOLD": "hold",
         "NEEDS_REVIEW": "review"}

_lock = threading.Lock()                # guards _rows, _queue: Live renders on its own thread
_rows: dict[str, dict] = {}
_counts: Callable[[], dict[str, int]] | None = None
_queue, _title = "queue: …", "itempipe"
_t0, _open, _drawing = 0.0, False, False
_console = _live = None


def _clock(seconds):
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _short(value, limit=96):
    text = re.sub(r"\s+", " ", value if isinstance(value, str) else str(value)).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _row(tag):
    return _rows.setdefault(tag, {"state": "starting", "what": "…", "tools": 0,
                                 "t0": time.monotonic()})


def _panel(final=False):
    """One frame: a row per worker, the queue tally as the caption."""
    with _lock:
        table = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False,
                      caption=_queue, caption_style="dim")
        table.add_column("", width=3, no_wrap=True)
        table.add_column("worker", style="bold", no_wrap=True)
        table.add_column("tools", justify="right", no_wrap=True, style="dim")
        table.add_column("doing", overflow="ellipsis", no_wrap=True)
        table.add_column("elapsed", justify="right", no_wrap=True, style="dim")
        for tag, row in _rows.items():
            settled = final or row["state"] == "done"
            table.add_row(Text("✓", style="green") if settled else Spinner("dots", style="cyan"),
                          tag, str(row["tools"]), row["what"],
                          _clock(time.monotonic() - row.get("t0", _t0)))
    return Panel(table, title=f"{_title}: done" if final else _title, title_align="left",
                 border_style="green" if final else "cyan", padding=(0, 1))


def _paint():
    """Redraw. Call with _lock released: Live renders while holding its own lock."""
    if _live is not None and _drawing:
        _live.update(_panel(), refresh=True)


def open_board(title="itempipe", counts=None):
    """Start drawing. `counts` re-reads the queue; it may raise (e.g. a db lock)."""
    global _t0, _title, _counts, _open, _drawing, _console, _live
    _t0, _title, _counts, _open = time.monotonic(), title, counts, True
    with _lock:
        _rows.clear()
    if RICH:
        _console = Console(stderr=True, highlight=False)
        if sys.stderr.isatty():
            # A renderable, refreshed by _paint(); never a callable -- Live would
            # happily try to render the function object itself.
            _live = Live(_panel(), console=_console, refresh_per_second=10,
                         vertical_overflow="visible", transient=True)
            _live.start()
            _drawing = True
    refresh()


def worker(tag, kind="state", text="", state=None):
    """Record what one worker just did, then redraw."""
    if not _open:
        return
    with _lock:
        row = _row(tag)
        row["what"] = _short(text)
        if state:
            row["state"] = state
        if kind == "call":
            row["tools"] += 1
    _paint()
    if _live is None:
        print(f"{_clock(time.monotonic() - _t0):>5} {tag:<3} {ICON.get(kind, '·')} "
              f"{_short(text, 140)}", file=sys.stderr, flush=True)


def finished(tag, text="done"):
    """Mark a worker's row settled."""
    if not _open:
        return
    with _lock:
        row = _row(tag)
        row["state"], row["what"] = "done", _short(text)
    _paint()
    if _live is None:
        print(f"{_clock(time.monotonic() - _t0):>5} {tag:<3} ✓ {_short(text, 140)}",
              file=sys.stderr, flush=True)


def refresh():
    """Re-read the queue counts and redraw, so the clock and the tally stay live."""
    global _queue
    if not _open:
        return
    if _counts is not None:
        try:
            values = _counts()
        except Exception:               # a transient db lock must not kill the board
            values = None
        if values is not None:
            line = " · ".join(f"{n} {LABEL.get(s, str(s).lower())}" for s, n in values.items())
            with _lock:
                _queue = line
    _paint()


def close_board():
    """Stop animating and leave one final frame (transient boards are wiped)."""
    global _open, _drawing
    if not _open:
        return
    refresh()
    if _live is not None:
        _drawing = False
        _live.stop()
    if _live is not None and _console is not None:
        _console.print(_panel(final=True))
    else:
        print(f"{_clock(time.monotonic() - _t0):>5} done {_queue}",
              file=sys.stderr, flush=True)
    _open = False
