import subprocess
from types import MethodType

from pydantic_ai import Agent
from pydantic_ai.capabilities import MCP
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

from cv.config import AgentConfig


def new_agent(cfg: AgentConfig,
              tools: list[MethodType] | None = None,
              capabilities: list | None = None
              ) -> Agent:
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
                 instructions=cfg.instructions,
                 system_prompt=cfg.system_prompt
                 )
