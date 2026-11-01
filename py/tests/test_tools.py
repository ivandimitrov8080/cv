"""Tool contract: strings in, strings out, never an exception.

The prompts are written against this contract, so this file is the specification
of what the model sees.
"""

import asyncio

import pytest

from itempipe.store import FENCED, TargetStore
from itempipe.tools import make_apply_tools, make_search_tools
from itempipe.tools.email import EmailError, render, reset_throttle
from itempipe.worker import WorkerContext


def apply_tools(items, ctx, cfg):
    return {tool.name: tool for tool in make_apply_tools(items, ctx, config=cfg)}


def search_tools(items, ctx, cfg, targets, hosts=None):
    return {tool.name: tool for tool in make_search_tools(
        items, ctx, targets=targets, config=cfg, hosts=hosts, with_web=False)}


def call(tool, payload=None):
    """Uniform call that works for sync and async tools."""
    return asyncio.run(tool.ainvoke(payload or {}))


def test_apply_tool_names(items, cfg):
    tools = apply_tools(items, WorkerContext(role="apply", config=cfg), cfg)
    assert set(tools) == {"claim_next_item", "advance_item", "release_item", "archive_item",
                          "begin_submission", "lookup_answer", "pause_item_for_answer",
                          "send_templated_email"}


def test_search_tool_names(items, cfg, targets):
    tools = search_tools(items, WorkerContext(role="search", config=cfg), cfg, targets)
    assert set(tools) == {"create_item", "list_items", "claim_research_target",
                          "mark_target_unusable"}


def test_bad_input_returns_a_string(items, cfg, targets):
    tools = search_tools(items, WorkerContext(role="search", config=cfg), cfg, targets)
    out = tools["create_item"].invoke({"name": "Acme", "title": "Engineer", "url": "nope"})
    assert isinstance(out, str) and out.startswith("NOT recorded")


def test_tools_without_a_claim_refuse(items, cfg):
    tools = apply_tools(items, WorkerContext(role="apply", config=cfg), cfg)
    for name, payload in (("advance_item", {}), ("release_item", {"reason": "timeout"}),
                          ("archive_item", {}), ("begin_submission", {}),
                          ("pause_item_for_answer", {"question": "Why us?"}),
                          ("send_templated_email", {"email": "a@b.example", "name": "Acme",
                                                   "title": "Engineer"})):
        assert call(tools[name], payload) == "No item claimed; call claim_next_item first."


def test_claim_guard_advance_flow(items, cfg):
    items.create(name="Acme", title="Engineer", url="https://example.com/1")
    ctx = WorkerContext(role="apply", config=cfg)
    tools = apply_tools(items, ctx, cfg)

    report = tools["claim_next_item"].invoke({})
    assert "Name: Acme" in report and "Title: Engineer" in report and ctx.claimed
    assert "Already holding" in tools["claim_next_item"].invoke({})
    assert tools["advance_item"].invoke({}).startswith("NOT done (submitting)")
    assert "at-most-once" in tools["begin_submission"].invoke({})
    assert tools["advance_item"].invoke({}).startswith("Marked Acme — Engineer as ACTIVE")
    assert not ctx.claimed
    assert tools["claim_next_item"].invoke({}) == "No NEW items left in the pipeline."


def test_fenced_tools_stop_a_stale_worker(cfg, conn, items):
    stale_ctx = WorkerContext(role="apply", config=cfg)
    stale = apply_tools(items, stale_ctx, cfg)
    items.create(name="Acme", title="Engineer", url="https://example.com/1")
    stale["claim_next_item"].invoke({})
    item_id = stale_ctx.item_id

    conn.execute("UPDATE items SET claimed_until = datetime('now', '-1 hour') WHERE id = ?",
                 (item_id,))
    items.reap()
    fresh_ctx = WorkerContext(role="apply", config=cfg)
    fresh = apply_tools(items, fresh_ctx, cfg)
    assert "Name: Acme" in fresh["claim_next_item"].invoke({})

    assert stale["release_item"].invoke({"reason": "still there?"}) == FENCED
    assert stale["archive_item"].invoke({"reason": "still there?"}) == FENCED
    assert items.get(item_id)["claimed_by"] == fresh_ctx.worker_id


def test_release_and_archive_tool_paths(items, cfg, make_items):
    make_items(2)
    ctx = WorkerContext(role="apply", config=cfg)
    tools = apply_tools(items, ctx, cfg)
    tools["claim_next_item"].invoke({})
    first_id = ctx.item_id
    assert tools["release_item"].invoke({"reason": "page timed out"}).startswith("Released")
    assert items.get(first_id)["state"] == cfg.initial_state

    tools["claim_next_item"].invoke({})
    assert tools["archive_item"].invoke({"reason": "no fit"}) == "Archived Subject 0: no fit"


def test_dry_run_leaves_the_row_untouched(items, cfg, make_items):
    make_items(1)
    ctx = WorkerContext(role="apply", config=cfg, dry_run=True)
    tools = apply_tools(items, ctx, cfg)
    assert "Name: Subject 0" in tools["claim_next_item"].invoke({})
    assert tools["advance_item"].invoke({}).startswith("DRY RUN: would mark")
    row = items.get(1)
    assert row["state"] == cfg.initial_state and row["claim_token"] is None


def test_list_items_output(items, cfg, targets):
    items.create(name="Acme", title="Engineer", url="https://example.com/1")
    tools = search_tools(items, WorkerContext(role="search", config=cfg), cfg, targets)
    assert "Acme — NEW" in tools["list_items"].invoke({"states": "NEW"})
    assert tools["list_items"].invoke({"states": "DONE"}) == "No items in DONE."


def test_create_item_duplicate_message(items, cfg, targets):
    tools = search_tools(items, WorkerContext(role="search", config=cfg), cfg, targets)
    first = tools["create_item"].invoke(
        {"name": "Acme", "title": "Engineer", "url": "https://example.com/1"})
    assert first.startswith("Recorded:")
    second = tools["create_item"].invoke(
        {"name": "acme", "title": "Engineer", "url": "https://example.com/2"})
    assert second.startswith("Already tracked")


# --- lookup_answer --------------------------------------------------------
def test_lookup_answer_is_read_only_and_says_when_empty(items, cfg, answers):
    answers.add("What is your notice period?", "One month")
    answers.add("Are you authorised to work in the EU?", None)
    tools = apply_tools(items, WorkerContext(role="apply", config=cfg), cfg)
    assert "One month" in tools["lookup_answer"].invoke({"query": "notice period"})
    assert "visa" not in tools["lookup_answer"].invoke({}).lower()
    assert tools["lookup_answer"].invoke({"query": "kubernetes"}).startswith("No stored answer")


# --- email ----------------------------------------------------------------
def _cfg_with(cfg, **overrides):
    return cfg.model_copy(update=overrides)


def test_render_substitutes_two_placeholders(cfg):
    message = render(_cfg_with(cfg, attachments=()), email="jobs@acme.example",
                     name="Acme", title="Backend Engineer")
    assert message["Subject"] == "Backend Engineer at Acme"
    assert "Hello Acme," in message.get_content()


def test_render_reports_the_failing_step(cfg, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("Subject: Hi\n\n{{office}} is nice.\n", encoding="utf-8")
    with pytest.raises(EmailError) as err:
        render(_cfg_with(cfg, attachments=(), email_template=str(bad)),
               email="a@b.example", name="Acme", title="Engineer")
    assert err.value.step == "parsing the template"
    assert "unsupported placeholder" in str(err.value.err)

    unwritten = tmp_path / "wip.txt"
    unwritten.write_text("Subject: <<< fill me >>>\n\nbody\n", encoding="utf-8")
    with pytest.raises(EmailError) as err:
        render(_cfg_with(cfg, attachments=(), email_template=str(unwritten)),
               email="a@b.example", name="Acme", title="Engineer")
    assert err.value.step == "reading the template"
    assert "write your application email" in str(err.value.err)

    missing = _cfg_with(cfg, attachments=(str(tmp_path / "cv.pdf"),))
    with pytest.raises(EmailError) as err:
        render(missing, email="a@b.example", name="Acme", title="Engineer")
    assert err.value.step == "attaching the CV"
    assert "is missing" in str(err.value.err)


def test_render_attaches_the_resume(cfg, tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    message = render(_cfg_with(cfg, attachments=(str(cv),)), email="a@b.example",
                     name="Acme", title="Engineer")
    assert [part.get_filename() for part in message.walk() if part.get_filename()] == ["cv.pdf"]


def test_send_dry_run_renders_but_never_sends(cfg, conn, items, make_items):
    reset_throttle()
    make_items(1)
    ctx = WorkerContext(role="apply", config=cfg, dry_run=True)
    tools = apply_tools(items, ctx, cfg)
    tools["claim_next_item"].invoke({})
    out = call(tools["send_templated_email"],
               {"email": "jobs@acme.example", "name": "Acme", "title": "Backend Engineer"})
    assert out.startswith("DRY RUN: rendered MIME") and "Backend Engineer at Acme" in out
    assert conn.execute("SELECT COUNT(*) FROM attempts WHERE status IN ('submitting','sent')"
                        ).fetchone()[0] == 0


def test_send_failure_keeps_the_submitting_guard_open(cfg, items, make_items, monkeypatch):
    from itempipe.tools import email as email_module

    reset_throttle()
    make_items(1)
    ctx = WorkerContext(role="apply", config=cfg)
    tools = apply_tools(items, ctx, cfg)
    tools["claim_next_item"].invoke({})

    def boom(cfg_, message):
        raise RuntimeError("msmtp: no account configured")

    monkeypatch.setattr(email_module, "deliver", boom)
    out = call(tools["send_templated_email"],
               {"email": "jobs@acme.example", "name": "Acme", "title": "Backend Engineer"})
    assert out.startswith("Application email NOT sent (sending):") and "no account" in out
    assert items.latest_attempt(int(ctx.item_id), ctx.worker_id)["status"] == "submitting"


def test_send_success_marks_sent(cfg, items, make_items, monkeypatch):
    from itempipe.tools import email as email_module

    reset_throttle()
    make_items(1)
    ctx = WorkerContext(role="apply", config=cfg)
    tools = apply_tools(items, ctx, cfg)
    tools["claim_next_item"].invoke({})
    sent = []
    monkeypatch.setattr(email_module, "deliver", lambda cfg_, message: sent.append(message["To"]))
    out = call(tools["send_templated_email"],
               {"email": "jobs@acme.example", "name": "Acme", "title": "Backend Engineer"})
    assert out == "Application email sent to jobs@acme.example for Backend Engineer at Acme."
    assert sent == ["jobs@acme.example"]
    assert items.latest_attempt(int(ctx.item_id), ctx.worker_id)["status"] == "sent"
