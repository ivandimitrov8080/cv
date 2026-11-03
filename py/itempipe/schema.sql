-- itempipe: the schema, in full.
--
-- `itempipe init` executes this file exactly as written. There are no migrations, no
-- ALTERs and no conditional DDL: the file has no memory of any earlier shape, and the
-- database either matches it or the tools fail on their first query. Bring a stale
-- development database up to date by deleting it and running `itempipe init` again.
--
-- The models in itempipe/__init__.py must agree with the tables below; that is the only
-- coupling. Everything else here — indexes, keys, WHERE clauses — is declared once.
--
--     sqlite3 py/itempipe.db < itempipe/schema.sql     # or: itempipe init
--
-- Note that journal_mode and busy_timeout are per-connection settings and live in the
-- SqliteDatabase constructor, not here.

-- A lead. `submitting` is the at-most-once flag; the claim token fences every write.
-- name_key and url are both unique: one company, one posting, however many workers race.
CREATE TABLE item (
  id             INTEGER      NOT NULL PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  name_key       VARCHAR(255) NOT NULL,   -- lowercased name
  title          VARCHAR(255) NOT NULL,
  url            VARCHAR(255) NOT NULL,   -- the posting's own URL
  notes          TEXT,
  state          VARCHAR(255) NOT NULL,   -- NEW | WORKING | ACTIVE | HOLD | NEEDS_REVIEW
  submitting     INTEGER      NOT NULL,
  advanced_on    DATETIME,
  archived_at    DATETIME,
  archive_reason VARCHAR(255),
  claim_token    VARCHAR(255),
  claimed_until  DATETIME
);

CREATE UNIQUE INDEX item_name_key ON item (name_key);
CREATE UNIQUE INDEX item_url_key  ON item (url);
CREATE INDEX item_state_idx ON item (state, id) WHERE archived_at IS NULL;

-- Where the workers have been: one row per page or query, shared by every session.
-- Not a lead — nothing claims or applies to a `seen` row. Its job is to stop the tenth
-- worker opening the page the first one already read, and to outlive the session that
-- read it. `key` is a normalised URL or query: the dedupe key.
CREATE TABLE seen (
  id         INTEGER      NOT NULL PRIMARY KEY,
  key        VARCHAR(255) NOT NULL,
  kind       VARCHAR(255) NOT NULL,   -- url | query | board
  url        TEXT,                    -- as first written, for the brief
  verdict    VARCHAR(255) NOT NULL,   -- claimed | open | dead | recorded | ...
  note       VARCHAR(255),            -- HTTP status, hit count, or the company
  label      VARCHAR(255),            -- which task queue entry wrote it
  session    VARCHAR(255),            -- the session that last wrote the verdict
  checked_at DATETIME     NOT NULL
);

CREATE UNIQUE INDEX seen_key ON seen (key);

-- One question a held lead is waiting on: the link, never the question. The question
-- itself lives in application-questions.org and nowhere else.
CREATE TABLE hold (
  id   INTEGER      NOT NULL PRIMARY KEY,
  item INTEGER      NOT NULL,         -- the lead, by id
  key  VARCHAR(255) NOT NULL          -- question_key() of the heading
);

CREATE UNIQUE INDEX hold_item_key ON hold (item, key);
