import subprocess
from types import MethodType
from typing import TypeVar

from playhouse.pydantic_utils import to_pydantic
from pydantic_ai import Agent
from pydantic_ai.capabilities import MCP
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

from cv.config import AgentConfig

R = TypeVar("R")

def new_agent[R](cfg: AgentConfig,
              tools: list[MethodType] | None = None,
              capabilities: list | None = None,
              instructions: list | None = None,
              system_prompt: str = "",
              output_type: type[R] = str
              ) -> Agent[object, list[R]]:
    if capabilities is None:
        capabilities = []
    if tools is None:
        tools = []
    api_key = subprocess.check_output(cfg.key_cmd.split(" ")).decode("ascii").strip()
    model = OpenAIChatModel(cfg.version,
                            provider = DeepSeekProvider(api_key=api_key))
    return Agent(model,
                 tools = tools,
                 capabilities=[*capabilities, MCP("http://localhost:8000/mcp", id="web4ai")],
                 model_settings=OpenAIChatModelSettings(thinking=cfg.thinking),
                 instructions=instructions,
                 system_prompt=system_prompt,
                 output_type=list[to_pydantic(output_type)]
                 )
