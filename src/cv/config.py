import os
import tomllib

from pydantic import ConfigDict, TypeAdapter
from pydantic.dataclasses import dataclass


@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class ApiConfig:
    key_cmd: str

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class ModelConfig:
    version: str
    thinking: bool

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class SearchConfig:
    backend: str
    region: str
    max_results: int

@dataclass(frozen=True, config=ConfigDict(extra="forbid"))
class Config:
    api: ApiConfig
    model: ModelConfig
    search: SearchConfig

def parse_config() -> Config:
    config_file = os.getenv("CV_CONFIG_FILE") or ""
    with open(config_file, "rb") as f:
        config = tomllib.load(f)
    return TypeAdapter(Config).validate_python(config)

