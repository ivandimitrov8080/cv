"""One table of settings.  State values are data, never identifiers.

Code refers to *roles* (initial/working/success/paused/review/terminal); the
actual strings live in `state_vocab`. Renaming the domain changes this table,
not the code.

Settings are read from the environment by `load()` rather than with
pydantic-settings: that package JSON-decodes complex env values even when an
explicit override is given, which turns one bad `ITEMPIPE_ATTACHMENTS` into a
crash on every command.
"""

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]      # py/itempipe/config.py -> repo
DEFAULT_RESUME = str(REPO_ROOT / "result" / "Ivan_Dimitrov_Resume_IT_Consultant.pdf")


class Config(BaseModel):
    # storage
    db: str = str(REPO_ROOT / "py" / "itempipe.db")

    # state vocabulary: data, not identifiers
    state_vocab: tuple[str, ...] = ("NEW", "WORKING", "ACTIVE", "INTERVIEW", "OFFER",
                                    "PAUSED", "NEEDS_REVIEW", "DONE", "REJECTED", "IGNORED")
    initial_state: str = "NEW"           # claim_next() pulls from here
    working_state: str = "WORKING"       # held while a worker owns the item
    success_state: str = "ACTIVE"        # advance() moves here + schedules the follow-up
    paused_state: str = "PAUSED"         # pause_item_for_answer() moves here
    review_state: str = "NEEDS_REVIEW"   # ambiguous outcome, a human must look
    terminal_states: tuple[str, ...] = ("DONE", "REJECTED", "IGNORED")

    # field vocabularies (an unknown value is dropped with a warning)
    size_vocab: tuple[str, ...] = ("small", "mid", "large")
    language_vocab: tuple[str, ...] = ("yes", "unknown", "no")

    # pipeline
    follow_up_days: int = 14
    answers_limit: int = 3

    # concurrency budget (§2): apply workers are expensive, search workers are cheap
    max_search_workers: int = 4
    max_apply_workers: int = 2
    browser_slots: int = 2
    max_llm_concurrency: int = 6
    claim_lease_seconds: int = 900
    heartbeat_seconds: int = 30
    email_min_interval: float = 5.0

    # model
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str | None = None
    temperature: float = 0.2
    recursion_limit: int = 60

    # files
    email_template: str = str(REPO_ROOT / "application-email-template.txt")
    attachments: tuple[str, ...] = (DEFAULT_RESUME,)
    tracker_org: str = str(REPO_ROOT / "jobs.org")
    answers_org: str = str(REPO_ROOT / "application-questions.org")
    cv_path: str = str(REPO_ROOT / "cv.typ")
    max_page_chars: int = 8000

    # transport
    msmtp_account: str = "default"
    smtp_host: str | None = None
    smtp_port: int = 465

    # browser
    profile_dir: str = str(REPO_ROOT / "py" / ".playwright-profiles")
    chromium: str | None = None

    @model_validator(mode="after")
    def _roles_are_in_vocab(self) -> "Config":
        for name in ("initial_state", "working_state", "success_state", "paused_state",
                     "review_state"):
            if getattr(self, name) not in self.state_vocab:
                raise ValueError(f"{name}={getattr(self, name)!r} is not in state_vocab")
        if unknown := set(self.terminal_states) - set(self.state_vocab):
            raise ValueError(f"terminal_states not in state_vocab: {sorted(unknown)}")
        return self

    def lease_sql(self, seconds: int | None = None) -> str:
        return f"+{int(seconds or self.claim_lease_seconds)} seconds"

    def follow_up_sql(self) -> str:
        return f"+{int(self.follow_up_days)} days"

    def cv_text(self) -> str:
        try:
            return Path(self.cv_path).read_text(encoding="utf-8")
        except OSError:
            return ""


def _coerce(kind: Any) -> Any:
    return {
        int: lambda raw: int(raw.strip()),
        float: lambda raw: float(raw.strip()),
        "paths": lambda raw: tuple(p.strip() for p in raw.split(":") if p.strip()),
        "opt": lambda raw: raw.strip() or None,
    }.get(kind, str.strip)


_ENV = {
    "ITEMPIPE_DB": ("db", str), "ITEMPIPE_MODEL": ("model", str),
    "ITEMPIPE_BASE_URL": ("base_url", str), "ITEMPIPE_FOLLOW_UP_DAYS": ("follow_up_days", int),
    "ITEMPIPE_ANSWERS_LIMIT": ("answers_limit", int),
    "ITEMPIPE_MAX_SEARCH_WORKERS": ("max_search_workers", int),
    "ITEMPIPE_MAX_APPLY_WORKERS": ("max_apply_workers", int),
    "ITEMPIPE_BROWSER_SLOTS": ("browser_slots", int),
    "ITEMPIPE_MAX_LLM_CONCURRENCY": ("max_llm_concurrency", int),
    "ITEMPIPE_CLAIM_LEASE_SECONDS": ("claim_lease_seconds", int),
    "ITEMPIPE_HEARTBEAT_SECONDS": ("heartbeat_seconds", int),
    "ITEMPIPE_EMAIL_MIN_INTERVAL": ("email_min_interval", float),
    "ITEMPIPE_EMAIL_TEMPLATE": ("email_template", str),
    "ITEMPIPE_ATTACHMENTS": ("attachments", "paths"),
    "ITEMPIPE_MSMTP_ACCOUNT": ("msmtp_account", str),
    "ITEMPIPE_SMTP_HOST": ("smtp_host", "opt"), "ITEMPIPE_SMTP_PORT": ("smtp_port", int),
    "ITEMPIPE_PROFILE_DIR": ("profile_dir", str), "ITEMPIPE_CHROMIUM": ("chromium", "opt"),
    "ITEMPIPE_MAX_PAGE_CHARS": ("max_page_chars", int),
    "ITEMPIPE_TRACKER_ORG": ("tracker_org", str), "ITEMPIPE_ANSWERS_ORG": ("answers_org", str),
    "ITEMPIPE_CV_PATH": ("cv_path", str),
}


def load(**overrides: Any) -> Config:
    """Environment first, then explicit overrides, then defaults."""
    data = {field: _coerce(kind)(os.environ[name])
            for name, (field, kind) in _ENV.items() if os.environ.get(name)}
    if os.environ.get("DEEPSEEK_API_KEY"):
        data["api_key"] = os.environ["DEEPSEEK_API_KEY"]
    return Config(**{**data, **overrides})


_DEFAULT: Config | None = None


def default_config() -> Config:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = load()
    return _DEFAULT
