-- itempipe schema.  Idempotent: every worker and the CLI run migrate() on
-- open, so a fresh database and an old one are the same code path.

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,               -- the subject of the item
  name_key       TEXT NOT NULL UNIQUE,        -- lower(trim(collapse(name)))
  title          TEXT NOT NULL,               -- what is being asked for
  url            TEXT NOT NULL,
  url_key        TEXT NOT NULL,               -- normalized url (dedupe hint)
  location       TEXT,
  region         TEXT,
  size           TEXT,                        -- vocabulary checked in Python
  language       TEXT,                        -- vocabulary checked in Python
  contact        TEXT,
  notes          TEXT,                        -- one-line summary
  state          TEXT NOT NULL,               -- validated against config.state_vocab
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  advanced_on    TEXT,
  follow_up_on   TEXT,
  archived_at    TEXT,
  archive_reason TEXT,
  meta_json      TEXT,                        -- everything the org import kept
  -- worker coordination
  claimed_by     TEXT,
  claim_token    TEXT,
  claimed_until  TEXT,
  attempt_count  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS items_state_idx ON items(state, id)
  WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS items_url_idx ON items(url_key);

CREATE TABLE IF NOT EXISTS notes (
  id         INTEGER PRIMARY KEY,
  item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL DEFAULT 'note',    -- note | state | reason | worker | question
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS notes_item_idx ON notes(item_id, id);

-- Audit trail for concurrent attempts. `status='submitting'` is the
-- at-most-once guard for irreversible actions.
CREATE TABLE IF NOT EXISTS attempts (
  id          INTEGER PRIMARY KEY,
  item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  worker_id   TEXT NOT NULL,
  status      TEXT NOT NULL,                  -- claimed | submitting | sent
                                              -- | submitted | abandoned | failed
  detail      TEXT,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS attempts_item_idx ON attempts(item_id, id);

-- Search-side shared TODO: stops N searchers researching the same subject.
CREATE TABLE IF NOT EXISTS targets (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  name_key      TEXT NOT NULL UNIQUE,
  url           TEXT,
  state         TEXT NOT NULL DEFAULT 'OPEN', -- OPEN | CLAIMED | DONE | UNUSABLE
  claimed_by    TEXT,
  claim_token   TEXT,
  claimed_until TEXT,
  detail        TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Host politeness: one worker per origin at a time.
CREATE TABLE IF NOT EXISTS host_leases (
  host          TEXT PRIMARY KEY,
  holder        TEXT NOT NULL,
  expires_at    TEXT NOT NULL
);

-- Liveness, so `status` can show dead workers and the reaper can be safe.
CREATE TABLE IF NOT EXISTS workers (
  id           TEXT PRIMARY KEY,
  role         TEXT NOT NULL,                 -- search | apply | supervisor
  pid          INTEGER,
  host         TEXT,
  started_at   TEXT NOT NULL DEFAULT (datetime('now')),
  heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
  item_id      INTEGER
);

CREATE TABLE IF NOT EXISTS questions (
  id           INTEGER PRIMARY KEY,
  question     TEXT NOT NULL UNIQUE,
  question_key TEXT NOT NULL UNIQUE,
  answer       TEXT,                          -- NULL = unanswered -> drives pause
  source       TEXT,
  note         TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  answered_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY,
  agent       TEXT NOT NULL,                  -- supervisor | search | apply
  worker_id   TEXT,
  brief       TEXT,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  result      TEXT,
  thread_id   TEXT                            -- LangGraph checkpointer id
);
