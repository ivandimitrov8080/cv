from pathlib import Path

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI


def render(path, values=None):
    text = Path(path).read_text(encoding="utf-8")
    for name, value in (values or {}).items():
        text = text.replace("{{" + name + "}}", str(value or ""))
    return text


def build(model, prompt, tools):
    llm = ChatOpenAI(model=model.name, base_url=model.base_url,
                     api_key=model.api_key, timeout=model.timeout)
    return create_agent(llm, tools, system_prompt=prompt)
