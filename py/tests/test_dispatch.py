"""Dispatch wiring: the queue drain and the search fan-out, with a scripted agent.

The scripted "agent" calls the real tools, so these tests prove the plumbing
(per-worker closures, claim tokens, the loop) rather than the prompt.
"""

import asyncio

from itempipe import dispatch
from itempipe.store import open_stores
from itempipe.tools import make_apply_tools


def agent_factory(stores, cfg):
    def make(ctx, extra):
        return {name: tool for name, tool in
                ((t.name, t) for t in make_apply_tools(stores["items"], ctx, config=cfg))}

    return make


def test_plan_slices_are_distinct_and_self_contained():
    slices = dispatch.plan_slices("find backend roles", 3)
    assert len(slices) == 3 and len(set(slices)) == 3
    assert all("find backend roles" in text for text in slices)
    assert "Angle 1 of 3" in slices[0]
    assert dispatch.plan_slices("one", 1) == ["one"]


def test_drain_apply_empties_the_queue(cfg, make_items):
    make_items(3)
    stores = open_stores(cfg.db, cfg)

    async def runner(tools, brief):
        if tools["claim_next_item"].invoke({}).startswith("No NEW"):
            return "idle"
        tools["begin_submission"].invoke({})
        return tools["advance_item"].invoke({})

    result = asyncio.run(dispatch.drain_apply(
        stores, cfg=cfg, workers=2, make_agent=agent_factory(stores, cfg), run=runner))
    assert result["processed"] == 3
    assert all(row["state"] == cfg.success_state for row in stores["items"].list_items())
    assert stores["workers"].list() == []


def test_drain_apply_respects_the_budget(cfg, make_items):
    make_items(4)
    stores = open_stores(cfg.db, cfg)

    async def runner(tools, brief):
        tools["claim_next_item"].invoke({})
        tools["begin_submission"].invoke({})
        return tools["advance_item"].invoke({})

    result = asyncio.run(dispatch.drain_apply(
        stores, cfg=cfg, workers=1, max_items=1,
        make_agent=agent_factory(stores, cfg), run=runner))
    assert result["processed"] == 1
    assert len(stores["items"].list_items(states=[cfg.initial_state])) == 3


def test_a_worker_that_stops_mid_claim_releases_the_item(cfg, make_items):
    make_items(1)
    stores = open_stores(cfg.db, cfg)

    async def runner(tools, brief):
        return tools["claim_next_item"].invoke({})

    asyncio.run(dispatch.drain_apply(
        stores, cfg=cfg, workers=1, make_agent=agent_factory(stores, cfg), run=runner))
    row = stores["items"].list_items()[0]
    assert row["state"] == cfg.initial_state and row["claim_token"] is None


def test_fan_out_search_runs_one_worker_per_slice(cfg):
    stores = open_stores(cfg.db, cfg)
    seen = []

    def make(ctx):
        seen.append(ctx)
        return ctx

    async def runner(ctx, brief):
        return f"report from {ctx.worker_id}"

    result = asyncio.run(dispatch.fan_out_search(
        stores, "find jobs", cfg=cfg, workers=2, make_agent=make, run=runner))
    assert len(result["reports"]) == 2 and len(seen) == 2
    assert {ctx.role for ctx in seen} == {"search"}
    assert stores["workers"].list() == []
