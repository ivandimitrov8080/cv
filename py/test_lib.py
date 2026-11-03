import dataclasses
import tempfile
import unittest
from pathlib import Path

from lib import config, db

SCHEMA = """
CREATE TABLE item (
  id            INTEGER PRIMARY KEY,
  label         TEXT    NOT NULL,
  state         TEXT    NOT NULL DEFAULT 'NEW',
  submitting    INTEGER NOT NULL DEFAULT 0,
  claim_token   TEXT,
  claimed_until DATETIME
);
CREATE UNIQUE INDEX item_label ON item (label);
CREATE TABLE memo (
  id      INTEGER PRIMARY KEY,
  key     TEXT NOT NULL,
  kind    TEXT NOT NULL,
  verdict TEXT NOT NULL,
  note    TEXT,
  at      DATETIME NOT NULL
);
CREATE UNIQUE INDEX memo_key ON memo (key);
"""

TOML = """
[db]
path = "t.db"
schema = "schema.sql"
table = "item"
key = "label"
memo = "memo"
states = { new = "NEW", working = "WORKING", review = "FAILED" }

[ingest]
prompt = "p.md"

[act]
prompt = "p.md"
outcomes = { done = "DONE", again = "FAILED" }
guarded = ["done"]
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "schema.sql").write_text(SCHEMA)
        (self.root / "p.md").write_text("prompt")
        (self.root / "tool.toml").write_text(TOML)
        self.config = config.load(self.root / "tool.toml")
        self.store = db.Store(self.config).create()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, label):
        return self.store.row.create(label=label, state=self.config.new)


class StoreTest(Base):
    def test_columns_are_the_data_columns_only(self):
        self.assertEqual(self.store.columns, ("label",))

    def test_connect_refuses_a_database_without_the_table(self):
        empty = db.Store(dataclasses.replace(self.config, db=self.root / "other.db"))
        with self.assertRaises(LookupError):
            empty.connect()

    def test_counts_by_state(self):
        self.add("a")
        self.assertEqual(self.store.counts(), {"NEW": 1})


class ClaimTest(Base):
    def test_claim_is_exclusive(self):
        self.add("a")
        one, two = db.Claim(self.store), db.Claim(self.store)
        self.assertEqual(one.take().label, "a")
        self.assertIsNone(two.take())
        self.assertEqual(one.take().label, "a")

    def test_take_returns_the_next_row(self):
        self.add("a")
        self.add("b")
        one = db.Claim(self.store)
        self.assertEqual(one.take().label, "a")
        one.end(state="DONE")
        self.assertEqual(one.take().label, "b")

    def test_expired_claim_comes_back(self):
        self.add("a")
        one, two = db.Claim(self.store), db.Claim(self.store)
        one.take()
        self.store.row.update(claimed_until=db.now() - db.timedelta(seconds=1)).execute()
        self.assertIsNone(one.mine())
        self.assertEqual(two.take().label, "a")

    def test_renew_holds_the_lease(self):
        self.add("a")
        one, two = db.Claim(self.store), db.Claim(self.store)
        one.take()
        self.store.row.update(claimed_until=db.now() - db.timedelta(seconds=1)).execute()
        self.assertTrue(one.renew())
        self.assertIsNone(two.take())

    def test_end_is_fenced_by_the_token(self):
        self.add("a")
        one = db.Claim(self.store)
        one.take()
        self.store.row.update(claim_token="someone-else").execute()
        self.assertFalse(one.end(state="DONE"))
        self.assertEqual(self.store.row.get().state, "WORKING")

    def test_guard_requires_the_claim(self):
        self.add("a")
        one = db.Claim(self.store)
        one.take()
        self.assertTrue(one.guard())
        self.store.row.update(claim_token="someone-else").execute()
        self.assertFalse(one.guard())

    def test_an_abandoned_guard_goes_to_review(self):
        self.add("a")
        one = db.Claim(self.store)
        one.take()
        one.guard()
        self.store.row.update(claimed_until=db.now() - db.timedelta(seconds=1)).execute()
        self.assertIsNone(db.Claim(self.store).take())
        self.assertEqual(self.store.row.get().state, "FAILED")


class MemoTest(Base):
    def test_claim_then_settle(self):
        memo = db.Memo(self.store)
        self.assertEqual(memo.claim("u:a", "url"), (True, None))
        won, row = db.Memo(self.store).claim("u:a", "url")
        self.assertFalse(won)
        self.assertEqual(row.verdict, "claimed")
        memo.settle("u:a", "recorded", "a")
        self.assertEqual(memo.get("u:a").verdict, "recorded")

    def test_settle_inserts_a_row_it_did_not_own(self):
        memo = db.Memo(self.store)
        self.assertFalse(memo.settle("u:b", "open", "HTTP 200"))
        self.assertEqual(memo.get("u:b").verdict, "open")

    def test_ttl_makes_a_verdict_stale_again(self):
        self.store.memo.create(key="u:a", kind="url", verdict="open", at=db.now())
        memo = db.Memo(self.store, ttl=(("url", 0),))
        self.assertTrue(memo.stale(memo.get("u:a")))
        self.assertTrue(memo.claim("u:a", "url")[0])

    def test_recorded_is_never_stale(self):
        self.store.memo.create(key="u:a", kind="url", verdict="recorded", at=db.now())
        memo = db.Memo(self.store, ttl=(("url", 0),))
        self.assertFalse(memo.stale(memo.get("u:a")))

    def test_release_drops_only_unsettled_claims(self):
        memo = db.Memo(self.store)
        memo.claim("u:a", "url")
        memo.claim("u:b", "url")
        memo.settle("u:b", "open")
        self.assertEqual(memo.release(["u:a", "u:b"]), 1)
        self.assertIsNone(memo.get("u:a"))
        self.assertIsNotNone(memo.get("u:b"))


if __name__ == "__main__":
    unittest.main()
