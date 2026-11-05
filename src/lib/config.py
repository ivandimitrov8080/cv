import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_ENV = "WEBPIPE_CONFIG"
LOCAL_CONFIG = Path("config", "tool.toml")
# src/lib/config.py -> src/config/tool.toml, i.e. the config next to the sources.
SOURCE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "tool.toml"


def default_config():
    """Locate tool.toml when --config is not given."""
    if override := os.environ.get(CONFIG_ENV):
        return Path(override)
    local = Path.cwd() / LOCAL_CONFIG
    if local.is_file():
        return local
    return SOURCE_CONFIG if SOURCE_CONFIG.is_file() else local


@dataclass(frozen=True)
class Model:
    name: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    key_var: str = "DEEPSEEK_API_KEY"
    timeout: int = 180

    @property
    def api_key(self):
        return os.environ.get(self.key_var, "missing")


@dataclass(frozen=True)
class Ingest:
    prompt: Path
    seeds: tuple = ()
    queries: tuple = ()
    workers: int = 4
    steps: int = 30
    pause: float = 2.0
    idle: float = 300.0
    seeds_per_session: int = 2
    queries_per_session: int = 3
    ttl: tuple = ()


@dataclass(frozen=True)
class Act:
    prompt: Path
    outcomes: tuple
    guarded: tuple = ()
    notes: Path | None = None
    workers: int = 2
    steps: int = 120
    lease: int = 1800
    browser: bool = False
    profile: str = "/tmp/webpipe-chromium"
    mcp_output: int = 20000


@dataclass(frozen=True)
class Config:
    root: Path
    db: Path
    schema: Path
    table: str
    key: str
    memo: str
    states: dict
    model: Model
    ingest: Ingest
    act: Act
    brief: str = ""

    @property
    def new(self):
        return self.states["new"]

    @property
    def working(self):
        return self.states["working"]

    @property
    def review(self):
        return self.states.get("review")


def _path(base, value):
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load(path):
    path = Path(path).resolve()
    base = path.parent
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    database, ingest, act = raw.get("db", {}), raw.get("ingest", {}), raw.get("act", {})
    if "prompt" not in ingest or "prompt" not in act or "outcomes" not in act:
        raise ValueError(f"{path}: [ingest] prompt and [act] prompt/outcomes are required")
    return Config(
        root=base,
        db=_path(base, database.get("path", "../webpipe.db")),
        schema=_path(base, database.get("schema", "schema.sql")),
        table=database.get("table", "item"),
        key=database.get("key", "url"),
        memo=database.get("memo", "memo"),
        states=dict(database.get("states", {"new": "NEW", "working": "WORKING"})),
        model=Model(**raw.get("model", {})),
        brief=" ".join(str(raw.get("brief", "")).split()),
        ingest=Ingest(
            prompt=_path(base, ingest["prompt"]),
            seeds=tuple(ingest.get("seeds", ())),
            queries=tuple(ingest.get("queries", ())),
            workers=int(ingest.get("workers", 4)),
            steps=int(ingest.get("steps", 30)),
            pause=float(ingest.get("pause", 2.0)),
            idle=float(ingest.get("idle", 300.0)),
            seeds_per_session=int(ingest.get("seeds_per_session", 2)),
            queries_per_session=int(ingest.get("queries_per_session", 3)),
            ttl=tuple(dict(ingest.get("ttl", {})).items()),
        ),
        act=Act(
            prompt=_path(base, act["prompt"]),
            outcomes=tuple(dict(act["outcomes"]).items()),
            guarded=tuple(act.get("guarded", ())),
            notes=_path(base, act["notes"]) if act.get("notes") else None,
            workers=int(act.get("workers", 2)),
            steps=int(act.get("steps", 120)),
            lease=int(act.get("lease", 1800)),
            browser=bool(act.get("browser", False)),
            profile=str(act.get("profile", "/tmp/webpipe-chromium")),
            mcp_output=int(act.get("mcp_output", 20000)),
        ),
    )
