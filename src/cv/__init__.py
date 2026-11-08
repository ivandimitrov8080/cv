import asyncio
from types import MethodType

from cv.agents import new_agent
from cv.config import Config, parse_config
from cv.data import init_db
from cv.tools import Tools

cfg: Config
tools_list: list[MethodType] = []

async def agent_loop(cfg: Config):
    while True:
        print((await new_agent(cfg, tools_list).run(cfg.task)).output)

def setup():
    global tools_list, cfg
    cfg = parse_config()
    tools = Tools(cfg)
    tools_list = [tools.search, tools.fetch]
    init_db(cfg)

async def go() -> None:
    results = await asyncio.gather(*(agent_loop(cfg) for _ in range(cfg.threads)))
    for i, out in enumerate(results, 1):
        print(f"--- run {i} ---\n{out}")

    

def main() -> None:
    setup()
    print(tools_list[0]("Search for something"))
    print(tools_list[0]("Search for something"))
    # asyncio.run(go())
