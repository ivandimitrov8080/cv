import subprocess
from types import MethodType

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

from cv.config import AgentConfig


def new_agent(cfg: AgentConfig, tools: list[MethodType]) -> Agent:
    api_key = subprocess.check_output(cfg.key_cmd.split(" ")).decode("ascii").strip()
    model = OpenAIChatModel(cfg.version,
                            provider = DeepSeekProvider(api_key=api_key))
    return Agent(model,
                 tools = tools,
                 model_settings=OpenAIChatModelSettings(thinking=cfg.thinking),
                 instructions="",
                 system_prompt=cfg.system_prompt
                 )
