"""Worker identity, lease renewal, heartbeat and the reaper.

The fencing token lives in the database; this module only keeps it fresh *from a
separate task*, so a hung browser page cannot silently let the lease expire
mid-application.
"""

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .config import Config, default_config
from .store import HostLeases, ItemStore, WorkerRegistry
from . import db


@dataclass
class WorkerContext:
    """Per-worker state.  Never a module-level global: K workers, one process."""

    role: str
    index: int = 0
    worker_id: str = ""
    dry_run: bool = False
    item_id: int | None = None
    claim_token: str | None = None
    config: Config | None = None

    def __post_init__(self) -> None:
        if not self.worker_id:
            self.worker_id = f"{self.role}-{self.index}-{uuid.uuid4().hex[:8]}"

    def adopt(self, item: dict[str, Any]) -> None:
        self.item_id, self.claim_token = int(item["id"]), item["claim_token"]

    def clear(self) -> None:
        self.item_id, self.claim_token = None, None

    @property
    def claimed(self) -> bool:
        return self.item_id is not None and self.claim_token is not None

    @property
    def cfg(self) -> Config:
        return self.config or default_config()

    def require_claim(self) -> str | None:
        """Return an error string when nothing is claimed, else None."""
        return None if self.claimed else "No item claimed; call claim_next_item first."


async def _tick(stop: asyncio.Event, interval: float) -> None:
    """Sleep `interval`, or wake early when the worker is stopping."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=interval)
    except TimeoutError:
        pass


async def renew_leases(store: ItemStore, ctx: WorkerContext, *, interval: float,
                       stop: asyncio.Event) -> None:
    while not stop.is_set():
        await _tick(stop, interval)
        if stop.is_set():
            return
        if not ctx.claimed:
            continue
        try:
            ok = store.renew_lease(int(ctx.item_id), str(ctx.claim_token))
        except Exception:  # a dead DB must not kill the worker loop
            ok = False
        if not ok:
            # Another worker owns it now; drop the claim so tools refuse.
            ctx.clear()


async def beat(registry: WorkerRegistry, ctx: WorkerContext, *, interval: float,
               stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            registry.heartbeat(ctx.worker_id, item_id=ctx.item_id)
        except Exception:
            pass
        await _tick(stop, interval)


@asynccontextmanager
async def liveness(registry: WorkerRegistry, ctx: WorkerContext, *, store: ItemStore | None = None):
    """Register the worker, keep the heartbeat and lease alive, deregister on exit."""
    registry.register(ctx.worker_id, ctx.role, pid=os.getpid(), host=socket.gethostname())
    stop = asyncio.Event()
    tasks = [asyncio.create_task(beat(registry, ctx, interval=ctx.cfg.heartbeat_seconds, stop=stop))]
    if store is not None:
        tasks.append(asyncio.create_task(renew_leases(
            store, ctx, interval=max(1.0, ctx.cfg.claim_lease_seconds / 3), stop=stop)))
    try:
        yield stop
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            registry.deregister(ctx.worker_id)
        except Exception:
            pass


def reap(conn, cfg: Config | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Expired leases back to the queue (or to review), answered pauses unpaused,
    stale host leases and dead workers cleaned up."""
    cfg = cfg or default_config()
    items, hosts, registry = ItemStore(conn, cfg), HostLeases(conn), WorkerRegistry(conn, cfg)
    if dry_run:
        stale = [row for row in items.list_items(states=[cfg.working_state])
                 if row["claimed_until"] is None or row["claimed_until"] < db.now()]
        return {"requeued": 0, "review": 0, "unpaused": 0, "stale": len(stale),
                "dead_workers": len(registry.dead()),
                "expired_hosts": len(hosts.expired()), "dry_run": True}
    result = items.reap()
    return {**result, "expired_hosts": hosts.purge(),
            "dead_workers": registry.purge_dead(), "dry_run": False}
