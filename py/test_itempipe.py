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
    """One browser, one session (and so one tab) per worker.

    Unit level only: these read the data handed to the MCP client and the error paths.
    Nothing starts chromium, spawns playwright-mcp or speaks MCP — `itempipe apply` is
    how those get exercised, by hand.
    """

    def test_workers_attach_to_one_shared_browser(self):
        # `mcp_config` is data only: the fields `mcp.StdioServerParameters` wants, so a
        # worker's playwright-mcp attaches to the shared CDP browser and owns no profile
        # of its own. Nothing here starts a browser or speaks MCP.
        cfg = ip.mcp_config(ip.cdp_endpoint())
        self.assertEqual(cfg["command"], "playwright-mcp")
        self.assertIn("--cdp-endpoint", cfg["args"])
        self.assertIn(ip.cdp_endpoint(), cfg["args"])
        self.assertNotIn("--user-data-dir", cfg["args"])   # the browser owns the profile
        self.assertEqual(ip.cdp_endpoint(9333), "http://127.0.0.1:9333")

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

    def edit(self, question, answer=None, rename=None):
        """Do what the user does: type into the entry's `:ANSWER:`, and/or rename it."""
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


if __name__ == "__main__":
    unittest.main()
