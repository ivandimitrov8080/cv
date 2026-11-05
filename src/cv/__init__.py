from . import config


def main() -> None:
    cfg = config.parse_config()
    print(cfg.api.key_cmd)
