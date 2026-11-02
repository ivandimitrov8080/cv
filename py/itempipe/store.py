"""The domain: a lead, a claim, and the two irreversible moments.

Claiming is one UPDATE ... RETURNING, so the database is the lock. Every later
mutation is fenced by the claim token: a worker whose lease expired can never
finish someone else's item.
"""

import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from . import db
from .config import Config

NEW, WORKING, ACTIVE, PAUSED, REVIEW = "NEW", "WORKING", "ACTIVE", "PAUSED", "NEEDS_REVIEW"
FENCED = "This item is no longer assigned to this worker; stopping."
_WS = re.compile(r"\s+")


def line(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip()


def key(value: Any) -> str:
    return line(value).lower()


def url_key(value: str) -> str:
    return line(value).split("#")[0].rstrip("/").lower()


@dataclass
class Worker:
    role: str
    index: int = 0
    dry_run: bool = False
    item_id: int | None = None
    token: str | None = None

    @property
    def claimed(self) -> bool:
        return self.item_id is not None and self.token is not None

    def clear(self) -> None:
        self.item_id = self.token = None


class Store:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.conn, self.cfg = conn, cfg

    # -- leads -------------------------------------------------------------
    def create(self, name: str, title: str, url: str, notes: str = "", *, dry_run: bool = False) -> str:
        name, title, url, notes = line(name), line(title), line(url), line(notes)
        if not name or not title:
            return "NOT recorded: a lead needs a company and the exact job title."
        if not url.startswith(("http://", "https://")):
            return "NOT recorded: a lead needs the posting's own http(s) URL."
        if dry_run:
            return f"DRY RUN: would record {name} — {title} <{url}>."
        try:
            self.conn.execute(
                "INSERT INTO items (name, name_key, title, url, url_key, notes, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, key(name), title, url, url_key(url), notes or None, NEW))
        except sqlite3.IntegrityError:
            row = self.conn.execute("SELECT * FROM items WHERE name_key = ?", (key(name),)).fetchone()
            if row is None:
                return "NOT recorded: that name was just taken."
            state = row["state"] + ("/archived" if row["archived_at"] else "")
            return f"Already tracked ({state}): {row['name']} — {row['title']}."
        return f"Recorded: {name} — {title} <{url}>."

    def rows(self, *, archived: bool = False, limit: int = 200):
        where = "" if archived else " WHERE archived_at IS NULL"
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM items{where} ORDER BY id LIMIT ?", (limit,))]

    def queued(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM items WHERE state = ? AND archived_at IS NULL", (NEW,)).fetchone()[0]

    # -- the claim ---------------------------------------------------------
    def claim(self, worker: Worker) -> str:
        token = uuid.uuid4().hex
        row = self.conn.execute(
            """
            UPDATE items SET state = ?, claimed_by = ?, claim_token = ?,
                             claimed_until = datetime('now', ?),
                             attempt_count = attempt_count + 1
             WHERE id = (SELECT id FROM items WHERE state = ? AND archived_at IS NULL
                          ORDER BY id LIMIT 1)
            RETURNING *;
            """, (WORKING, f"{worker.role}-{worker.index}", token,
                  f"+{self.cfg.lease_minutes} minutes", NEW)).fetchone()
        if row is None:
            return "No NEW items left. Stop."
        worker.item_id, worker.token = row["id"], token
        fields = (("Id", row["id"]), ("Name", row["name"]), ("Title", row["title"]),
                  ("Url", row["url"]), ("Notes", row["notes"]))
        return "\n".join(f"{name}: {value or '-'}" for name, value in fields)

    def _owned(self, worker: Worker):
        if not worker.claimed:
            return None
        row = self.conn.execute(
            "SELECT * FROM items WHERE id = ? AND claim_token = ? AND state = ?",
            (worker.item_id, worker.token, WORKING)).fetchone()
        if row is None:
            return None
        renewed = self.conn.execute(
            "UPDATE items SET claimed_until = datetime('now', ?) "
            "WHERE id = ? AND claim_token = ? AND state = ?",
            (f"+{self.cfg.lease_minutes} minutes", worker.item_id, worker.token, WORKING))
        return row if renewed.rowcount == 1 else None

    def _finish(self, worker: Worker, sets: str, params: tuple = ()) -> bool:
        """One fenced update: the claim token and WORKING state must still be ours."""
        cursor = self.conn.execute(
            f"UPDATE items SET {sets}, claim_token = NULL, claimed_by = NULL, "
            f"claimed_until = NULL WHERE id = ? AND claim_token = ? AND state = ?",
            (*params, worker.item_id, worker.token, WORKING))
        ok = cursor.rowcount == 1
        worker.clear()
        return ok

    def settle(self, worker: Worker) -> None:
        """A worker that stopped without an outcome must not leave a lock behind."""
        if worker.claimed:
            self.release(worker, "agent stopped without recording an outcome")

    # -- outcomes ----------------------------------------------------------
    def advance(self, worker: Worker) -> str:
        row = self._owned(worker)
        if row is None:
            return FENCED
        if worker.dry_run:
            self.release(worker, "dry run")
            return f"DRY RUN: would mark {row['name']} ACTIVE."
        if not row["submitting"]:
            return ("NOT done: call begin_submission immediately before the final submit, then "
                    "advance_item.")
        ok = self._finish(worker, "state = ?, submitting = 0, advanced_on = date('now')",
                          (ACTIVE,))
        return (f"Marked {row['name']} — {row['title']} ACTIVE." if ok else FENCED)

    def release(self, worker: Worker, reason: str) -> str:
        row = self._owned(worker)
        if row is None:
            worker.clear()
            return FENCED
        state = REVIEW if row["submitting"] else NEW
        ok = self._finish(worker, "state = ?, submitting = 0, archive_reason = ?",
                          (state, line(reason)))
        return (f"Released {row['name']} to {state}: {line(reason)}." if ok else FENCED)

    def archive(self, worker: Worker, reason: str = "") -> str:
        row = self._owned(worker)
        if row is None:
            return FENCED
        if worker.dry_run:
            self.release(worker, "dry run")
            return f"DRY RUN: would archive {row['name']}."
        if row["submitting"]:
            return self.release(worker, f"archive requested after submission started: {reason}")
        reason = line(reason) or "abandoned"
        ok = self._finish(worker, "archived_at = datetime('now'), archive_reason = ?, "
                          "submitting = 0", (reason,))
        return (f"Archived {row['name']}: {reason}." if ok else FENCED)

    def begin_submission(self, worker: Worker) -> str:
        row = self._owned(worker)
        if row is None:
            return FENCED
        cursor = self.conn.execute(
            "UPDATE items SET submitting = 1 WHERE id = ? AND claim_token = ? AND state = ?",
            (row["id"], worker.token, WORKING))
        if cursor.rowcount != 1:
            worker.clear()
            return FENCED
        return f"Submission guard set for {row['name']}; at-most-once from here."

    def pause(self, worker: Worker, question: str) -> str:
        row = self._owned(worker)
        if row is None:
            return FENCED
        question = line(question)
        if not question:
            return "Nothing saved: no question given."
        if worker.dry_run:
            self.release(worker, "dry run")
            return f"DRY RUN: would save this question and pause {row['name']}: {question}"
        question_key = key(question)
        self.conn.execute(
            "INSERT INTO questions (question, question_key) VALUES (?, ?) "
            "ON CONFLICT(question_key) DO NOTHING", (question, question_key))
        ok = self._finish(worker, "state = ?, question = ?, submitting = 0",
                          (PAUSED, question_key))
        return (f"Paused {row['name']}; the question is saved for the user." if ok else FENCED)

    # -- answers -----------------------------------------------------------
    def lookup(self, query: str = "") -> str:
        rows = self.conn.execute(
            "SELECT question, answer FROM questions WHERE answer IS NOT NULL").fetchall()
        tokens = re.findall(r"[a-z0-9]+", query.lower())
        score = lambda row: sum(token in row["question"].lower() for token in tokens)  # noqa: E731
        matches = [row for row in sorted(rows, key=score, reverse=True) if not tokens or score(row)]
        if not rows:
            return "No stored answers yet."
        if not matches:
            return f"No stored answer matches {query!r}. Ask instead of inventing one."
        return "\n\n".join(f"Q: {row['question']}\nA: {row['answer']}" for row in matches[:5])

    def answer(self, ident: str, text: str) -> str:
        ident = line(ident)
        if ident.isdigit():
            cursor = self.conn.execute(
                "UPDATE questions SET answer = ?, answered_at = datetime('now') WHERE id = ?",
                (line(text), int(ident)))
            if cursor.rowcount == 0:
                return f"No question {ident}."
        else:
            self.conn.execute(
                "INSERT INTO questions (question, question_key, answer, answered_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(question_key) DO UPDATE SET answer = excluded.answer, "
                "answered_at = datetime('now')", (ident, key(ident), line(text)))
        unpaused = self.conn.execute(
            "UPDATE items SET state = ?, question = NULL WHERE state = ? AND question IN "
            "(SELECT question_key FROM questions WHERE answer IS NOT NULL)", (NEW, PAUSED)).rowcount
        return f"Answered {ident}." + (f" Requeued {unpaused} item(s)." if unpaused else "")

    # -- recovery ----------------------------------------------------------
    def reap(self) -> dict[str, int]:
        stale = self.conn.execute(
            "SELECT COUNT(*) FROM items WHERE state = ? AND "
            "(claimed_until IS NULL OR claimed_until < datetime('now'))", (WORKING,)).fetchone()[0]
        with db.transaction(self.conn):
            self.conn.execute(
                "UPDATE items SET state = CASE WHEN submitting THEN ? ELSE ? END, "
                "submitting = 0, claim_token = NULL, claimed_by = NULL, claimed_until = NULL "
                "WHERE state = ? AND (claimed_until IS NULL OR claimed_until < datetime('now'))",
                (REVIEW, NEW, WORKING))
            unpaused = self.conn.execute(
                "UPDATE items SET state = ?, question = NULL WHERE state = ? AND question IN "
                "(SELECT question_key FROM questions WHERE answer IS NOT NULL)",
                (NEW, PAUSED)).rowcount
        return {"reaped": stale, "unpaused": unpaused}
