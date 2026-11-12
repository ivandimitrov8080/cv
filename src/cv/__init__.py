from asyncio import TaskGroup, run

from pydantic_ai.capabilities import MCP
from pydantic_ai_harness.playwright import PlaywrightBrowser

from cv.agents import new_agent
from cv.config import AgentConfig, Config, parse_config
from cv.data import init_db

cfg: Config

def varName(p):
    for k, v in globals().items():
        if id(p) == id(v):
            return k

web4ai = MCP("http://localhost:8000/mcp")

playwright = PlaywrightBrowser(cdp_url="http://localhost:9222")

capabilities = [playwright, web4ai]

async def agent_loop(cfg: AgentConfig):
    caps = list(filter(lambda x: varName(x), capabilities))
    while True:
        await new_agent(cfg, capabilities=caps).run(cfg.task)

def setup():
    global cfg
    cfg = parse_config()
    init_db(cfg)

async def go() -> None:
    async with TaskGroup() as tg:
        for agent in cfg.agents:
            for _ in range(agent.threads):
                tg.create_task(agent_loop(agent))
    

def main() -> None:
    setup()
    run(go())
