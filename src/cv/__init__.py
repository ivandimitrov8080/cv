from . import config
from . import agents



def main() -> None:
    cfg = config.parse_config()
    agent = agents.init_agent(cfg)
    print(agent)
