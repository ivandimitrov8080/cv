import asyncio
import time
from . import config
from . import agents
from . import tools

async def go() -> None:
    cfg = config.parse_config()
    tools.init(cfg)
    tools_list = [tools.search, tools.fetch]
    agent = agents.init_agent(cfg, tools_list)
    #print(await tools.fetch("https://example.com/spa-page"))
    #print(await tools.fetch("https://search.nixos.org/packages?channel=26.05&query=ddgr"))
    print(tools.search("ddgs pypi DDGS class"))
    # print((await agent.run("search the web for ddgs and tell me what it says on the pypi page about the DDGS class")).output)

def main() -> None:
    asyncio.run(go())
