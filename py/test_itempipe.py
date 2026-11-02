"""Queue mechanics only: no model, no browser, no network."""

import asyncio, contextlib, io, sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

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


class SeenTest(unittest.TestCase):
    """The browsing memory a clean session starts from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ip.open_db(Path(self.tmp.name) / "t.db")
        ip.Item.delete().execute()
        ip.Seen.delete().execute()

    def tearDown(self):
        ip.db.close()
        self.tmp.cleanup()

    def test_one_page_has_one_identity(self):
        self.assertEqual(ip.url_key("https://www.Example.com/jobs/42/?utm_source=hn"),
                         ip.url_key("http://example.com/jobs/42"))
        self.assertNotEqual(ip.url_key("https://a.com/jobs/42"),
                            ip.url_key("https://a.com/jobs/43"))

    def test_one_query_has_one_identity(self):
        self.assertEqual(ip.query_key("  Remote Java "), ip.query_key("remote  java!"))

    def test_two_sessions_cannot_check_one_page_at_once(self):
        key = ip.url_key("https://a.com/1")
        self.assertTrue(ip.claim_seen(key, session="s1")[0])
        won, row = ip.claim_seen(key, session="s2")
        self.assertFalse(won)
        self.assertEqual(row.verdict, ip.CLAIMED)

    def test_a_settled_page_comes_back_in_the_next_session(self):
        key = ip.url_key("https://a.com/1")
        ip.claim_seen(key, session="s1")
        ip.settle(key, ip.OPEN, "HTTP 200", "s1")
        self.assertFalse(ip.claim_seen(key, session="s1")[0])   # not twice in one session
        self.assertTrue(ip.claim_seen(key, session="s2")[0])    # a new one may look again

    def test_a_dead_page_waits_a_week_and_a_recorded_one_never_returns(self):
        dead, kept = ip.url_key("https://a.com/dead"), ip.url_key("https://a.com/kept")
        ip.claim_seen(dead, session="s1")
        ip.settle(dead, ip.DEAD, "HTTP 404", "s1")
        ip.claim_seen(kept, session="s1")
        ip.settle(kept, ip.RECORDED, "Acme", "s1")
        self.assertFalse(ip.claim_seen(dead, session="s2")[0])
        ip.Seen.update(checked_at=ip.now() - ip.RETRY - timedelta(minutes=1)).where(
            ip.Seen.key == dead).execute()
        self.assertTrue(ip.claim_seen(dead, session="s2")[0])
        ip.Seen.update(checked_at=ip.now() - timedelta(days=500)).where(
            ip.Seen.key == kept).execute()
        self.assertFalse(ip.claim_seen(kept, session="s2")[0])

    def test_a_session_hands_its_unrecorded_pages_to_the_next_one(self):
        sess = ip.Session(angle="x")
        key = ip.url_key("https://a.com/1")
        ip.claim_seen(key, session=sess.token)
        ip.settle(key, ip.OPEN, "HTTP 200", sess.token)
        self.assertEqual(ip.reconcile(sess), 1)
        self.assertEqual(ip.Seen.get(ip.Seen.key == key).verdict, ip.SKIPPED)
        self.assertTrue(ip.claim_seen(key, session="another")[0])

    def test_the_frontier_moves_on_once_a_board_is_opened(self):
        with mock.patch.dict(ip.ANGLE_BOARDS, {"x": ["https://a.com", "https://b.com"]}):
            self.assertEqual(ip.frontier("x", offset=1), ["https://b.com", "https://a.com"])
            ip.claim_seen(ip.url_key("https://b.com"), session="s1")
            self.assertEqual(ip.frontier("x"), ["https://a.com"])

    def test_an_angle_with_no_boards_is_left_to_the_model(self):
        with mock.patch.dict(ip.ANGLE_BOARDS, {"x": []}):
            self.assertEqual(ip.frontier("x"), [])
            boards, queries, _ = ip.plan_for("x", 0, 1, 0)
            self.assertEqual(boards, [])
            self.assertEqual(len(queries), 3)    # the query space keeps it going

    def test_the_contract_angle_searches_for_contracts(self):
        self.assertIn("contract", " ".join(ip.space_for("freelance / contract")[:8]))

    def test_a_board_comes_back_after_its_ttl(self):
        with mock.patch.dict(ip.ANGLE_BOARDS, {"x": ["https://a.com/board"]}):
            key = ip.url_key("https://a.com/board")
            self.assertTrue(ip.is_board("https://a.com/board"))
            self.assertFalse(ip.is_board("https://a.com/posting"))
            ip.claim_seen(key, kind=ip.BOARD, session="s1")
            ip.settle(key, ip.SKIPPED, "read", "s1", kind=ip.BOARD)
            self.assertEqual(ip.frontier("x"), [])
            ip.Seen.update(checked_at=ip.now() - ip.BOARD_TTL - timedelta(minutes=1)).execute()
            self.assertEqual(ip.frontier("x"), ["https://a.com/board"])

    def test_the_query_space_never_runs_out_of_new_queries(self):
        self.assertGreater(len(ip.space_for("x")), 500)
        first, cursor = ip.next_queries("x", 0, 2)
        self.assertEqual(len(first), 2)
        for query in first:                 # once run, they are in `seen`
            ip.claim_seen(ip.query_key(query), kind="query", url=query, session="s1")
        second, _ = ip.next_queries("x", cursor, 2)
        self.assertFalse(set(first) & set(second))

    def test_workers_start_at_different_queries(self):
        starts = {ip.starting_cursor(i, 10, "x") for i in range(10)}
        self.assertEqual(len(starts), 10)

    def test_a_plan_prefers_boards_then_fills_with_queries(self):
        with mock.patch.dict(ip.ANGLE_BOARDS, {"x": ["https://a.com/board"]}):
            boards, queries, _ = ip.plan_for("x", 0, 1, 0)
            self.assertEqual(boards, ["https://a.com/board"])
            self.assertEqual(len(queries), 2)

    def test_existing_leads_are_remembered_as_recorded(self):
        ip.Item.create(name="Acme", name_key="acme", title="T", url="https://a.com/1")
        self.assertEqual(ip.remember_leads(), 1)
        self.assertEqual(ip.Seen.get(ip.Seen.key == ip.url_key("https://a.com/1")).verdict,
                         ip.RECORDED)
        self.assertEqual(ip.remember_leads(), 0)     # idempotent
        self.assertFalse(ip.claim_seen(ip.url_key("https://a.com/1"), session="s2")[0])

    def test_a_recorded_url_cannot_come_back_under_another_name(self):
        def create_item(sess):
            return next(t for t in ip.tools_for(sess=sess) if t.name == "create_item")
        self.assertIn("Recorded", create_item(ip.Session(angle="x")).invoke(
            {"name": "Acme", "title": "T", "url": "https://a.com/1"}))
        self.assertIn("Already recorded", create_item(ip.Session(angle="x")).invoke(
            {"name": "Acme Two", "title": "T", "url": "https://a.com/1/"}))

    def test_a_session_stops_after_its_one_lead(self):
        sess = ip.Session(angle="x")
        tools = {t.name: t for t in ip.tools_for(sess=sess)}
        tools["create_item"].invoke(
            {"name": "Acme", "title": "T", "url": "https://a.com/1"})
        self.assertEqual(sess.recorded, ["Acme"])
        self.assertIn("SESSION OVER", tools["create_item"].invoke(
            {"name": "Other", "title": "T", "url": "https://b.com/2"}))
        self.assertIn("SESSION OVER", asyncio.run(
            tools["fetch_page"].ainvoke({"url": "https://b.com/2"})))


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

    def test_stopping_a_dead_process_is_not_an_error(self):
        # The old code called kill() on a browser that had already exited, which raised
        # ProcessLookupError instead of saying why chromium refused to start.
        async def gone():
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "pass",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            await ip.stop_proc(proc)
            return proc.returncode
        self.assertEqual(asyncio.run(gone()), 0)

    def test_the_log_is_translated_into_a_fix(self):
        self.assertIn("in use", ip.chrome_hint("Failed to create SingletonLock"))
        self.assertIn("--no-sandbox", ip.chrome_hint("Running as root without --no-sandbox"))
        self.assertIn("display", ip.chrome_hint("Missing X server or $DISPLAY"))
        self.assertIn("9222", ip.chrome_hint("Address already in use"))
        self.assertIn("No recognised cause", ip.chrome_hint(""))


class WebTest(unittest.TestCase):
    """A dead search must come back as a sentence the agent can act on, not an exception."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ip.open_db(Path(self.tmp.name) / "t.db")
        ip.Seen.delete().execute()

    def tearDown(self):
        ip.db.close()
        self.tmp.cleanup()

    def tool(self, name):
        return next(t for t in ip.tools_for() if t.name == name)

    def search_raising(self, message):
        from ddgs.exceptions import DDGSException
        import ddgs
        fake = mock.Mock()
        fake.return_value.text.side_effect = DDGSException(message)
        with mock.patch.object(ip, "SEARCH_TRIES", 1), mock.patch.object(ddgs, "DDGS", fake):
            return asyncio.run(self.tool("web_search").ainvoke({"query": "nothing at all"}))

    def test_no_results_becomes_advice(self):
        out = self.search_raising("No results found.")
        self.assertIn("NOT searched", out)
        self.assertIn("no results for 'nothing at all'", out)
        self.assertIn("Do not repeat", out)

    def test_rate_limit_is_told_apart_from_no_results(self):
        out = self.search_raising("Ratelimit 429")
        self.assertIn("rate limiting", out)
        self.assertIn("not a failure", ip.search_failure("no results", "q", 3))

    def test_fetch_page_refuses_a_non_http_url(self):
        out = asyncio.run(self.tool("fetch_page").ainvoke({"url": "file:///etc/passwd"}))
        self.assertIn("NOT fetched", out)

    def test_hits_text_renders_rows(self):
        rows = [{"title": "T", "href": "https://a", "body": "B"}]
        self.assertEqual(ip.hits_text(rows), "T\nhttps://a\nB")

    def test_the_same_query_is_not_repeated_in_one_session(self):
        tool = self.tool("web_search")
        with mock.patch.object(ip, "SEARCH_TRIES", 1), mock.patch("ddgs.DDGS") as fake:
            fake.return_value.text.return_value = [
                {"title": "T", "href": "https://a", "body": "B"}]
            self.assertIn("https://a", asyncio.run(tool.ainvoke({"query": "java remote"})))
            self.assertIn("already checked",
                          asyncio.run(tool.ainvoke({"query": "Java,  remote!"})))


if __name__ == "__main__":
    unittest.main()
