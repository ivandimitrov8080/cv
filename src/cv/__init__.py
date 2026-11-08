import asyncio
from types import MethodType

from cv.agents import new_agent
from cv.config import AgentConfig, Config, parse_config
from cv.data import init_db
from cv.tools import Tools

cfg: Config
tools_list: list[MethodType] = []

async def agent_loop(cfg: AgentConfig):
    tools = list(filter(lambda x: x.__name__ in cfg.tools, tools_list))
    while True:
        print((await new_agent(cfg, tools).run(cfg.task)).output)

def setup():
    global tools_list, cfg
    cfg = parse_config()
    tools = Tools(cfg)
    tools_list = [tools.search, tools.fetch]
    init_db(cfg)

async def go() -> None:
    for agent in cfg.agents:
        results = await asyncio.gather(*(agent_loop(agent) for _ in range(agent.threads)))
        for i, out in enumerate(results, 1):
            print(f"--- run {i} ---\n{out}")

    

def main() -> None:
    setup()
    print(tools_list[0]("Search for something"))
    print(tools_list[0]("Search for something"))
    # asyncio.run(go())
