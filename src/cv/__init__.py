from . import config
from . import agents
from . import tools

def main() -> None:
    cfg = config.parse_config()
    tools_list = [tools.search]
    agent = agents.init_agent(cfg, tools_list)
    print(tools.search("camel"))
    # print(agent.run_sync("search the web for camels and tell me the first url").output)
