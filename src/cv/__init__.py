import asyncio

from cv import agents, config
from cv.tools import Tools


async def go() -> None:
    cfg = config.parse_config()
    tools = Tools(cfg)
    tools_list = [tools.search, tools.fetch]
    agent = agents.init_agent(cfg, tools_list)
    print((await agent.run("search the web for ddgs and tell me what it says on the pypi page about the DDGS class")).output)

def main() -> None:
    asyncio.run(go())
