"""Every knob, all optional except DEEPSEEK_API_KEY."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    db: Path = ROOT / "py" / "itempipe.db"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str | None = None
    search_workers: int = 4
    apply_workers: int = 2
    browser_slots: int = 2
    lease_minutes: int = 60
    max_page_chars: int = 8000
    email_min_interval: float = 5.0
    msmtp_account: str = "default"
    email_template: Path = ROOT / "application-email-template.txt"
    attachments: tuple[Path, ...] = (
        ROOT / "result" / "Ivan_Dimitrov_Resume_IT_Consultant.pdf",
    )
    profile_dir: Path = ROOT / "py" / ".playwright-profiles"
    chromium: str | None = None
    cv_path: Path = ROOT / "cv.typ"
    recursion_limit: int = 60


def load(**overrides) -> Config:
    env = os.environ
    ints = {"search_workers": "ITEMPIPE_SEARCH_WORKERS",
            "apply_workers": "ITEMPIPE_APPLY_WORKERS",
            "browser_slots": "ITEMPIPE_BROWSER_SLOTS",
            "lease_minutes": "ITEMPIPE_LEASE_MINUTES",
            "max_page_chars": "ITEMPIPE_MAX_PAGE_CHARS"}
    values = {field: int(env[name]) for field, name in ints.items() if env.get(name)}
    paths = {"db": "ITEMPIPE_DB", "email_template": "ITEMPIPE_EMAIL_TEMPLATE",
             "profile_dir": "ITEMPIPE_PROFILE_DIR", "cv_path": "ITEMPIPE_CV_PATH"}
    values |= {field: Path(env[name]) for field, name in paths.items() if env.get(name)}
    for field, name in (("model", "ITEMPIPE_MODEL"), ("base_url", "ITEMPIPE_BASE_URL"),
                        ("msmtp_account", "ITEMPIPE_MSMTP_ACCOUNT"),
                        ("chromium", "ITEMPIPE_CHROMIUM")):
        if env.get(name):
            values[field] = env[name]
    if env.get("DEEPSEEK_API_KEY"):
        values["api_key"] = env["DEEPSEEK_API_KEY"]
    if env.get("ITEMPIPE_ATTACHMENTS"):
        values["attachments"] = tuple(Path(p) for p in env["ITEMPIPE_ATTACHMENTS"].split(":")
                                      if p.strip())
    return Config(**{**values, **overrides})


def cv_text(cfg: Config) -> str:
    try:
        return cfg.cv_path.read_text(encoding="utf-8")
    except OSError:
        return "(CV text unavailable)"
