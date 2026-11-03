"""Queue mechanics only: no model, no browser, no network."""

import asyncio, contextlib, io, sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
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
        one, two, three = ip.Worker(), ip.Worker(), ip.Worker()
        self.assertIn("Name: A", one.claim())
        self.assertIn("Name: B", two.claim())
        self.assertIn("Already claimed", two.claim())   # B, not a third lead
        self.assertEqual(three.claim(), "No leads left. Stop.")
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

    def test_one_worker_holds_at_most_one_claim(self):
        for name in ("A", "B"):
            self.add(name, url=f"https://{name.lower()}")
        w = ip.Worker()
        self.assertIn("Name: A", w.claim())
        out = w.claim()             # a second claim must not move on to B
        self.assertIn("Already claimed", out)
        self.assertEqual(w.item.name, "A")
        self.assertEqual(ip.Item.get(ip.Item.name == "B").state, ip.NEW)

    def test_renew_keeps_a_live_claim_out_of_other_workers_hands(self):
        self.add()
        one, two = ip.Worker(), ip.Worker()
        one.claim()
        ip.Item.update(claimed_until=ip.now() - ip.LEASE).execute()
        self.assertTrue(one.renew())
        self.assertEqual(ip.claimable(), 0)
        self.assertEqual(two.claim(), "No leads left. Stop.")

    def test_an_expired_unfinished_claim_is_still_claimable(self):
        self.add()
        ip.Worker().claim()
        ip.Item.update(claimed_until=ip.now() - ip.LEASE).execute()
        self.assertEqual(ip.queued(), 0)          # NEW only
        self.assertEqual(ip.claimable(), 1)       # but free() can still pick it up

    def test_begin_submission_is_fenced_after_the_claim_is_taken(self):
        self.add()
        w = ip.Worker()
        w.claim()
        ip.Item.update(claim_token="someone-else").execute()
        self.assertEqual(self.tool(w, "begin_submission").invoke({}), ip.FENCED)
        self.assertFalse(ip.Item.get().submitting)

    def test_applied_is_fenced_if_the_claim_was_taken_before_finishing(self):
        self.add()
        w = ip.Worker()
        w.claim()
        self.tool(w, "begin_submission").invoke({})
        ip.Item.update(claim_token="someone-else").execute()
        self.assertEqual(self.tool(w, "finish_item").invoke({"outcome": "applied"}),
                         ip.FENCED)
        row = ip.Item.get()
        self.assertEqual((row.state, row.submitting), (ip.WORKING, True))

    def test_heartbeat_renews_a_claim_while_the_agent_is_running(self):
        self.add()
        w = ip.Worker()
        w.claim()
        ip.Item.update(claimed_until=ip.now() - ip.LEASE).execute()

        async def beat():
            task = asyncio.create_task(ip.heartbeat(w))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        with mock.patch.object(ip, "RENEW", 0.01):
            asyncio.run(beat())
        self.assertGreater(ip.Item.get().claimed_until, ip.now())
        self.assertFalse(ip.claimable())


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

    def test_existing_leads_are_remembered_as_recorded(self):
        ip.Item.create(name="Acme", name_key="acme", title="T", url="https://a.com/1")
        self.assertEqual(ip.remember_leads(), 1)
        self.assertEqual(ip.Seen.get(ip.Seen.key == ip.url_key("https://a.com/1")).verdict,
                         ip.RECORDED)
        self.assertEqual(ip.remember_leads(), 0)     # idempotent
        self.assertFalse(ip.claim_seen(ip.url_key("https://a.com/1"), session="s2")[0])

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
        sess = ip.Session()
        key = ip.url_key("https://a.com/1")
        ip.claim_seen(key, session=sess.token)
        ip.settle(key, ip.OPEN, "HTTP 200", sess.token)
        self.assertEqual(ip.reconcile(sess), 1)
        self.assertEqual(ip.Seen.get(ip.Seen.key == key).verdict, ip.SKIPPED)
        self.assertTrue(ip.claim_seen(key, session="another")[0])

    def test_a_run_with_no_default_boards_falls_back_to_queries(self):
        with mock.patch.object(ip, "SEED_TASKS", ()):
            tasks = ip.next_tasks(ip.Session(tag="w1"))
            self.assertTrue(tasks)
            self.assertTrue(all(task.kind == ip.QUERY for task in tasks))

    def test_the_query_space_is_built_only_from_the_config(self):
        with mock.patch.object(ip, "ROLES", ("quantum welder",)), \
             mock.patch.object(ip, "SENIORITY", ("",)), \
             mock.patch.object(ip, "PLACES", ("mars",)), \
             mock.patch.object(ip, "PHRASES", ("hiring",)), \
             mock.patch.object(ip, "CONTRACT", ()), \
             mock.patch.object(ip, "DOMAINS", ()), \
             mock.patch.object(ip, "SITES", ()), \
             mock.patch.object(ip, "CITIES", ()):
            self.assertEqual(list(ip.query_space()), ["quantum welder mars hiring"])

    def test_domains_widen_the_roles_without_editing_code(self):
        with mock.patch.object(ip, "ROLES", ("backend engineer",)), \
             mock.patch.object(ip, "DOMAINS", ("crypto",)), \
             mock.patch.object(ip, "SENIORITY", ("",)), \
             mock.patch.object(ip, "PLACES", ("remote europe",)), \
             mock.patch.object(ip, "PHRASES", ("hiring",)), \
             mock.patch.object(ip, "CONTRACT", ()), \
             mock.patch.object(ip, "SITES", ()), \
             mock.patch.object(ip, "CITIES", ()):
            space = list(ip.query_space())
        self.assertIn("backend engineer crypto remote europe hiring", space)
        self.assertIn("backend engineer remote europe hiring", space)

    def test_the_config_is_a_file_and_says_what_it_holds(self):
        path = Path(self.tmp.name) / "search.toml"
        path.write_text(
            "[vars]\nhn = '42'\n\n"
            "[search]\nroles = ['quantum welder']\nnotes = 'mars only'\n"
            "phrases = ['hiring']\n\n"
            "[search.regions]\nremote = ['mars']\n\n"
            "[[boards]]\nlabel = 'HN'\nurls = ['https://a.com/{hn}']\n",
            encoding="utf-8")
        cfg = ip.load_config(path)
        self.assertEqual(cfg.roles, ("quantum welder",))
        self.assertEqual(cfg.places, ("mars",))
        self.assertEqual(cfg.notes, "mars only")
        self.assertEqual(cfg.boards, (ip.Task(ip.BOARD, "https://a.com/42", "HN"),))

    def test_a_missing_config_is_empty_rather_than_invented(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            cfg = ip.load_config(Path(self.tmp.name) / "absent.toml")
        self.assertEqual((cfg.roles, cfg.places, cfg.boards), ((), (), ()))
        self.assertIn("no search config", err.getvalue())

    def test_the_contract_option_is_in_the_query_space(self):
        space = ip.space_for()
        self.assertTrue(any("contract" in query for query in space))
        self.assertTrue(any("remote europe" in query for query in space))

    def test_the_query_space_covers_more_than_one_region(self):
        space = ip.space_for()
        for place in ("germany", "united kingdom", "united states", "singapore",
                      "bulgaria", "remote worldwide"):
            self.assertTrue(any(place in query for query in space), place)
        # and the regions alternate rather than arriving in one block
        self.assertGreater(len(set(ip.PLACES[:12])), 8)

    def test_a_board_task_is_not_repeated_by_the_next_session(self):
        with mock.patch.object(ip, "SEED_TASKS",
                               (ip.Task(ip.BOARD, "https://a.com/board", "x"),)):
            first, second = ip.Session(tag="w1"), ip.Session(tag="w2")
            tasks = ip.next_tasks(first, boards=1)
            key = ip.url_key("https://a.com/board")
            ip.settle(key, ip.OPEN, "HTTP 200", first.token, kind=ip.BOARD)
            self.assertIn(ip.BOARD, [t.kind for t in tasks])
            self.assertNotIn(ip.BOARD, [t.kind for t in ip.next_tasks(second, boards=1)])

    def test_a_board_task_is_taken_by_one_worker_only(self):
        with mock.patch.object(ip, "SEED_TASKS",
                               (ip.Task(ip.BOARD, "https://a.com/board", "x"),)):
            one, two = ip.Session(tag="w1"), ip.Session(tag="w2")
            self.assertIn(ip.BOARD, [t.kind for t in ip.next_tasks(one, boards=1)])
            self.assertNotIn(ip.BOARD, [t.kind for t in ip.next_tasks(two, boards=1)])

    def test_a_task_claimed_but_never_used_is_handed_back(self):
        sess = ip.Session(tag="w1")
        key = ip.url_key("https://a.com/board")
        ip.claim_seen(key, kind=ip.BOARD, url="https://a.com/board", session=sess.token)
        self.assertEqual(ip.reconcile(sess), 1)          # released, not left claimed
        self.assertIsNone(ip.Seen.get_or_none(ip.Seen.key == key))

    def test_a_board_comes_back_after_its_ttl(self):
        key = ip.url_key("https://a.com/board")
        ip.claim_seen(key, kind=ip.BOARD, url="https://a.com/board", session="s1")
        ip.settle(key, ip.SKIPPED, "read", "s1", kind=ip.BOARD)
        self.assertFalse(ip.claim_seen(key, session="s2")[0])
        ip.Seen.update(checked_at=ip.now() - ip.BOARD_TTL - timedelta(minutes=1)).execute()
        self.assertTrue(ip.claim_seen(key, session="s2")[0])

    def test_a_query_comes_back_after_its_ttl(self):
        key = ip.query_key("java remote")
        ip.claim_seen(key, kind=ip.QUERY, url="java remote", session="s1")
        ip.settle(key, ip.SEARCHED, "8 hits", "s1", kind=ip.QUERY)
        self.assertFalse(ip.claim_seen(key, session="s2")[0])
        ip.Seen.update(checked_at=ip.now() - ip.QUERY_TTL - timedelta(minutes=1)).execute()
        self.assertTrue(ip.claim_seen(key, session="s2")[0])

    def test_a_recorded_url_cannot_come_back_under_another_name(self):
        def create_item(sess):
            return next(t for t in ip.tools_for(sess=sess) if t.name == "create_item")
        self.assertIn("Recorded", create_item(ip.Session()).invoke(
            {"name": "Acme", "title": "T", "url": "https://a.com/1"}))
        self.assertIn("Already recorded", create_item(ip.Session()).invoke(
            {"name": "Acme Two", "title": "T", "url": "https://a.com/1/"}))

    def test_a_session_stops_after_its_one_lead(self):
        sess = ip.Session()
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
    """One browser and one MCP session; one tab per worker inside it.

    Unit level only: these read the data handed to the MCP client and the error paths.
    Nothing starts chromium, spawns playwright-mcp or speaks MCP — `itempipe apply` is
    how those get exercised, by hand.
    """

    def test_workers_attach_to_one_shared_browser(self):
        # `mcp_config` is data only: the fields `mcp.StdioServerParameters` wants, so the
        # one playwright-mcp server attaches to the shared CDP browser and owns no profile
        # of its own. Nothing here starts a browser or speaks MCP.
        cfg = ip.mcp_config(ip.cdp_endpoint())
        self.assertEqual(cfg["command"], "playwright-mcp")
        self.assertIn("--cdp-endpoint", cfg["args"])
        self.assertIn(ip.cdp_endpoint(), cfg["args"])
        self.assertNotIn("--user-data-dir", cfg["args"])   # the browser owns the profile
        self.assertNotIn("--shared-browser-context", cfg["args"])  # tab pool, not HTTP
        self.assertEqual(ip.cdp_endpoint(9333), "http://127.0.0.1:9333")

    def test_the_tab_listing_parser_knows_the_current_tab(self):
        listing = "- 0: [](about:blank)\n- 1: (current) [Acme](https://acme.dev)"
        self.assertEqual(ip.tab_indices(listing), [0, 1])
        self.assertEqual(ip.current_tab(listing), 1)
        self.assertIsNone(ip.current_tab("no tabs"))

    def playwright(self):
        """A stand-in for one playwright-mcp client with real tab bookkeeping."""
        class Client:
            def __init__(self):
                self.tabs, self.current, self.calls = ["about:blank"], 0, []

            def listing(self):
                return "\n".join(
                    f"- {i}: {'(current) ' if i == self.current else ''}"
                    f"[{url}]({url})" for i, url in enumerate(self.tabs))

            async def call_tool(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if name == "browser_tabs":
                    action = arguments["action"]
                    if action == "new":
                        self.tabs.append("about:blank")
                        self.current = len(self.tabs) - 1
                        text = "new tab snapshot"
                    elif action == "select":
                        self.current = arguments["index"]
                        text = self.listing()
                    else:
                        text = self.listing()
                elif name == "browser_click" and arguments.get("target") == "popup":
                    self.tabs.append("https://popup")
                    self.current = len(self.tabs) - 1      # target=_blank lands here
                    text = "popup opened"
                else:
                    text = f"{name}@{self.current}"
                return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)
        return Client()

    def remote_tools(self):
        return [SimpleNamespace(name="browser_navigate", description="Go",
                                inputSchema={"type": "object", "properties": {}}),
                SimpleNamespace(name="browser_click", description="Click",
                                inputSchema={"type": "object", "properties": {}}),
                SimpleNamespace(name="browser_tabs", description="Tabs",
                                inputSchema={"type": "object", "properties": {}}),
                SimpleNamespace(name="browser_close", description="Close",
                                inputSchema={"type": "object", "properties": {}})]

    def test_two_workers_get_two_tabs_in_one_browser(self):
        client = self.playwright()

        async def claim():
            shared = ip.SharedBrowser(client, self.remote_tools())
            return await shared.claim_tab(), await shared.claim_tab()

        first, second = asyncio.run(claim())
        self.assertEqual((first.index, second.index), (0, 1))
        self.assertEqual(len(client.tabs), 2)       # one browser, two tabs
        self.assertEqual(len({id(tab.browser) for tab in (first, second)}), 1)

    def test_each_workers_tool_call_selects_its_own_tab(self):
        client = self.playwright()

        async def drive():
            shared = ip.SharedBrowser(client, self.remote_tools())
            first, second = await shared.claim_tab(), await shared.claim_tab()
            one = {tool.name: tool for tool in shared.tools(first)}
            two = {tool.name: tool for tool in shared.tools(second)}
            a = await one["browser_navigate"].ainvoke({"url": "https://a"})
            b = await two["browser_navigate"].ainvoke({"url": "https://b"})
            c = await one["browser_navigate"].ainvoke({"url": "https://c"})
            return a, b, c

        self.assertEqual(asyncio.run(drive()),
                         ("browser_navigate@0", "browser_navigate@1",
                          "browser_navigate@0"))
        selects = [call for call in client.calls
                   if call[0] == "browser_tabs" and call[1]["action"] == "select"]
        self.assertEqual([call[1]["index"] for call in selects[-3:]], [0, 1, 0])

    def test_calls_through_the_shared_client_never_overlap(self):
        # The stdio MCP client has one pipe and one "current tab"; two workers' calls
        # must therefore be serialised, not interleaved, or they would race each other.
        inner = self.playwright()

        class Slow:
            def __init__(self):
                self.peak, self.racing = 0, 0

            async def call_tool(self, name, arguments):
                self.racing += 1
                self.peak = max(self.peak, self.racing)
                try:
                    await asyncio.sleep(0.005)
                    return await inner.call_tool(name, arguments)
                finally:
                    self.racing -= 1

        client = Slow()

        async def drive():
            shared = ip.SharedBrowser(client, self.remote_tools())
            tabs = [await shared.claim_tab() for _ in range(2)]
            calls = {i: {tool.name: tool for tool in shared.tools(tab)}
                     for i, tab in enumerate(tabs)}
            await asyncio.gather(*(calls[n % 2]["browser_navigate"].ainvoke(
                {"url": f"https://{n}"}) for n in range(6)))

        asyncio.run(drive())
        self.assertEqual(client.peak, 1)

    def test_a_workers_tab_follows_it_to_a_popup(self):
        client = self.playwright()

        async def drive():
            shared = ip.SharedBrowser(client, self.remote_tools())
            first, second = await shared.claim_tab(), await shared.claim_tab()
            mine = {tool.name: tool for tool in shared.tools(first)}
            other = {tool.name: tool for tool in shared.tools(second)}
            await mine["browser_click"].ainvoke({"target": "popup"})
            here = await mine["browser_navigate"].ainvoke({"url": "https://a"})
            there = await other["browser_navigate"].ainvoke({"url": "https://b"})
            return first.index, second.index, here, there

        first, second, here, there = asyncio.run(drive())
        self.assertEqual((first, second), (2, 1))       # worker A now drives the popup
        self.assertEqual((here, there), ("browser_navigate@2", "browser_navigate@1"))

    def test_tab_management_tools_are_not_offered_to_the_model(self):
        shared = ip.SharedBrowser(self.playwright(), self.remote_tools())
        tab = asyncio.run(shared.claim_tab())
        self.assertEqual({tool.name for tool in shared.tools(tab)},
                         {"browser_navigate", "browser_click"})

    def test_a_missing_mcp_client_is_a_sentence_not_a_traceback(self):
        async def enter():
            with mock.patch.dict(sys.modules, {"mcp": None}):
                async with ip.browser(ip.cdp_endpoint()):
                    pass
        with self.assertRaises(ip.Startup) as caught:
            asyncio.run(enter())
        self.assertIn("mcp", str(caught.exception))

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


class ApplyTest(unittest.TestCase):
    """The apply loop itself: one browser, one tab per worker, one claim per agent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ip.open_db(Path(self.tmp.name) / "t.db")
        ip.Item.delete().execute()

    def tearDown(self):
        ip.db.close()
        self.tmp.cleanup()

    def add(self, name):
        return ip.Item.create(name=name, name_key=name.lower(), title="T",
                              url=f"https://{name.lower()}.dev/1")

    def test_two_workers_share_one_browser_and_get_one_tab_each(self):
        self.add("Acme")
        self.add("Beta")
        entered, shared, claimed_ids = [], None, []

        class Browser:
            def __init__(self):
                self.tabs = []

            async def claim_tab(self):
                self.tabs.append(len(self.tabs))
                return ip.Tab(self, self.tabs[-1])

            def tools(self, tab):
                return []

        @contextlib.asynccontextmanager
        async def one_browser(endpoint):
            nonlocal shared
            entered.append(endpoint)
            shared = Browser()
            yield shared

        async def no_chromium():
            return None

        def fake_agent(role, tools):
            return {tool.name: tool for tool in tools}

        async def fake_run(tools, text, tag="·", finish=True, limit=60):
            claimed = tools["claim_next_item"].invoke({})
            if not claimed.startswith("Id:"):
                return f"{tag}: nothing claimed"
            claimed_ids.append(int(claimed.splitlines()[0].split(":", 1)[1]))
            tools["begin_submission"].invoke({})
            tools["finish_item"].invoke({"outcome": "applied"})
            return f"{tag}: applied"

        with mock.patch.object(ip, "chromium", no_chromium), \
             mock.patch.object(ip, "browser", one_browser), \
             mock.patch.object(ip, "agent", fake_agent), \
             mock.patch.object(ip, "run", fake_run), \
             mock.patch.object(ip.status, "worker", lambda *args, **kwargs: None):
            reports = asyncio.run(ip.apply(workers=2))

        self.assertEqual(len(entered), 1)             # one MCP/browser session
        self.assertEqual(shared.tabs, [0, 1])         # two tabs in it
        self.assertEqual(len(claimed_ids), len(set(claimed_ids)))  # no job twice
        self.assertEqual(sorted(claimed_ids), [1, 2])
        self.assertEqual(ip.Item.select().where(ip.Item.state == ip.ACTIVE).count(), 2)
        self.assertIn("applied", " ".join(reports))


class McpTest(unittest.TestCase):
    """MCP tools as LangChain tools: the adaptation only, with a stand-in client."""

    def tool(self):
        return SimpleNamespace(
            name="browser_navigate", description="Go to a URL",
            inputSchema={"type": "object", "required": ["url"],
                         "properties": {"url": {"type": "string", "description": "where"}}})

    def test_an_mcp_tool_keeps_its_name_and_schema(self):
        made = ip.mcp_tool(self.tool(), mock.Mock())
        self.assertEqual(made.name, "browser_navigate")
        self.assertEqual(made.description, "Go to a URL")
        # The server's schema is passed through as-is: no pydantic model to keep in step.
        self.assertEqual(made.tool_call_schema["required"], ["url"])
        self.assertEqual(set(made.tool_call_schema["properties"]), {"url"})

    def test_a_call_reaches_the_client_and_returns_its_text(self):
        async def call_tool(name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(text=f"{name} {arguments['url']}")],
                                   isError=False)
        made = ip.mcp_tool(self.tool(), mock.Mock(call_tool=call_tool))
        self.assertEqual(asyncio.run(made.ainvoke({"url": "https://a"})),
                         "browser_navigate https://a")

    def test_a_failing_call_is_a_sentence_not_an_exception(self):
        async def call_tool(name, arguments):
            raise RuntimeError("Connection closed")
        made = ip.mcp_tool(self.tool(), mock.Mock(call_tool=call_tool))
        out = asyncio.run(made.ainvoke({"url": "https://a"}))
        self.assertIn("NOT done", out)
        self.assertIn("Connection closed", out)

    def test_a_failed_tab_switch_is_a_sentence_not_an_exception(self):
        async def dead_tab(name, arguments):
            raise RuntimeError("that tab is gone")
        made = ip.mcp_tool(self.tool(), mock.Mock(), invoke=dead_tab)
        out = asyncio.run(made.ainvoke({"url": "https://a"}))
        self.assertIn("NOT done", out)
        self.assertIn("tab is gone", out)

    def test_an_error_result_is_reported_as_such(self):
        async def call_tool(name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(text="no such element")],
                                   is_error=True)      # the SDK's own field name
        out = asyncio.run(ip.mcp_tool(self.tool(), mock.Mock(call_tool=call_tool))
                          .ainvoke({"url": "https://a"}))
        self.assertIn("NOT done", out)
        self.assertIn("no such element", out)

    def test_a_long_page_snapshot_is_truncated(self):
        async def call_tool(name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(text="x" * 50_000)], is_error=False)
        out = asyncio.run(ip.mcp_tool(self.tool(), mock.Mock(call_tool=call_tool))
                          .ainvoke({"url": "https://a"}))
        self.assertLess(len(out), 50_000)
        self.assertIn("truncated", out)


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


class QuestionsTest(unittest.TestCase):
    """application-questions.org is the answer store; a hold is how a lead waits for it."""

    FILE = """#+TITLE: Application Questions and Answers

* Recurring answers

** Salary expectation
   :PROPERTIES:
   :ANSWER: 100 000 EUR / year
   :NOTE: gross, per year
   :END:

** Do you have experience with Node.js?
:PROPERTIES:
:SOURCE: Delta
:END:
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ip.open_db(Path(self.tmp.name) / "t.db")
        ip.Item.delete().execute()
        self.path = Path(self.tmp.name) / "application-questions.org"
        self.path.write_text(self.FILE, encoding="utf-8")
        self.patch = mock.patch.object(ip, "QUESTIONS", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        ip.db.close()
        self.tmp.cleanup()

    def tool(self, w, name):
        return next(t for t in ip.tools_for(w) if t.name == name)

    def edit(self, question, answer=None, rename=None, decline=None):
        """Do what the user does: type into the entry's `:ANSWER:`, mark it `:DECLINE:`,
        and/or rename it."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        heading = next((i for i, line in enumerate(lines)
                        if line.startswith("*")
                        and ip.question_key(line.lstrip("* ")) == ip.question_key(question)),
                       None)
        if heading is None:
            raise AssertionError(f"no heading for {question!r}")
        if rename:
            lines[heading] = f"** {rename}"
        if answer:
            filled = next((i for i in range(heading + 1, len(lines))
                           if lines[i].lstrip().upper().startswith(":ANSWER:")), None)
            if filled is not None:
                indent = lines[filled][:len(lines[filled]) - len(lines[filled].lstrip())]
                lines[filled] = f"{indent}:ANSWER: {answer}"
            else:
                lines[heading + 1:heading + 1] = ["   :PROPERTIES:",
                                                 f"   :ANSWER: {answer}", "   :END:"]
        if decline:
            filled = next((i for i in range(heading + 1, len(lines))
                           if lines[i].lstrip().upper().startswith((":ANSWER:", ":END:"))),
                          None)
            if filled is None:
                raise AssertionError(f"no drawer under {question!r}")
            indent = lines[filled][:len(lines[filled]) - len(lines[filled].lstrip())]
            lines[filled:filled] = [f"{indent}:DECLINE: {decline}"]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def add(self, name):
        return ip.Item.create(name=name, name_key=name.lower(), title="T",
                              url=f"https://{name.lower()}.dev/1")

    def worker(self, name="Acme"):
        """A worker holding a freshly claimed lead."""
        w = ip.Worker()
        self.add(name)
        w.claim()
        return w

    def save(self, w, question):
        return self.tool(w, "save_question").invoke({"question": question})

    def hold(self, w, question=None):
        reason = {"reason": question} if question else {}
        return self.tool(w, "finish_item").invoke({"outcome": "hold", **reason})

    def state(self, name="Acme"):
        return ip.Item.get(ip.Item.name == name).state

    def test_a_stored_answer_is_found_by_keyword_not_wording(self):
        # The form says "What is your salary expectation?"; the file says "Salary expectation".
        out = ip.answer_text("salary expectation")
        self.assertIn("100 000 EUR / year", out)
        self.assertIn("gross, per year", out)         # the note comes along

    def test_an_unanswered_heading_is_not_an_answer(self):
        self.assertEqual([q.title for q in ip.answered("node.js")], [])
        self.assertIn("No stored answer", ip.answer_text("node.js"))
        self.assertIn("hold", ip.answer_text("node.js"))

    def test_a_hold_saves_the_question_and_stops_the_lead(self):
        before = self.path.read_text(encoding="utf-8")
        out = self.hold(self.worker(), "What is your notice period?")
        self.assertEqual(self.state(), ip.HOLD)
        self.assertEqual(ip.queued(), 0)             # out of the queue, not archived
        self.assertIsNone(ip.Item.get(ip.Item.name == "Acme").archived_at)
        self.assertIn("on hold", out)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("** What is your notice period?", saved)
        self.assertTrue(saved.startswith(before))    # appended to, never rewritten

    def test_saving_never_touches_the_entries_already_in_the_file(self):
        before = self.path.read_text(encoding="utf-8")
        w = self.worker()
        self.save(w, "What is your notice period?")
        self.save(w, "Are you willing to relocate?")
        after = self.path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before))
        self.assertEqual(after.count("** Salary expectation"), 1)
        self.assertEqual(len(ip.questions()), 4)      # the fixture's 2, and the 2 new ones
        self.assertNotIn("Recurring answers", [q.title for q in ip.questions()])

    def test_an_appended_question_has_the_files_own_shape(self):
        self.save(self.worker(), "What is your notice period?")
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n** What is your notice period?\n"
                                      "   :PROPERTIES:\n"
                                      "   :ANSWER:\n"
                                      "   :SOURCE: Acme (T)\n"
                                      "   :END:\n"), text[-200:])
        entry = ip.questions()[-1]
        self.assertEqual((entry.title, entry.answer, entry.source),
                         ("What is your notice period?", "", "Acme (T)"))

    def test_filling_in_the_appended_entry_is_all_the_user_does(self):
        self.hold(self.worker(), "What is your notice period?")
        self.assertEqual(ip.unhold(), 0)
        self.edit("What is your notice period?", answer="One month")
        self.assertEqual(ip.unhold(), 1)
        self.assertEqual(self.state(), ip.NEW)
        self.assertIn("One month", ip.answer_text("notice period"))

    def test_a_lead_can_wait_on_several_questions(self):
        w = self.worker()
        self.assertIn("Saved", self.save(w, "What is your notice period?"))
        self.assertIn("Saved", self.save(w, "Are you willing to relocate?"))
        self.assertIn("2 question(s)", self.hold(w))
        self.assertEqual(self.state(), ip.HOLD)
        self.assertEqual([entry.title for entry in ip.waiting(ip.Item.get().id)],
                         ["What is your notice period?", "Are you willing to relocate?"])

    def test_nothing_is_stored_on_the_lead_itself(self):
        self.hold(self.worker(), "What is your notice period?")
        self.assertNotIn("question", {field.name for field in ip.Item._meta.sorted_fields})

    def test_a_hold_is_refused_when_the_file_already_answers_the_question(self):
        before = self.path.read_text(encoding="utf-8")
        out = self.hold(self.worker(), "What is your salary expectation?")
        self.assertIn("Already answered", out)
        self.assertIn("100 000 EUR / year", out)
        self.assertEqual(self.state(), ip.WORKING)   # the lead is untouched
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_hold_needs_some_question_to_wait_for(self):
        out = self.hold(self.worker())
        self.assertIn("NOT held", out)
        self.assertEqual(self.state(), ip.WORKING)

    def test_an_unanswered_question_cannot_be_saved_without_a_name(self):
        self.assertIn("NOT saved", self.save(self.worker(), "  "))

    def test_two_leads_on_one_question_leave_one_heading(self):
        self.hold(self.worker("Acme"), "What is your notice period?")
        self.hold(self.worker("Beta"), "What is your notice period?")
        saved = self.path.read_text(encoding="utf-8")
        self.assertEqual(saved.count("** What is your notice period?"), 1)
        self.assertEqual(saved.count(":SOURCE: Acme (T)"), 1)   # appended once, not edited
        self.assertEqual(len(ip.waiting(ip.Item.get(ip.Item.name == "Beta").id)), 1)

    def test_answering_a_question_releases_only_the_leads_that_asked_it(self):
        self.hold(self.worker("Acme"), "What is your notice period?")
        self.hold(self.worker("Beta"), "Are you willing to relocate?")
        self.assertEqual(ip.unhold(), 0)             # both still open
        self.edit("What is your notice period?", answer="One month")
        self.assertEqual(ip.unhold(), 1)
        self.assertEqual((self.state("Acme"), self.state("Beta")), (ip.NEW, ip.HOLD))
        self.assertEqual(ip.queued(), 1)

    def test_renaming_a_heading_still_releases_the_lead(self):
        self.hold(self.worker(), "What is your notice period?")
        self.edit("What is your notice period?", answer="One month",
                  rename="Notice period at a new job")
        self.assertEqual(ip.unhold(), 1)

    def test_the_board_counts_held_leads(self):
        self.hold(self.worker(), "What is your notice period?")
        self.assertEqual(ip.tally()[ip.HOLD], 1)

    def test_a_missing_questions_file_is_created_by_the_first_hold(self):
        self.path.unlink()
        self.hold(self.worker(), "What is your notice period?")
        self.assertIn("#+TITLE", self.path.read_text(encoding="utf-8"))
        self.assertEqual(ip.questions()[0].title, "What is your notice period?")

    def test_a_question_added_by_hand_is_found_without_being_rewritten(self):
        appended = self.path.read_text(encoding="utf-8") + (
            "\n** Do you have experience with Go?\n:PROPERTIES:\n"
            ":ANSWER: Yes, two years\n:END:\n")
        self.path.write_text(appended, encoding="utf-8")
        self.assertIn("Yes, two years", ip.answer_text("Go experience"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), appended)

    def test_a_declined_field_is_reported_as_a_refusal_not_as_a_value(self):
        self.edit("Do you have experience with Node.js?", decline="t")
        out = ip.answer_text("node.js experience")
        self.assertIn("DECLINED", out)
        self.assertIn("leave the field blank", out)
        self.assertIn("never invent", out.lower())
        self.assertTrue(ip.questions()[-1].decline)
        self.assertEqual(ip.open_questions(), [])              # decided, not open

    def test_decline_off_leaves_the_question_open(self):
        self.edit("Do you have experience with Node.js?", decline="no")
        self.assertFalse(ip.questions()[-1].decline)
        self.assertEqual([q.title for q in ip.open_questions()],
                         ["Do you have experience with Node.js?"])

    def test_a_declined_question_releases_the_lead_that_waited_on_it(self):
        self.hold(self.worker(), "What is your notice period?")
        self.assertEqual(ip.unhold(), 0)                     # still open
        self.edit("What is your notice period?", decline="t")
        self.assertEqual(ip.unhold(), 1)
        self.assertEqual(self.state(), ip.NEW)

    def test_a_declined_field_is_not_asked_again_and_not_appended(self):
        self.edit("Do you have experience with Node.js?", decline="t")
        before = self.path.read_text(encoding="utf-8")
        out = self.save(self.worker(), "Do you have experience with Node JS?")
        self.assertIn("Declined", out)
        self.assertIn("do not ask again", out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_hold_is_refused_for_a_declined_field(self):
        self.edit("Do you have experience with Node.js?", decline="t")
        out = self.hold(self.worker(), "Do you have experience with Node.js?")
        self.assertIn("Declined", out)
        self.assertEqual(self.state(), ip.WORKING)            # untouched, not held
        self.assertEqual(ip.Hold.select().count(), 0)

    def test_a_decline_may_carry_the_fallback_to_type(self):
        self.edit("Do you have experience with Node.js?", decline="t", answer="N/A")
        out = ip.answer_text("node.js experience")
        self.assertIn("DECLINED", out)
        self.assertIn("N/A", out)                             # a fallback, not an answer
        self.assertIn("where the form insists", out)


class MatchingTest(unittest.TestCase):
    """Questions are found by meaning — the words of a question, not its wording.

    The file stands in for the real one: short headings, the form's paperwork kept out of
    them, and `:ALIASES:` carrying the synonyms a dozen forms use for the same field.
    """

    FILE = """#+TITLE: Application Questions and Answers

* Recurring answers

** Salary expectation
   :PROPERTIES:
   :ANSWER: 100 000 EUR / year
   :END:

** Address
   :PROPERTIES:
   :DECLINE: t
   :ALIASES: address line, postal address, city, state, province, zip, postal code
   :END:

** Address Line 1 (also City, State/Province and Zip/Postal Code are required)

** Notice period at a new job

** Years of Java experience
   :PROPERTIES:
   :ANSWER: Eight years
   :END:

** Relocation
   :PROPERTIES:
   :ANSWER: Remote only.
   :ALIASES: relocate, relocating, relocation, realocation, moving, spain, portugal, elsewhere
   :END:

** Do you have experience with Go?
   :PROPERTIES:
   :ANSWER: Yes, two years
   :END:
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ip.open_db(Path(self.tmp.name) / "t.db")
        ip.Item.delete().execute()
        self.path = Path(self.tmp.name) / "application-questions.org"
        self.path.write_text(self.FILE, encoding="utf-8")
        self.patch = mock.patch.object(ip, "QUESTIONS", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        ip.db.close()
        self.tmp.cleanup()

    def add(self, name="Acme"):
        return ip.Item.create(name=name, name_key=name.lower(), title="T",
                              url=f"https://{name.lower()}.dev/1")

    def entry(self, title):
        return next(q for q in ip.questions() if q.title == title)

    def save(self, question):
        w = ip.Worker()
        self.add()
        w.claim()
        tool = next(t for t in ip.tools_for(w) if t.name == "save_question")
        return tool.invoke({"question": question})

    def test_a_short_heading_answers_every_wording_of_the_field(self):
        asked = ("What is your full postal address - Address Line 1, City, "
                 "State/Province and Zip or Postal Code?")
        self.assertTrue(ip.same_question(asked, self.entry("Address")))
        out = ip.answer_text("postal address")
        self.assertIn("DECLINED", out)
        self.assertIn("Address", out)

    def test_a_heading_carrying_one_forms_paperwork_matches_only_that_form(self):
        # Why the heading has to be short: this one is nobody else's wording.
        long_heading = ("Address Line 1: / City: / State/Province: / Zip/Postal Code: "
                        "(all marked required on the Dynatrace SAP SuccessFactors form)")
        self.assertFalse(ip.same_question("What is your full postal address?", long_heading))
        self.assertTrue(ip.same_question("Address Line 1: / City: / Zip/Postal Code",
                                         long_heading))

    def test_aliases_cover_the_synonyms_a_form_may_use(self):
        # One general Relocation heading answers the Spain and Portugal forms alike.
        for asked in ("Are you open to relocating to Spain if you are currently based "
                      "elsewhere?",
                      "Available for Realocation to Portugal",
                      "Are you willing to relocate?",
                      "Would you consider relocation?"):
            self.assertTrue(ip.same_question(asked, self.entry("Relocation")), asked)
        self.assertIn("Remote only.", ip.answer_text("relocating to spain"))

    def test_aliases_do_not_merge_unrelated_questions(self):
        relocation = self.entry("Relocation")
        self.assertFalse(ip.same_question("Which office location would you choose?",
                                          relocation))
        self.assertFalse(ip.same_question("Years of Java experience", relocation))

    def test_a_declined_alias_answers_a_reworded_field_without_appending(self):
        before = self.path.read_text(encoding="utf-8")
        out = self.save("What is your postal address?")
        self.assertIn("Declined", out)
        self.assertIn("do not ask again", out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_the_paperwork_is_not_part_of_the_question(self):
        # Parentheticals, links and bare numbers are where the form came from, not what
        # it asked, so the same address field cannot grow a heading per application.
        self.assertEqual(ip.tokens_of("Address Line 1 (all marked required on the Acme form)"),
                         ["address", "line"])
        self.assertEqual(ip.tokens_of("See https://acme.dev/jobs/42 for the postcode"),
                         ["see", "postcode"])

    def test_two_different_skills_stay_two_questions(self):
        self.assertFalse(ip.same_question("Do you have experience with Go?",
                                          "Do you have experience with Node.js?"))
        self.assertFalse(ip.same_question("Years of React experience",
                                          "Years of Java experience"))

    def test_a_reworded_question_is_never_appended_twice(self):
        before = self.path.read_text(encoding="utf-8")
        out = self.save("What is your notice period?")
        self.assertIn("Already asked", out)
        self.assertIn("nothing is asked twice", out)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(ip.Item.select().where(ip.Item.state == ip.HOLD).count(), 0)

    def test_a_new_question_is_stored_without_its_paperwork(self):
        self.save("What is your current job title? (as it appears on LinkedIn)")
        self.assertIn("** What is your current job title?\n",
                      self.path.read_text(encoding="utf-8"))

    def test_the_two_keywords_are_content_words(self):
        words = ip.keywords("What is your full postal address - Address Line 1, City, "
                            "State/Province and Zip or Postal Code?")
        self.assertEqual(len(words), 2)
        self.assertTrue(all(word not in ip.STOPWORDS for word in words))

    def test_at_most_three_headings_come_back(self):
        self.assertLessEqual(len(ip.answered("address")), 3)

    def test_repeats_finds_the_same_question_in_two_wordings(self):
        groups = ip.repeats()
        self.assertTrue(groups)
        titles = {entry.title for group in groups for entry in group}
        self.assertIn("Address Line 1 (also City, State/Province and Zip/Postal Code are "
                      "required)", titles)

    def test_the_report_points_at_the_line_to_edit(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ip.print_questions()
        entry = self.entry("Notice period at a new job")
        self.assertIn(f"{self.path}:{entry.line}:", out.getvalue())
        self.assertIn("2 open", out.getvalue())
        self.assertIn("1 declined", out.getvalue())

    def test_headings_know_their_line(self):
        lines = [entry.line for entry in ip.questions()]
        self.assertTrue(all(line > 1 for line in lines))
        self.assertEqual(lines, sorted(lines))
        entry = self.entry("Salary expectation")
        self.assertEqual(self.FILE.splitlines()[entry.line - 1], "** Salary expectation")

    def test_a_hold_key_does_not_change_when_the_matching_vocabulary_widens(self):
        # `question_key` is a hold's link to a heading: widening STOPWORDS must not move it.
        self.assertEqual(ip.question_key("Are you open to relocating to Spain?"),
                         "open relocating spain")


if __name__ == "__main__":
    unittest.main()
