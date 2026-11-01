"""Config invariants: state values are data, roles are code."""

import pytest
from pydantic import ValidationError

from itempipe import config as config_module
from itempipe.config import Config


def test_every_role_is_in_the_vocabulary():
    cfg = config_module.load()
    for name in ("initial_state", "working_state", "success_state", "paused_state", "review_state"):
        assert getattr(cfg, name) in cfg.state_vocab
    assert set(cfg.terminal_states) <= set(cfg.state_vocab)


def test_a_role_outside_the_vocabulary_is_refused():
    with pytest.raises(ValidationError):
        Config(initial_state="LEAD")


def test_env_overrides_and_explicit_wins(monkeypatch):
    monkeypatch.setenv("ITEMPIPE_MAX_APPLY_WORKERS", "5")
    monkeypatch.setenv("ITEMPIPE_EMAIL_MIN_INTERVAL", "0.5")
    monkeypatch.setenv("ITEMPIPE_ATTACHMENTS", "/a.pdf:/b.pdf")
    monkeypatch.setenv("ITEMPIPE_DB", "/tmp/env.db")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = config_module.load(db="/tmp/explicit.db")
    assert (cfg.max_apply_workers, cfg.email_min_interval) == (5, 0.5)
    assert cfg.attachments == ("/a.pdf", "/b.pdf")
    assert cfg.api_key == "sk-test"
    assert cfg.db == "/tmp/explicit.db"


def test_sql_interval_helpers():
    cfg = config_module.load(claim_lease_seconds=30, follow_up_days=3)
    assert cfg.lease_sql() == "+30 seconds" and cfg.follow_up_sql() == "+3 days"
