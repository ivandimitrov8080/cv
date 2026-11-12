from asyncio import TaskGroup, run

from peewee import IntegrityError
from playhouse.pydantic_utils import to_pydantic
from pydantic_ai.capabilities import MCP
from pydantic_ai_harness.playwright import PlaywrightBrowser

from cv.agents import new_agent
from cv.config import AgentConfig, Config, parse_config
from cv.data import init_db, db
from cv.data.lead import Lead, LeadState

cfg: Config

def varName(p):
    for k, v in globals().items():
        if id(p) == id(v):
            return k

web4ai = MCP("http://localhost:8000/mcp", tool_prefix=True, id="web")

playwright = PlaywrightBrowser(cdp_url="http://localhost:9222")

capabilities = [playwright, web4ai]

def next_lead() -> Lead:
    with db.atomic():
        lead = Lead.get(Lead.state == LeadState.NEW)
        Lead.update(state=LeadState.PROCESSING).where(Lead.url == lead.url).execute()
    return lead

def read_profile():
    with open("../../cv.typ") as f:
        return f.readline()

def read_questions():
    with open("../../data/application-questions.org") as f:
        return f.readline()

async def apply_jobs(cfg: AgentConfig):
    lead_url = next_lead().url
    agent = new_agent(cfg,
                      capabilities=[playwright],
                      output_type=Lead,
                      system_prompt=f"The lead URL is {lead_url}. Apply only to one job post and return the status depending on the result. The CV to attach is at /home/ivand/src/cv/result/Ivan_Dimitrov_Resume_IT_Consultant_ATS.pdf. Fill out only required fields and ignore fields that are not required. Do not fabricate information. Here's the user profile: {read_profile()}. Here's the Q/A info: {read_questions()}")
    task = "Apply to jobs online."
    lead = (await agent.run(task)).output
    Lead.update(state=lead.state).where(Lead.url == lead_url).execute()

async def search_jobs(cfg: AgentConfig):
    existing_leads = [(l.url + ' ') for l in Lead.select(Lead.url)]
    agent = new_agent(cfg,
                      capabilities=[web4ai],
                      output_type=list[Lead],
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
            tg.create_task(apply_jobs(cfg.agent))
    

def main() -> None:
    setup()
    run(go())
