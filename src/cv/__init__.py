from asyncio import TaskGroup, run

from peewee import IntegrityError
from pydantic_ai.capabilities import MCP
from pydantic_ai_harness.playwright import PlaywrightBrowser

from cv.agents import new_agent
from cv.config import AgentConfig, Config, parse_config
from cv.data import init_db
from cv.data.lead import Lead

cfg: Config

def varName(p):
    for k, v in globals().items():
        if id(p) == id(v):
            return k

web4ai = MCP("http://localhost:8000/mcp", tool_prefix=True, id="web")

playwright = PlaywrightBrowser(cdp_url="http://localhost:9222")

capabilities = [playwright, web4ai]

async def apply_jobs(cfg: AgentConfig):
    agent = new_agent(cfg, capabilities=[playwright])
    print((await agent.run("")).output)

async def search_jobs(cfg: AgentConfig):
    existing_leads = [(l.url + ' ') for l in Lead.select(Lead.url)]
    agent = new_agent(cfg,
                      capabilities=[web4ai],
                      output_type=Lead,
                      system_prompt=f"Avoid the existing lead urls: {existing_leads}. As soon as you find a few return. The point is to not search too much.")
    task = "Search for remote software development jobs online."
    leads = (await agent.run(task)).output
    for lead in leads:
        try:
            Lead.create(
                url=lead.url,
                title=lead.title,
                description=lead.description)
        except IntegrityError as e:
            print(e)


def setup():
    global cfg
    cfg = parse_config()
    init_db(cfg)

async def go() -> None:
    async with TaskGroup() as tg:
        for _ in range(cfg.agent.threads):
            tg.create_task(search_jobs(cfg.agent))
    

def main() -> None:
    setup()
    run(go())
