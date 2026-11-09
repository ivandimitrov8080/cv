from asyncio import TaskGroup, run
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
        await new_agent(cfg, tools).run(cfg.task)

def setup():
    global tools_list, cfg
    cfg = parse_config()
    tools = Tools(cfg)
    tools_list = [tools.search, tools.fetch, tools.save_lead]
    init_db(cfg)

async def go() -> None:
    async with TaskGroup() as tg:
        for agent in cfg.agents:
            for _ in range(agent.threads):
                tg.create_task(agent_loop(agent))
    

def main() -> None:
    setup()
    run(go())
