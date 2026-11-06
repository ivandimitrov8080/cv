import asyncio
import cv
from cv import agents
from cv.tools import Tools
from cv.config import Config, parse_config

global tools_list

async def agent(cfg: Config):
    agent = agents.init_agent(cfg, cv.tools_list)
    return (await agent.run(cfg.task)).output

async def agent_loop(cfg: Config):
    while True:
        await agent(cfg)

async def go() -> None:
    cfg = parse_config()
    tools = Tools(cfg)
    cv.tools_list = [tools.search, tools.fetch]
    results = await asyncio.gather(*(agent_loop(cfg) for _ in range(cfg.threads)))
    for i, out in enumerate(results, 1):
        print(f"--- run {i} ---\n{out}")

    

def main() -> None:
    asyncio.run(go())
