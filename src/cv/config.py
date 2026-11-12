import os
import tomllib

from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class DataConfig:
    db: str

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class AgentConfig:
    key_cmd: str
    version: str
    thinking: bool
    threads: int

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Config:
    data: DataConfig
    agent: AgentConfig

def parse_config() -> Config:
    config_file = os.getenv("CV_CONFIG_FILE") or ""
    with open(config_file, "rb") as f:
        config = tomllib.load(f)
    return TypeAdapter(Config).validate_python(config)

