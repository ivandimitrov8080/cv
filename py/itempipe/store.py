"""All SQL lives here; nothing above this layer writes a query.

Two rules the design rests on: claiming is one atomic `UPDATE ... RETURNING`
(the database *is* the lock), and every mutation after a claim is fenced by
`claim_token`, so a worker whose lease expired can never finish someone
else's item.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from . import db
from .config import Config, default_config

FENCED = "This item is no longer assigned to this worker; stopping."
CLEAR = "claim_token = NULL, claimed_by = NULL, claimed_until = NULL"
_WS = re.compile(r"\s+")

CLAIM = """
UPDATE items SET state = :working, claimed_by = :worker, claim_token = :token,
                 claimed_until = datetime('now', :lease), attempt_count = attempt_count + 1
WHERE id = (SELECT id FROM items WHERE state = :initial AND archived_at IS NULL
             ORDER BY id LIMIT 1)
RETURNING *;
"""


# --- normalisation --------------------------------------------------------
def one_line(value: Any) -> str | None:
    """Collapse to one trimmed line; blank becomes None."""
    if value is None:
        return None
    line = _WS.sub(" ", str(value)).strip()
    return line or None


def name_key(value: Any) -> str | None:
    line = one_line(value)
    return line.lower() if line else None


def url_key(url: Any) -> str:
    """Dedupe hint: scheme/host lowercased, trailing slash and fragment gone."""
    line = one_line(url) or ""
    try:
        parts = urlsplit(line)
    except ValueError:
        return line.lower()
    if not parts.scheme:
        return line.lower()
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/",
         parts.query, "")
    )


def _json(value: dict | None) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None


def _upsert_question(conn, question, answer=None, source=None, note=None) -> str:
    key = name_key(question) or ""
    conn.execute(
        "INSERT INTO questions (question, question_key, answer, source, note, answered_at) "
        "VALUES (?, ?, ?, ?, ?, CASE WHEN ? IS NULL THEN NULL ELSE datetime('now') END) "
        "ON CONFLICT(question_key) DO NOTHING",
        (one_line(question) or question, key, answer, source, note, answer),
    )
    return key


# --- items ----------------------------------------------------------------
class ItemStore:
    def __init__(self, conn: sqlite3.Connection, cfg: Config | None = None):
        self.conn = conn
        self.cfg = cfg or default_config()

    # -- reads -------------------------------------------------------------
    def _row(self, row) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def get(self, item_id: int) -> dict[str, Any] | None:
        return self._row(self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())

    def find_by_name_key(self, key: str | None) -> dict[str, Any] | None:
        """Duplicate check.  Deliberately does *not* filter archived rows."""
        if not key:
            return None
        return self._row(self.conn.execute("SELECT * FROM items WHERE name_key = ?", (key,)).fetchone())

    def get_by_name(self, name: Any) -> dict[str, Any] | None:
        return self.find_by_name_key(name_key(name))

    def list_items(self, *, states: Sequence[str] | None = None, limit: int = 200,
                   include_archived: bool = False) -> list[dict[str, Any]]:
        clauses, params = [], []
        if not include_archived:
            clauses.append("archived_at IS NULL")
        if states:
            clauses.append(f"state IN ({','.join('?' for _ in states)})")
            params.extend(states)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM items{where} ORDER BY id LIMIT ?",
                                 (*params, int(limit))).fetchall()
        return [dict(row) for row in rows]

    def notes(self, item_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM notes WHERE item_id = ? ORDER BY id", (item_id,))]

    def describe(self, row) -> str:
        suffix = f", archived {row['archived_at']}" if row["archived_at"] else ""
        return f"{row['name']} — {row['title']} ({row['state'] or '?'}{suffix})"

    def format_item(self, row: dict[str, Any]) -> str:
        """The multi-line report `claim_next_item` hands to the model."""
        fields = (("Id", row["id"]), ("Name", row["name"]), ("Title", row["title"]),
                  ("State", row["state"]), ("Url", row["url"]), ("Location", row["location"]),
                  ("Region", row["region"]), ("Size", row["size"]),
                  ("Language", row["language"]), ("Contact", row["contact"]),
                  ("Notes", row["notes"]), ("Attempts", row["attempt_count"]))
        return "\n".join(f"{k}: {v if v not in (None, '') else '-'}" for k, v in fields)

    # -- notes / attempts --------------------------------------------------
    def add_note(self, item_id: int, kind: str = "note", body: str = "") -> None:
        self.conn.execute("INSERT INTO notes (item_id, kind, body) VALUES (?, ?, ?)",
                          (item_id, kind, str(body)))

    def _attempt(self, item_id: int, worker_id: str, status: str, detail: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO attempts (item_id, worker_id, status, detail) VALUES (?, ?, ?, ?)",
            (item_id, worker_id, status, detail))

    def latest_attempt(self, item_id: int, worker_id: str | None = None) -> dict[str, Any] | None:
        sql, params = "SELECT * FROM attempts WHERE item_id = ?", [item_id]
        if worker_id is not None:
            sql, params = sql + " AND worker_id = ?", [item_id, worker_id]
        return self._row(self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone())

    def _close_attempts(self, item_id: int, status: str, detail: str | None = None) -> None:
        self.conn.execute(
            "UPDATE attempts SET status = ?, ended_at = datetime('now'), "
            "detail = COALESCE(?, detail) WHERE item_id = ? AND ended_at IS NULL "
            "AND status IN ('claimed', 'submitting')", (status, detail, item_id))

    # -- create ------------------------------------------------------------
    def create(self, *, name: Any, title: Any, url: Any = "", location: Any = None,
               region: Any = None, size: Any = None, language: Any = None,
               contact: Any = None, notes: Any = None, state: str | None = None,
               require_url: bool = True, created_at: str | None = None,
               advanced_on: str | None = None, follow_up_on: str | None = None,
               archived_at: str | None = None, archive_reason: str | None = None,
               meta: dict | None = None, note_bodies: Iterable[str] = (),
               dry_run: bool = False) -> dict[str, Any]:
        """Record one item.  Bad *input* is a report, never an exception.

        `require_url=False` is the bulk-import path (archived Org rows often
        have no URL); agents always take the default.
        """
        cfg = self.cfg
        name, title, url = one_line(name), one_line(title), one_line(url) or ""
        if not name:
            return self._report("rejected", "NOT recorded: no name given.")
        if not title:
            return self._report("rejected", f"NOT recorded: {name} has no title.")
        if require_url and not re.match(r"^https?://", url):
            return self._report(
                "rejected",
                f"NOT recorded: {name} — {title} needs the posting's own http(s) URL, got {url!r}.")
        state = state or cfg.initial_state
        if state not in cfg.state_vocab:
            return self._report("rejected", f"NOT recorded: {name} — {title} has unknown state {state!r}.")

        warnings, values = [], {}
        for field, raw, vocab in (("size", size, cfg.size_vocab),
                                  ("language", language, cfg.language_vocab)):
            value = one_line(raw)
            if value is not None:
                value = value.lower()
                if value not in vocab:
                    warnings.append(f"ignored unknown {field} {value!r}")
                    value = None
            values[field] = value

        key = name_key(name)
        if (existing := self.find_by_name_key(key)) is not None:
            return self._report(
                "duplicate",
                f"Already tracked, so not added again: {self.describe(existing)}. "
                "Report it as a duplicate.", item=existing, warnings=warnings)

        message = f"Recorded: {name} — {title} <{url}>."
        if warnings:
            message += " Warnings: " + "; ".join(warnings) + "."
        if other := self._other_item_with_url(url, exclude_key=key):
            message += f" Note: that URL is already used by {other}."
        if dry_run:
            return self._report("created", "DRY RUN: would record " + message[len("Recorded: "):],
                                warnings=warnings)

        payload = {
            "name": name, "name_key": key, "title": title, "url": url, "url_key": url_key(url),
            "location": one_line(location), "region": one_line(region),
            "size": values["size"], "language": values["language"],
            "contact": one_line(contact), "notes": one_line(notes), "state": state,
            "created_at": created_at, "advanced_on": advanced_on, "follow_up_on": follow_up_on,
            "archived_at": archived_at, "archive_reason": archive_reason,
            "meta_json": _json(meta),
        }
        with db.transaction(self.conn):
            cur = self.conn.execute(
                """
                INSERT INTO items (name, name_key, title, url, url_key, location, region,
                                   size, language, contact, notes, state, created_at,
                                   advanced_on, follow_up_on, archived_at, archive_reason, meta_json)
                VALUES (:name, :name_key, :title, :url, :url_key, :location, :region,
                        :size, :language, :contact, :notes, :state,
                        COALESCE(:created_at, datetime('now')), :advanced_on, :follow_up_on,
                        :archived_at, :archive_reason, :meta_json)
                ON CONFLICT(name_key) DO NOTHING
                """, payload)
            # Invariant: a swallowed lookup miss once dropped CREATED/NOTES on
            # every recorded item, so the write is asserted, not trusted.
            if cur.rowcount != 1:
                lost = self.find_by_name_key(key)
                if lost is None:
                    raise RuntimeError(f"insert of name_key={key!r} left no row")
                return self._report(
                    "duplicate",
                    f"Already tracked, so not added again: {self.describe(lost)}. "
                    "Report it as a duplicate.", item=lost, warnings=warnings)
            row = self.get_by_name(name)
            assert row is not None
            for body in note_bodies:
                self.add_note(row["id"], "note", body)
        return self._report("created", message, item=row, warnings=warnings)

    def _other_item_with_url(self, url: str, *, exclude_key: str | None) -> str | None:
        if not url:
            return None
        row = self.conn.execute(
            "SELECT name FROM items WHERE url_key = ? AND name_key IS NOT ? LIMIT 1",
            (url_key(url), exclude_key)).fetchone()
        return row["name"] if row is not None else None

    @staticmethod
    def _report(status: str, message: str, *, item=None, warnings=None) -> dict[str, Any]:
        return {"status": status, "message": message, "item": item, "warnings": warnings or []}

    # -- claiming ----------------------------------------------------------
    def claim_next(self, worker_id: str, *, lease_seconds: int | None = None) -> dict[str, Any] | None:
        cfg, token = self.cfg, uuid.uuid4().hex
        with db.transaction(self.conn):
            self._requeue_expired()
            row = self.conn.execute(CLAIM, {
                "working": cfg.working_state, "initial": cfg.initial_state,
                "worker": worker_id, "token": token, "lease": cfg.lease_sql(lease_seconds),
            }).fetchone()
            if row is None:
                return None
            self._attempt(row["id"], worker_id, "claimed", f"claim_token={token}")
            self.add_note(row["id"], "worker", f"claimed by {worker_id}")
            self.conn.execute("UPDATE workers SET item_id = ? WHERE id = ?", (row["id"], worker_id))
            return dict(row)

    def renew_lease(self, item_id: int, token: str) -> bool:
        cur = self.conn.execute(
            f"UPDATE items SET claimed_until = datetime('now', ?) WHERE id = ? AND claim_token = ? "
            f"AND state = ?", (self.cfg.lease_sql(), item_id, token, self.cfg.working_state))
        return cur.rowcount == 1

    def _owned(self, item_id: int, token: str) -> dict[str, Any] | None:
        return self._row(self.conn.execute(
            "SELECT * FROM items WHERE id = ? AND claim_token = ? AND state = ?",
            (item_id, token, self.cfg.working_state)).fetchone())

    def _fenced(self, item_id: int, token: str, sets: str, params: dict | None = None) -> bool:
        cur = self.conn.execute(
            f"UPDATE items SET {sets} WHERE id = :item_id AND claim_token = :token "
            f"AND state = :working",
            {"item_id": item_id, "token": token, "working": self.cfg.working_state, **(params or {})})
        return cur.rowcount == 1

    # -- transitions -------------------------------------------------------
    def advance(self, item_id: int, token: str, *, worker_id: str | None = None) -> tuple[bool, str]:
        cfg = self.cfg
        with db.transaction(self.conn):
            row = self._owned(item_id, token)
            if row is None:
                return False, FENCED
            attempt = self.latest_attempt(item_id, worker_id)
            if attempt is None or attempt["status"] not in ("submitting", "sent"):
                return False, ("NOT done (submitting): call begin_submission immediately "
                               "before the final submit action (or send_templated_email for "
                               "an email application) before recording the outcome.")
            if not self._fenced(
                    item_id, token,
                    f"state = :state, {CLEAR}, advanced_on = date('now'), "
                    f"follow_up_on = date('now', :follow_up)",
                    {"state": cfg.success_state, "follow_up": cfg.follow_up_sql()}):
                return False, FENCED
            self.conn.execute("UPDATE attempts SET status = 'submitted', "
                              "ended_at = datetime('now') WHERE item_id = ? AND status = 'submitting'",
                              (item_id,))
            self.add_note(item_id, "state", f"{cfg.working_state} -> {cfg.success_state}")
            updated = self.get(item_id)
        assert updated is not None
        return True, (f"Marked {updated['name']} — {updated['title']} as {cfg.success_state} "
                      f"and scheduled a follow-up ({updated['follow_up_on']}).")

    def release(self, item_id: int, token: str, reason: str) -> tuple[bool, str]:
        with db.transaction(self.conn):
            row = self._owned(item_id, token)
            if row is None:
                return False, FENCED
            if not self._fenced(item_id, token, f"state = :state, {CLEAR}",
                                {"state": self.cfg.initial_state}):
                return False, FENCED
            reason = one_line(reason) or "no reason given"
            self._close_attempts(item_id, "abandoned", reason)
            self.add_note(item_id, "reason", reason)
        return True, (f"Released {row['name']} — {row['title']} back to "
                      f"{self.cfg.initial_state}: {reason}.")

    def pause(self, item_id: int, token: str, question: str) -> tuple[bool, str]:
        with db.transaction(self.conn):
            row = self._owned(item_id, token)
            if row is None:
                return False, FENCED
            if not self._fenced(item_id, token, f"state = :state, {CLEAR}",
                                {"state": self.cfg.paused_state}):
                return False, FENCED
            _upsert_question(self.conn, question, source=row["name"])
            self.add_note(item_id, "question", one_line(question) or question)
            self._close_attempts(item_id, "abandoned", "paused for an answer")
            self.add_note(item_id, "state", f"{self.cfg.working_state} -> {self.cfg.paused_state}")
        return True, (f"Put {row['name']} on hold and saved the question; it stays out of "
                      f"the queue until an answer exists.")

    def begin_submission(self, item_id: int, token: str, worker_id: str) -> tuple[bool, str]:
        with db.transaction(self.conn):
            row = self._owned(item_id, token)
            if row is None:
                return False, FENCED
            self._attempt(item_id, worker_id, "submitting", "irreversible step started")
            self.add_note(item_id, "worker", "submission started (at-most-once)")
        return True, (f"Submission started for {row['name']} — {row['title']}; if this worker "
                      f"dies the item goes to review, never back to the queue.")

    def mark_sent(self, item_id: int, worker_id: str) -> None:
        self.conn.execute(
            "UPDATE attempts SET status = 'sent', ended_at = datetime('now') WHERE id = "
            "(SELECT id FROM attempts WHERE item_id = ? AND worker_id = ? AND status = 'submitting' "
            "ORDER BY id DESC LIMIT 1)", (item_id, worker_id))

    def archive(self, item_id: int, *, token: str | None = None,
                reason: str | None = None) -> tuple[bool, str]:
        with db.transaction(self.conn):
            row = self.get(item_id)
            if row is None:
                return False, f"No item {item_id}."
            if token is not None and (row["claim_token"] != token or row["state"] != self.cfg.working_state):
                return False, FENCED
            reason = one_line(reason) or "abandoned"
            self.conn.execute(
                f"UPDATE items SET archived_at = datetime('now'), archive_reason = ?, {CLEAR} "
                f"WHERE id = ?", (reason, item_id))
            self._close_attempts(item_id, "abandoned", reason)
            self.add_note(item_id, "reason", reason)
        return True, f"Archived {row['name']}: {reason}"

    # -- reaper ------------------------------------------------------------
    def _requeue_expired(self) -> dict[str, int]:
        cfg = self.cfg
        rows = self.conn.execute(
            """
            SELECT i.id, (SELECT 1 FROM attempts a WHERE a.item_id = i.id
                          AND a.status IN ('submitting', 'sent')
                          ORDER BY a.id DESC LIMIT 1) AS submitting
              FROM items i
             WHERE i.state = :working AND i.archived_at IS NULL
               AND (i.claimed_until IS NULL OR i.claimed_until < datetime('now'))
            """, {"working": cfg.working_state}).fetchall()
        requeued = reviewed = 0
        for row in rows:
            if row["submitting"]:
                # An irreversible step may have run: never retry automatically.
                state, note = cfg.review_state, "lease expired with a submission in flight; needs review"
                reviewed += 1
            else:
                state, note = cfg.initial_state, "lease expired, requeued"
                self._close_attempts(row["id"], "failed", "lease expired")
                requeued += 1
            self.conn.execute(f"UPDATE items SET state = ?, {CLEAR} WHERE id = ?", (state, row["id"]))
            self.add_note(row["id"], "worker", note)
        return {"requeued": requeued, "review": reviewed}

    def unpause_answered(self) -> int:
        """A paused item whose question now has an answer re-enters the queue."""
        rows = self.conn.execute(
            """
            SELECT i.id, n.body AS question FROM items i
              JOIN notes n ON n.item_id = i.id AND n.kind = 'question'
             WHERE i.state = :paused AND i.archived_at IS NULL
             ORDER BY n.id DESC
            """, {"paused": self.cfg.paused_state}).fetchall()
        unpaused, seen = 0, set()
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            if self.conn.execute("SELECT 1 FROM questions WHERE question_key = ? "
                                 "AND answer IS NOT NULL", (name_key(row["question"]),)).fetchone():
                self.conn.execute(f"UPDATE items SET state = ?, {CLEAR} WHERE id = ?",
                                  (self.cfg.initial_state, row["id"]))
                self.add_note(row["id"], "worker", "question answered, requeued")
                unpaused += 1
        return unpaused

    def reap(self) -> dict[str, Any]:
        with db.transaction(self.conn):
            expired = self._requeue_expired()
            return {**expired, "unpaused": self.unpause_answered()}

    # -- status ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        cfg = self.cfg
        row = lambda sql, *args: [dict(r) for r in self.conn.execute(sql, args)]  # noqa: E731
        counts = {r["state"]: r["n"] for r in row(
            "SELECT state, COUNT(*) AS n FROM items WHERE archived_at IS NULL GROUP BY state")}
        in_flight = row("SELECT id, name, claimed_by, claimed_until FROM items WHERE state = ? "
                        "AND archived_at IS NULL ORDER BY id", cfg.working_state)
        return {
            "counts": counts,
            "queued": counts.get(cfg.initial_state, 0),
            "archived": self.conn.execute(
                "SELECT COUNT(*) FROM items WHERE archived_at IS NOT NULL").fetchone()[0],
            "in_flight": in_flight,
            "stale": [r for r in in_flight
                      if r["claimed_until"] is None or r["claimed_until"] < db.now()],
            "review": row("SELECT id, name, title FROM items WHERE state = ? "
                          "AND archived_at IS NULL", cfg.review_state),
            "paused_waiting": row(
                "SELECT i.id, i.name, n.body AS question FROM items i JOIN notes n "
                "ON n.item_id = i.id AND n.kind = 'question' WHERE i.state = ? "
                "AND i.archived_at IS NULL ORDER BY i.id, n.id DESC", cfg.paused_state),
            "questions_open": self.conn.execute(
                "SELECT COUNT(*) FROM questions WHERE answer IS NULL").fetchone()[0],
        }


# --- answers --------------------------------------------------------------
def tokens_of(query: str | None) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z]+", (query or "").lower()) if t]


def answer_score(question: str, tokens: Sequence[str]) -> int:
    """3 per matching token + 1 when every token is present."""
    if not tokens:
        return 0
    hits = sum(1 for token in tokens if token in question.lower())
    return 3 * hits + (1 if hits == len(tokens) else 0)


class AnswerStore:
    """`lookup_answer` without Org completions: token scoring over `questions`."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config | None = None):
        self.conn, self.cfg = conn, cfg or default_config()

    def add(self, question: str, answer: str | None = None, *, source: str | None = None,
            note: str | None = None) -> dict[str, Any]:
        key = _upsert_question(self.conn, question, answer=answer, source=source, note=note)
        if answer is not None:
            self.conn.execute("UPDATE questions SET answer = ?, answered_at = datetime('now') "
                              "WHERE question_key = ?", (answer, key))
        return {"question_key": key, "answered": answer is not None}

    def answer(self, ident: str | int, text: str) -> dict[str, Any]:
        """Answer by id or by question wording."""
        if str(ident).isdigit():
            cur = self.conn.execute("UPDATE questions SET answer = ?, "
                                    "answered_at = datetime('now') WHERE id = ?", (text, int(ident)))
        else:
            cur = self.conn.execute("UPDATE questions SET answer = ?, "
                                    "answered_at = datetime('now') WHERE question_key = ?",
                                    (text, name_key(ident)))
        return {"updated": cur.rowcount}

    def rows(self, *, answered_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM questions" + (" WHERE answer IS NOT NULL" if answered_only else "")
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY id")]

    def search(self, query: str | None = None, *, limit: int | None = None) -> str:
        limit = self.cfg.answers_limit if limit is None else limit
        tokens = tokens_of(query)
        rows = [r for r in self.rows() if not tokens or answer_score(r["question"], tokens)]
        rows.sort(key=lambda r: (r["answered_at"] or "", r["id"]), reverse=True)
        rows.sort(key=lambda r: answer_score(r["question"], tokens), reverse=True)
        rows = rows[:limit] if limit is not None else rows
        if not rows:
            return f"No stored answer matches {query!r}." if query else "No stored answers yet."
        blocks = [f"{i}. {r['question']}\n- {r['answer']}" + (f"\n  Note: {r['note']}" if r["note"] else "")
                  for i, r in enumerate(rows, start=1)]
        return "\n\n".join(blocks)


# --- research targets -----------------------------------------------------
class TargetStore:
    """Stops N search workers researching the same subject."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config | None = None):
        self.conn, self.cfg = conn, cfg or default_config()

    def claim(self, name: Any, worker_id: str) -> tuple[bool, str]:
        key = name_key(name)
        if not key:
            return False, "Nothing to claim: no subject given."
        with db.transaction(self.conn):
            row = self.conn.execute("SELECT * FROM targets WHERE name_key = ?", (key,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO targets (name, name_key, state, claimed_by, claimed_until) "
                    "VALUES (?, ?, 'CLAIMED', ?, datetime('now', ?))",
                    (one_line(name), key, worker_id, self.cfg.lease_sql()))
                return True, f"Researching: {one_line(name)} (new target)."
            if row["state"] == "UNUSABLE":
                return False, f"{row['name']} is marked unusable: {row['detail'] or 'no reason'}"
            if row["state"] == "DONE":
                return False, f"{row['name']} was already researched."
            if row["state"] == "CLAIMED" and row["claimed_by"] != worker_id and \
                    (row["claimed_until"] is None or row["claimed_until"] >= db.now()):
                return False, f"Another worker is researching {row['name']} right now."
            self.conn.execute("UPDATE targets SET state = 'CLAIMED', claimed_by = ?, "
                              "claimed_until = datetime('now', ?) WHERE name_key = ?",
                              (worker_id, self.cfg.lease_sql(), key))
        return True, f"Researching: {one_line(name)}."

    def mark_unusable(self, name: Any, reason: str) -> str:
        key = name_key(name)
        if not key:
            return "Not marked: no subject given."
        with db.transaction(self.conn):
            cur = self.conn.execute("UPDATE targets SET state = 'UNUSABLE', detail = ? "
                                    "WHERE name_key = ?", (one_line(reason), key))
            if cur.rowcount == 0:
                self.conn.execute("INSERT INTO targets (name, name_key, state, detail) "
                                  "VALUES (?, ?, 'UNUSABLE', ?)", (one_line(name), key, one_line(reason)))
        return f"Marked {one_line(name)} unusable: {one_line(reason) or 'no reason given'}"


# --- host politeness ------------------------------------------------------
class HostLeases:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def acquire(self, host: str, holder: str, *, ttl_seconds: int = 120) -> bool:
        with db.transaction(self.conn):
            self.conn.execute("DELETE FROM host_leases WHERE host = ? AND expires_at < datetime('now')",
                              (host,))
            cur = self.conn.execute(
                "INSERT INTO host_leases (host, holder, expires_at) VALUES (?, ?, datetime('now', ?)) "
                "ON CONFLICT(host) DO NOTHING", (host, holder, f"+{int(ttl_seconds)} seconds"))
            return cur.rowcount == 1

    def release(self, host: str, holder: str) -> None:
        self.conn.execute("DELETE FROM host_leases WHERE host = ? AND holder = ?", (host, holder))

    def expired(self) -> list[str]:
        return [r["host"] for r in self.conn.execute(
            "SELECT host FROM host_leases WHERE expires_at < datetime('now')")]

    def purge(self) -> int:
        return self.conn.execute(
            "DELETE FROM host_leases WHERE expires_at < datetime('now')").rowcount


# --- liveness -------------------------------------------------------------
class WorkerRegistry:
    def __init__(self, conn: sqlite3.Connection, cfg: Config | None = None):
        self.conn, self.cfg = conn, cfg or default_config()

    def register(self, worker_id: str, role: str, *, pid: int | None = None,
                 host: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO workers (id, role, pid, host) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET role = excluded.role, pid = excluded.pid, "
            "heartbeat_at = datetime('now')", (worker_id, role, pid, host))

    def heartbeat(self, worker_id: str, *, item_id: int | None = None) -> None:
        self.conn.execute("UPDATE workers SET heartbeat_at = datetime('now'), item_id = ? WHERE id = ?",
                          (item_id, worker_id))

    def deregister(self, worker_id: str) -> None:
        self.conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))

    def list(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM workers ORDER BY started_at, id")]

    def dead(self, *, seconds: int | None = None) -> list[dict[str, Any]]:
        cutoff = f"-{int(seconds or self.cfg.heartbeat_seconds * 4)} seconds"
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM workers WHERE heartbeat_at < datetime('now', ?)", (cutoff,))]

    def purge_dead(self, *, seconds: int | None = None) -> int:
        cutoff = f"-{int(seconds or self.cfg.heartbeat_seconds * 4)} seconds"
        return self.conn.execute(
            "DELETE FROM workers WHERE heartbeat_at < datetime('now', ?)", (cutoff,)).rowcount


def open_stores(path: str | Path, cfg: Config | None = None) -> dict[str, Any]:
    """One connection with every store hung off it (the CLI's whole entry point)."""
    cfg = cfg or default_config()
    conn = db.open_db(path)
    return {"conn": conn, "items": ItemStore(conn, cfg), "answers": AnswerStore(conn, cfg),
            "targets": TargetStore(conn, cfg), "hosts": HostLeases(conn),
            "workers": WorkerRegistry(conn, cfg)}
