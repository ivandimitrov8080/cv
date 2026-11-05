from . import config
from . import agents
from . import tools

def main() -> None:
    cfg = config.parse_config()
    tool = [tools.roll_dice]
    agent = agents.init_agent(cfg, tool)
    print(agent.run_sync("use the roll dice tool and tell me the output. If you don't see the tool tell me"))
