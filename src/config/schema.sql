CREATE TABLE item (
  id             INTEGER PRIMARY KEY,
  name           TEXT    NOT NULL,
  title          TEXT    NOT NULL,
  url            TEXT    NOT NULL,
  notes          TEXT,
  state          TEXT    NOT NULL DEFAULT 'NEW',
  submitting     INTEGER NOT NULL DEFAULT 0,
  claim_token    TEXT,
  claimed_until  DATETIME
);

CREATE UNIQUE INDEX item_url ON item (url);
CREATE INDEX item_state ON item (state, id);

CREATE TABLE memo (
  id       INTEGER PRIMARY KEY,
  key      TEXT NOT NULL,
  kind     TEXT NOT NULL,
  verdict  TEXT NOT NULL,
  note     TEXT,
  at       DATETIME NOT NULL
);

CREATE UNIQUE INDEX memo_key ON memo (key);
