-- Two tables. The database is the queue and the lock.

CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  name_key      TEXT NOT NULL UNIQUE,
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  url_key       TEXT NOT NULL,
  notes         TEXT,
  state         TEXT NOT NULL DEFAULT 'NEW',   -- NEW | WORKING | ACTIVE | PAUSED | NEEDS_REVIEW
  question      TEXT,                          -- questions.question_key while PAUSED
  submitting    INTEGER NOT NULL DEFAULT 0,    -- irreversible step started
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  advanced_on   TEXT,
  archived_at   TEXT,
  archive_reason TEXT,
  claimed_by    TEXT,
  claim_token   TEXT,
  claimed_until TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS items_state_idx ON items(state, id)
  WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS questions (
  id           INTEGER PRIMARY KEY,
  question     TEXT NOT NULL,
  question_key TEXT NOT NULL UNIQUE,
  answer       TEXT,                           -- NULL means the user was asked
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  answered_at  TEXT
);
