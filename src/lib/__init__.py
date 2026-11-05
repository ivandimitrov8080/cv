import argparse
import asyncio
import dataclasses

from . import act, config, db, ingest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="webpipe")
    parser.add_argument("command", choices=["init", "status", "ingest", "act"])
    parser.add_argument("-c", "--config", default=None,
                        help="path to tool.toml (default: ./config/tool.toml, then "
                             "the checkout's src/config/tool.toml)")
    parser.add_argument("-n", "--workers", type=int)
    args = parser.parse_args(argv)
    cfg = config.load(args.config or config.default_config())
    if args.workers:
        cfg = dataclasses.replace(
            cfg,
            ingest=dataclasses.replace(cfg.ingest, workers=args.workers),
            act=dataclasses.replace(cfg.act, workers=args.workers))
    if args.command == "init":
        store = db.Store(cfg).create()
        print(f"{cfg.db}: {store.row.select().count()} item(s)")
        return 0
    if args.command == "status":
        store = db.Store(cfg).connect()
        counts = " ".join(f"{state}={count}" for state, count in sorted(store.counts().items()))
        print(f"{cfg.db}: {counts or 'empty'}")
        return 0
    try:
        reports = asyncio.run(ingest.run(cfg) if args.command == "ingest" else act.run(cfg))
    except KeyboardInterrupt:
        return 130
    print("\n".join(reports or []) or "nothing to report")
    return 0
