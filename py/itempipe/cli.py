"""Command line: run | work | process | ls | status | reap | answers | import-org.

The model stack and the browser are imported inside the commands that need them,
so `itempipe --help`, `ls` and `status` work on a bare interpreter.
"""

import argparse
import asyncio
import json
import sys
from typing import Any, Sequence

from . import config as config_module
from .config import Config
from .store import open_stores
from .worker import reap


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="itempipe", description=(
        "N stateless workers against one SQLite database: search fans out over angles, "
        "apply drains the queue."))
    p.add_argument("--db", help="database file (default: $ITEMPIPE_DB)")
    p.add_argument("--model", help="model id (default: $ITEMPIPE_MODEL)")
    sub = p.add_subparsers(dest="command")

    run = sub.add_parser("run", help="supervisor: fan out search, then drain apply")
    run.add_argument("brief")
    run.add_argument("--workers", type=int, help="search workers")
    run.add_argument("--max", dest="max_items", type=int, help="item budget for the drain")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-browser", action="store_true")

    work = sub.add_parser("work", help="long-lived worker pool")
    work.add_argument("--role", choices=["search", "apply"], required=True)
    work.add_argument("--count", type=int)
    work.add_argument("--brief", help="search brief (required for --role search)")
    work.add_argument("--max", dest="max_items", type=int)
    work.add_argument("--every", type=float, help="repeat every N seconds")
    work.add_argument("--dry-run", action="store_true")

    process = sub.add_parser("process", help="one apply pass")
    process.add_argument("--max", dest="max_items", type=int)
    process.add_argument("--count", type=int)
    process.add_argument("--dry-run", action="store_true")
    process.add_argument("--no-browser", action="store_true")

    ls = sub.add_parser("ls", help="queue inspection")
    ls.add_argument("--state", help="comma-separated state values")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--archived", action="store_true")

    status = sub.add_parser("status", help="depth, in-flight, stale leases, review queue")
    status.add_argument("--review", action="store_true", help="only what a human must look at")
    status.add_argument("--json", action="store_true")

    reap_cmd = sub.add_parser("reap", help="release expired leases, flag stuck submits")
    reap_cmd.add_argument("--dry-run", action="store_true")

    answers = sub.add_parser("answers", help="inspect or answer stored questions")
    answers_sub = answers.add_subparsers(dest="answers_cmd")
    add = answers_sub.add_parser("add", help="answer a question by id or wording")
    add.add_argument("id")
    add.add_argument("text")
    add.add_argument("--dry-run", action="store_true")
    listing = answers_sub.add_parser("list", help="look up answers")
    listing.add_argument("query", nargs="?")
    listing.add_argument("--limit", type=int)

    import_org = sub.add_parser("import-org", help="one-shot Org import")
    import_org.add_argument("jobs", nargs="?", help="jobs.org")
    import_org.add_argument("questions", nargs="?", help="application-questions.org")
    import_org.add_argument("--dry-run", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 2
    cfg = config_module.load(**{k: v for k, v in
                                (("db", args.db), ("model", args.model)) if v})
    try:
        return {"run": cmd_run, "work": cmd_work, "process": cmd_process, "ls": cmd_ls,
                "status": cmd_status, "reap": cmd_reap, "answers": cmd_answers,
                "import-org": cmd_import_org}[args.command](args, cfg)
    except KeyboardInterrupt:
        return 130
    except Exception as err:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(f"error: {err}", file=sys.stderr)
        return 1


# --- commands -------------------------------------------------------------
def cmd_ls(args: argparse.Namespace, cfg: Config) -> int:
    items = open_stores(cfg.db, cfg)["items"]
    states = [s.strip().upper() for s in (args.state or "").replace(",", " ").split()] or None
    rows = items.list_items(states=states, limit=args.limit, include_archived=args.archived)
    print(f"db: {cfg.db}")
    for row in rows:
        archived = "  [archived]" if row["archived_at"] else ""
        print(f"{row['id']:>4}  {row['state']:<12} {row['name']} — {row['title']}{archived}")
    print(f"\n{len(rows)} item(s)")
    return 0


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    stores = open_stores(cfg.db, cfg)
    status = stores["items"].status()
    if args.review:
        for row in status["review"]:
            print(f"{row['id']:>4}  {row['name']} — {row['title']}")
        print(f"\n{len(status['review'])} item(s) need review")
        return 0
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
        return 0

    print(f"db: {cfg.db}")
    print(f"queued ({cfg.initial_state}): {status['queued']}   archived: {status['archived']}")
    for state, count in sorted(status["counts"].items()):
        print(f"  {state:<14} {count}")
    print(f"in flight: {len(status['in_flight'])}  stale: {len(status['stale'])}")
    for row in status["in_flight"]:
        stale = " STALE" if row in status["stale"] else ""
        print(f"  {row['id']:>4}  {row['name']:<30} by {row['claimed_by']}{stale}")
    print(f"needs review: {len(status['review'])}")
    for row in status["review"]:
        print(f"  {row['id']:>4}  {row['name']} — {row['title']}")
    print(f"paused, waiting on an answer: {len(status['paused_waiting'])}  "
          f"(open questions: {status['questions_open']})")
    for row in status["paused_waiting"]:
        print(f"  {row['id']:>4}  {row['name']}: {row['question']}")
    workers = stores["workers"].list()
    print(f"workers: {len(workers)}")
    for worker in workers:
        print(f"  {worker['id']:<28} {worker['role']:<9} item={worker['item_id']} "
              f"beat={worker['heartbeat_at']}")
    return 0


def cmd_reap(args: argparse.Namespace, cfg: Config) -> int:
    result = reap(open_stores(cfg.db, cfg)["conn"], cfg, dry_run=args.dry_run)
    prefix = "DRY RUN: " if args.dry_run else ""
    print(f"{prefix}requeued={result['requeued']} review={result['review']} "
          f"unpaused={result['unpaused']} dead_workers={result['dead_workers']} "
          f"expired_host_leases={result['expired_hosts']}")
    return 0


def cmd_answers(args: argparse.Namespace, cfg: Config) -> int:
    answers = open_stores(cfg.db, cfg)["answers"]
    if args.answers_cmd == "add":
        if args.dry_run:
            print(f"DRY RUN: would answer {args.id!r} with {args.text!r}")
        elif not answers.answer(args.id, args.text)["updated"]:
            print(f"no question matches {args.id!r}")
            return 1
        else:
            print(f"answered {args.id}")
        return 0
    print(answers.search(getattr(args, "query", None), limit=getattr(args, "limit", None)))
    return 0


def cmd_import_org(args: argparse.Namespace, cfg: Config) -> int:
    from .org_import import import_org

    summary = import_org(open_stores(cfg.db, cfg)["conn"], cfg,
                         jobs_path=args.jobs or cfg.tracker_org,
                         questions_path=args.questions or cfg.answers_org,
                         dry_run=args.dry_run)
    print(f"{'DRY RUN: ' if args.dry_run else ''}db: {cfg.db}")
    print(f"entries={summary['entries']} created={summary['created']} "
          f"duplicate={summary['duplicate']} rejected={summary['rejected']}")
    for state, count in sorted(summary["states"].items()):
        print(f"  {state:<14} {count}")
    print(f"notes={summary['notes']} archived={summary['archived']} "
          f"questions={summary['questions']} answers={summary['answers']}")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")
    return 0


def _run(coro: Any) -> int:
    asyncio.run(coro)
    return 0


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    from . import dispatch

    stores = open_stores(cfg.db, cfg)

    async def go() -> None:
        result = await dispatch.run_supervisor(
            stores, args.brief, cfg=cfg, workers=args.workers, max_items=args.max_items,
            dry_run=args.dry_run, browser=not args.no_browser)
        print(result["report"])

    return _run(go())


def cmd_process(args: argparse.Namespace, cfg: Config) -> int:
    from . import dispatch

    stores = open_stores(cfg.db, cfg)

    async def go() -> None:
        result = await dispatch.drain_apply(
            stores, cfg=cfg, workers=args.count, max_items=args.max_items,
            dry_run=args.dry_run, browser=not args.no_browser)
        print(f"processed {result['processed']} item(s) with {result['workers']} worker(s)"
              f"{' (dry run)' if args.dry_run else ''}")
        for report in result["reports"]:
            print("---")
            print(report or "(empty)")

    return _run(go())


def cmd_work(args: argparse.Namespace, cfg: Config) -> int:
    from . import dispatch

    if args.role == "search" and not args.brief:
        print("--brief is required for --role search", file=sys.stderr)
        return 2
    stores = open_stores(cfg.db, cfg)

    async def go() -> None:
        while True:
            if args.role == "apply":
                result = await dispatch.drain_apply(
                    stores, cfg=cfg, workers=args.count, max_items=args.max_items,
                    dry_run=args.dry_run, browser=True)
                print(f"apply: {result['processed']} item(s)")
            else:
                result = await dispatch.fan_out_search(
                    stores, args.brief, cfg=cfg, workers=args.count, dry_run=args.dry_run)
                for report in result["reports"]:
                    print("---")
                    print(report or "(empty)")
            if args.every is None or args.dry_run:
                return
            await asyncio.sleep(args.every)

    return _run(go())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
