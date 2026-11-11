import os
import tomllib
from typing import Optional

from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class SearchConfig:
    backend: str
    region: str
    max_results: int

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class DataConfig:
    db: str

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class AgentConfig:
    key_cmd: str
    version: str
    thinking: bool
    system_prompt: str
    instructions: str
    capabilities: Optional[list[str]]
    task: str
    threads: int
    tools: list[str]
    
@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Config:
    search: SearchConfig
    data: DataConfig
    agents: list[AgentConfig]

def parse_config() -> Config:
    config_file = os.getenv("CV_CONFIG_FILE") or ""
    with open(config_file, "rb") as f:
        config = tomllib.load(f)
    return TypeAdapter(Config).validate_python(config)

