from . import config
from . import agents
from . import tools

def main() -> None:
    cfg = config.parse_config()
    tool = [tools.roll_dice]
    agent = agents.init_agent(cfg, tool)
    print(tools.search())
