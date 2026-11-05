import asyncio
import time
from . import config
from . import agents
from . import tools

async def go() -> None:
    cfg = config.parse_config()
    tools.init()
    tools_list = [tools.search]
    agent = agents.init_agent(cfg, tools_list)
    print(await tools.fetch("https://example.com/spa-page"))
    print(await tools.fetch("https://search.nixos.org/packages?channel=26.05&query=ddgr"))


def main() -> None:
    asyncio.run(go())
    # print(agent.run_sync("search the web for camels and tell me the first url").output)
