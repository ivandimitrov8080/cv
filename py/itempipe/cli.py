"""CLI: search | apply | run | ls | answer | reap."""

import argparse
import asyncio
import sys

from . import agents, config
from .db import open_db
from .store import Store


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="itempipe", description=__doc__)
    p.add_argument("--db", help="database file (default: $ITEMPIPE_DB)")
    sub = p.add_subparsers(dest="command")

    search = sub.add_parser("search", help="find and record new leads")
    search.add_argument("brief")
    search.add_argument("-n", type=int, help="parallel search agents")
    search.add_argument("--dry-run", action="store_true")

    apply_cmd = sub.add_parser("apply", help="drain the queue")
    apply_cmd.add_argument("-n", type=int, help="parallel apply workers")
    apply_cmd.add_argument("--dry-run", action="store_true")

    run = sub.add_parser("run", help="search, then apply")
    run.add_argument("brief")
    run.add_argument("-n", type=int, help="parallel search agents")
    run.add_argument("-k", type=int, help="parallel apply workers")
    run.add_argument("--dry-run", action="store_true")

    ls = sub.add_parser("ls", help="list leads")
    ls.add_argument("--state")
    ls.add_argument("--all", action="store_true", help="include archived")

    answer = sub.add_parser("answer", help="answer a paused question by id or wording")
    answer.add_argument("ident")
    answer.add_argument("text")

    sub.add_parser("reap", help="requeue expired claims, unpause answered items")
    return p


def _report(reports: list[str]) -> None:
    for text in reports:
        print(text or "(empty)")
        print("---")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 2
    cfg = config.load(db=args.db) if args.db else config.load()
    store = Store(open_db(cfg.db), cfg)
    try:
        if args.command in ("search", "run"):
            reports = asyncio.run(agents.search(store, args.brief, cfg, args.n, args.dry_run))
            _report(reports)
        if args.command == "apply":
            _report(asyncio.run(agents.apply(store, cfg, args.n, args.dry_run)))
        elif args.command == "run":
            _report(asyncio.run(agents.apply(store, cfg, args.k, args.dry_run)))
        elif args.command == "ls":
            wanted = (args.state or "").replace(",", " ").upper().split()
            for row in store.rows(archived=args.all):
                if wanted and row["state"] not in wanted:
                    continue
                print(f"{row['id']:>4}  {row['state']:<12} {row['name']} — {row['title']}")
        elif args.command == "answer":
            print(store.answer(args.ident, args.text))
        elif args.command == "reap":
            result = store.reap()
            print(f"reaped {result['reaped']} expired claim(s), unpaused {result['unpaused']}")
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as err:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(f"error: {err}", file=sys.stderr)
        return 1
