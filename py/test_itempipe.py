"""Queue mechanics only: no model, no browser, no network."""

import contextlib, io, tempfile, unittest
from pathlib import Path

import itempipe as ip


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ip.open_db(Path(self.tmp.name) / "t.db")
        ip.Item.delete().execute()

    def tearDown(self):
        ip.db.close()
        self.tmp.cleanup()

    def add(self, name="A", title="T", url="https://a"):
        return ip.Item.create(name=name, name_key=name.lower(), title=title, url=url)

    def tool(self, w, name):
        return next(t for t in ip.tools_for(w) if t.name == name)

    def test_create_is_unique_by_name(self):
        self.assertIsNotNone(self.add().id)
        with self.assertRaises(ip.IntegrityError):
            self.add(url="https://b")

    def test_claim_is_exclusive_and_moves_to_the_next_lead(self):
        for name in ("A", "B"):
            self.add(name, url=f"https://{name.lower()}")
        one, two = ip.Worker(), ip.Worker()
        self.assertIn("Name: A", one.claim())
        self.assertIn("Name: B", two.claim())
        self.assertEqual(two.claim(), "No leads left. Stop.")
        self.assertNotEqual(one.item.id, two.item.id)

    def test_expired_claim_returns_to_the_queue(self):
        self.add()
        one, two = ip.Worker(), ip.Worker()
        one.claim()
        ip.Item.update(claimed_until=ip.now() - ip.LEASE).execute()
        two.claim()
        self.assertEqual(two.item.name, "A")
        self.assertIsNone(one.mine())

    def test_interrupted_submit_goes_to_review_not_back_to_the_queue(self):
        self.add()
        one, two = ip.Worker(), ip.Worker()
        one.claim()
        ip.Item.update(submitting=True, claimed_until=ip.now() - ip.LEASE).execute()
        self.assertEqual(two.claim(), "No leads left. Stop.")
        self.assertEqual(ip.Item.get().state, ip.REVIEW)

    def test_applied_requires_the_submission_guard(self):
        self.add()
        w = ip.Worker()
        w.claim()
        self.assertIn("begin_submission", self.tool(w, "finish_item").invoke({"outcome": "applied"}))
        self.tool(w, "begin_submission").invoke({})
        self.tool(w, "finish_item").invoke({"outcome": "applied"})
        self.assertEqual(ip.Item.get().state, ip.ACTIVE)

    def test_fence_stops_a_worker_whose_claim_was_taken(self):
        self.add()
        w = ip.Worker()
        w.claim()
        ip.Item.update(claim_token="someone-else").execute()
        self.assertEqual(self.tool(w, "finish_item").invoke({"outcome": "archive"}), ip.FENCED)
        self.assertEqual(ip.queued(), 0)

    def test_archive_leaves_the_queue_and_keeps_the_reason(self):
        self.add()
        w = ip.Worker()
        w.claim()
        self.tool(w, "finish_item").invoke({"outcome": "archive", "reason": "closed"})
        self.assertEqual((ip.Item.get().archive_reason, ip.queued()), ("closed", 0))


class StatusTest(unittest.TestCase):
    """The board must be inert before open_board and plain off a tty."""

    def test_silent_until_opened(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ip.status.worker("w1", "call", "something")
            ip.status.finished("w1")
            ip.status.refresh()
            ip.status.close_board()
        self.assertEqual(err.getvalue(), "")

    def test_plain_lines_when_stderr_is_not_a_tty(self):
        counts = lambda: {"NEW": 2, "ACTIVE": 1}
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ip.status.open_board("itempipe test", counts)
            ip.status.worker("w1", "call", "fetch_page {'url': 'https://a'}")
            ip.status.finished("w1", "A: applied")
            ip.status.close_board()
        out = err.getvalue()
        self.assertIn("w1", out)
        self.assertIn("fetch_page", out)
        self.assertIn("A: applied", out)
        self.assertIn("2 new · 1 active", out)


class BrowserTest(unittest.TestCase):
    """One browser, one session (and so one tab) per worker."""

    def test_workers_attach_to_one_shared_browser(self):
        cfg = ip.mcp_config(ip.cdp_endpoint())
        self.assertIn("--cdp-endpoint", cfg["args"])
        self.assertNotIn("--user-data-dir", cfg["args"])   # the browser owns the profile
        self.assertEqual(ip.cdp_endpoint(9333), "http://127.0.0.1:9333")


if __name__ == "__main__":
    unittest.main()
