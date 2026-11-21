import subprocess
from asyncio import TaskGroup, run
from sys import argv

from peewee import IntegrityError
from pydantic_ai import Agent, RunContext, ToolDefinition
from pydantic_ai.capabilities import MCP, PrepareTools
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider

from cv.config import Config, parse_config
from cv.data import db, init_db
from cv.data.lead import Lead, LeadState

cfg: Config

api_key: str
model: OpenAIChatModel
model_settings: OpenAIChatModelSettings

def varName(p):
    for k, v in globals().items():
        if id(p) == id(v):
            return k

web4ai = MCP("http://localhost:8000/mcp", tool_prefix=True, id="web")

capabilities = [web4ai]

def next_lead() -> Lead:
    with db.atomic():
        lead = Lead.get(Lead.state == LeadState.NEW)
        Lead.update(state=LeadState.PROCESSING).where(Lead.url == lead.url).execute()
    return lead

def read_profile():
    with open("cv.typ") as f:
        return f.read()

def read_questions():
    with open("data/application-questions.org") as f:
        return f.read()


ACT = frozenset(f'web_{x}' for x in ('see', 'go', 'click', 'type_text'))
READ = frozenset(f'web_{x}' for x in ('fetch', 'search'))

async def browser_act(_: RunContext, tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
    return [td for td in tool_defs if td.name in ACT]

async def browser_read(_: RunContext, tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
    return [td for td in tool_defs if td.name in READ]

async def apply_jobs():
    lead_url = next_lead().url
    agent = Agent(model, model_settings=model_settings,
                  capabilities=[web4ai, PrepareTools(browser_act)],
                  output_type=Lead,
                  system_prompt=f"The lead URL is {lead_url}. Apply only to one job post and return the status depending on the result. The CV to attach is at /home/ivand/src/cv/result/Ivan_Dimitrov_Resume_IT_Consultant_ATS.pdf. Fill out only required fields and ignore fields that are not required. Do not fabricate information. Here's the user profile: {read_profile()}. Here's the Q/A info: {read_questions()}")
    task = "Apply to jobs online."
    lead = (await agent.run(task)).output
    Lead.update(state=lead.state).where(Lead.url == lead_url).execute()

async def search_jobs():
    existing_leads = [(l.url + ' ') for l in Lead.select(Lead.url)]
    agent = Agent(model, model_settings=model_settings,
                  capabilities=[web4ai, PrepareTools(browser_read)],
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
    global cfg, api_key, model, model_settings
    cfg = parse_config()
    api_key = subprocess.check_output(cfg.agent.key_cmd.split(" ")).decode("ascii").strip()
    model = OpenAIChatModel(cfg.agent.version,
                            provider = DeepSeekProvider(api_key=api_key),
                            )
    model_settings = OpenAIChatModelSettings(thinking=cfg.agent.thinking)
    init_db(cfg)

async def go_apply() -> None:
    async with TaskGroup() as tg:
        for _ in range(cfg.agent.threads):
            tg.create_task(apply_jobs())
    
async def go_search() -> None:
    async with TaskGroup() as tg:
        for _ in range(cfg.agent.threads):
            tg.create_task(search_jobs())


def main() -> None:
    setup()
    if argv[1] == "apply":
        run(go_apply())
    else:
        run(go_search())
