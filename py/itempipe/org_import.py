"""Org -> SQLite import.  The only place the old file format is still understood.

  LEAD APPLIED INTERVIEW OFFER HOLD | HIRED REJECTED IGNORED
  ->  NEW ACTIVE  INTERVIEW OFFER PAUSED | DONE REJECTED IGNORED
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, default_config
from .store import AnswerStore, ItemStore, one_line

HEADING = re.compile(r"^(\*+)\s+(.*)$")
TAGS = re.compile(r"\s+:([\w@:#%\-]+):\s*$")
PROP = re.compile(r"^:([A-Za-z0-9_@#%\-]+):\s*(.*)$")
NOTE = re.compile(r"^-\s*(?:NOTE:\s*|Note\s*\[[^\]]*\]\s*)?", re.IGNORECASE)

STATE_MAP = {"LEAD": "NEW", "APPLIED": "ACTIVE", "INTERVIEW": "INTERVIEW", "OFFER": "OFFER",
             "HOLD": "PAUSED", "HIRED": "DONE", "REJECTED": "REJECTED", "IGNORED": "IGNORED"}
# Org property -> items column; everything else is kept in meta_json.
COLUMNS = {"URL": "url", "LOCATION": "location", "NATION": "region", "SIZE": "size",
           "ENGLISH": "language", "CONTACT": "contact", "NOTES": "notes",
           "CREATED": "created_at", "APPLIED_ON": "advanced_on"}


@dataclass
class Entry:
    level: int
    todo: str | None
    name: str
    title: str
    props: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    archived: bool = False


def split_heading(text: str) -> tuple[str | None, str, str]:
    """`LEAD Acme — Backend Engineer` -> (LEAD, Acme, Backend Engineer)."""
    if match := TAGS.search(text):
        text = text[: match.start()].rstrip()
    todo, _, rest = text.partition(" ")
    todo = todo if todo.isupper() and todo.isalpha() else None
    body = rest.strip() if todo else text
    for separator in ("—", " - "):
        if separator in body:
            name, _, title = body.partition(separator)
            return todo, name.strip(), title.strip()
    return todo, body, ""


def parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    current, in_props, archived_tree = None, False, False
    for raw in text.splitlines():
        if heading := HEADING.match(raw):
            level, todo, name, title = len(heading.group(1)), *split_heading(heading.group(2))
            current, in_props = None, False
            if level == 1:      # "* Pipeline" / "* Archive" are containers
                archived_tree = name.lower().startswith("archive")
                continue
            if name:
                current = Entry(level, todo, name, title, archived=archived_tree)
                entries.append(current)
            continue
        if current is None:
            continue
        line = raw.strip()
        if line == ":PROPERTIES:":
            in_props = True
        elif line == ":END:":
            in_props = False
        elif in_props:
            if prop := PROP.match(line):
                current.props[prop.group(1).upper()] = prop.group(2).strip()
        elif line.startswith("- ") and (note := NOTE.sub("", line).strip()):
            current.notes.append(note)
    return entries


def import_org(conn, cfg: Config | None = None, *, jobs_path: str | Path | None = None,
               questions_path: str | Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Idempotent on `name_key`; returns the diff-shaped summary the CLI prints."""
    cfg = cfg or default_config()
    items = ItemStore(conn, cfg)
    entries = parse(Path(jobs_path or cfg.tracker_org).read_text(encoding="utf-8"))
    summary: dict[str, Any] = {"entries": len(entries), "created": 0, "duplicate": 0,
                               "rejected": 0, "states": {}, "notes": 0, "archived": 0,
                               "questions": 0, "answers": 0, "warnings": []}
    for entry in entries:
        state = STATE_MAP.get(entry.todo or "", cfg.initial_state)
        if state not in cfg.state_vocab:
            summary["warnings"].append(f"unmapped keyword {entry.todo!r} for {entry.name}")
            state = cfg.initial_state
        props = entry.props
        archived_at = (props.get("ARCHIVE_TIME") or "unknown") if (
            entry.archived or props.get("ARCHIVE_TIME")) else None
        result = items.create(
            name=entry.name, title=entry.title or entry.name, require_url=False,
            url=props.get("URL", ""), state=state,
            location=props.get("LOCATION"), region=props.get("NATION"),
            size=props.get("SIZE"), language=props.get("ENGLISH"),
            contact=props.get("CONTACT"), notes=props.get("NOTES"),
            created_at=props.get("CREATED"), advanced_on=props.get("APPLIED_ON"),
            archived_at=archived_at,
            archive_reason=props.get("ARCHIVE_OLPATH") or ("imported from org archive" if archived_at else None),
            meta={k: v for k, v in props.items() if k not in COLUMNS},
            note_bodies=entry.notes, dry_run=dry_run,
        )
        summary[result["status"]] += 1
        if result["status"] == "created":
            summary["states"][state] = summary["states"].get(state, 0) + 1
            summary["notes"] += len(entry.notes)
            summary["archived"] += bool(archived_at)

    if questions_path and Path(questions_path).exists():
        rows = parse_questions(Path(questions_path).read_text(encoding="utf-8"))
        answers = AnswerStore(conn, cfg)
        for question, props in rows:
            if not dry_run:
                answers.add(question, one_line(props.get("ANSWER")),
                            source=one_line(props.get("SOURCE")),
                            note=one_line(props.get("NOTE") or props.get("FORMAT")))
            summary["questions"] += 1
            summary["answers"] += bool(one_line(props.get("ANSWER")))
    return summary


def parse_questions(text: str) -> list[tuple[str, dict[str, str]]]:
    """`** question` + `:PROPERTIES:` -> [(question, props)]; sections skipped."""
    rows, current, in_props = [], None, False
    for raw in text.splitlines():
        if heading := HEADING.match(raw):
            current, in_props = None, False
            question = one_line(heading.group(2))
            if question and len(heading.group(1)) > 1:
                current = {}
                rows.append((question, current))
            continue
        if current is None:
            continue
        line = raw.strip()
        if line == ":PROPERTIES:":
            in_props = True
        elif line == ":END:":
            in_props = False
        elif in_props and (prop := PROP.match(line)):
            current[prop.group(1).upper()] = prop.group(2).strip()
    return rows
