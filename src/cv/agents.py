import subprocess
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

def init_agent(cfg: Config) -> Agent:
    api_key = subprocess.check_output(cfg.api.key_cmd.split(" ")).decode("ascii").strip()
    model = OpenAIChatModel(cfg.model.version, provider = DeepSeekProvider(api_key=api_key))
    return Agent(model, model_settings=OpenAIChatModelSettings(thinking=False))
