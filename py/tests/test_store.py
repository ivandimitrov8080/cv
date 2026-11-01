"""Store invariants: no double claim, fencing, leases, dedupe, archive, pause.

They use a real file-backed database, because several connections racing for the
same rows is the thing under test.
"""

import threading

from itempipe.store import FENCED, ItemStore, name_key, url_key


# --- claiming (invariant 1 and 2) -----------------------------------------
def test_no_double_claim(cfg, conn, make_items, open_db):
    ids = make_items(5)
    results, lock, barrier = [], threading.Lock(), threading.Barrier(20)

    def run() -> None:
        local = open_db()
        try:
            store = ItemStore(local, cfg)
            barrier.wait()
            row = store.claim_next(f"w-{threading.get_ident()}")
            with lock:
                results.append(row["id"] if row else None)
        finally:
            local.close()

    threads = [threading.Thread(target=run) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 5 and len(set(claimed)) == 5
    assert results.count(None) == 15 and set(claimed) == set(ids)
    assert conn.execute("SELECT COUNT(*) FROM attempts WHERE status='claimed'").fetchone()[0] == 5


def test_fencing_rejects_a_stale_token(cfg, items, conn, make_items):
    make_items(1)
    first = items.claim_next("worker-a")
    stale = first["claim_token"]

    conn.execute("UPDATE items SET claimed_until = datetime('now', '-1 hour') WHERE id = ?",
                 (first["id"],))
    items.reap()
    second = items.claim_next("worker-b")
    assert second["id"] == first["id"] and second["claim_token"] != stale

    assert items.advance(first["id"], stale, worker_id="worker-a") == (False, FENCED)
    assert items.release(first["id"], stale, "stale") == (False, FENCED)
    row = items.get(first["id"])
    assert row["state"] == cfg.working_state and row["claimed_by"] == "worker-b"


def test_expired_lease_requeues(cfg, items, conn, make_items):
    make_items(1)
    claimed = items.claim_next("worker-a")
    conn.execute("UPDATE items SET claimed_until = datetime('now', '-1 hour') WHERE id = ?",
                 (claimed["id"],))
    assert items.reap()["requeued"] == 1
    row = items.get(claimed["id"])
    assert row["state"] == cfg.initial_state and row["claim_token"] is None
    failed = conn.execute("SELECT status FROM attempts WHERE item_id = ?", (claimed["id"],)).fetchall()
    assert "failed" in [r["status"] for r in failed]


def test_submission_in_flight_goes_to_review_never_back_to_queue(cfg, items, conn, make_items):
    """Invariant 9: an irreversible step that may have run is never retried."""
    make_items(1)
    claimed = items.claim_next("worker-a")
    items.begin_submission(claimed["id"], claimed["claim_token"], "worker-a")
    items.mark_sent(claimed["id"], "worker-a")
    conn.execute("UPDATE items SET claimed_until = datetime('now', '-1 hour') WHERE id = ?",
                 (claimed["id"],))

    assert items.reap() == {"requeued": 0, "review": 1, "unpaused": 0}
    assert items.get(claimed["id"])["state"] == cfg.review_state
    assert items.claim_next("worker-b") is None


def test_advance_requires_the_submission_guard(cfg, items, make_items):
    make_items(1)
    claimed = items.claim_next("worker-a")
    ok, message = items.advance(claimed["id"], claimed["claim_token"], worker_id="worker-a")
    assert not ok and message.startswith("NOT done (submitting)")

    items.begin_submission(claimed["id"], claimed["claim_token"], "worker-a")
    ok, message = items.advance(claimed["id"], claimed["claim_token"], worker_id="worker-a")
    assert ok and "as ACTIVE" in message
    row = items.get(claimed["id"])
    assert row["state"] == cfg.success_state and row["follow_up_on"]


def test_pause_answer_requeue_cycle(cfg, items, answers, make_items):
    """Invariants 7 and 8: unanswered stays unanswerable; pause is a state."""
    make_items(1)
    claimed = items.claim_next("worker-a")
    question = "What is your salary expectation?"
    assert items.pause(claimed["id"], claimed["claim_token"], question)[0]
    assert items.get(claimed["id"])["state"] == cfg.paused_state

    assert answers.search("salary") == "No stored answer matches 'salary'."
    assert items.reap()["unpaused"] == 0
    assert items.get(claimed["id"])["state"] == cfg.paused_state

    answers.answer(question, "100k EUR")
    assert items.reap()["unpaused"] == 1
    assert items.get(claimed["id"])["state"] == cfg.initial_state
    assert items.claim_next("worker-b") is not None


# --- item lifecycle (invariants 4-6) --------------------------------------
def test_required_fields_and_url(items):
    assert items.create(name="  ", title="T", url="https://e.com/1")["status"] == "rejected"
    assert "no title" in items.create(name="Acme", title="", url="https://e.com/1")["message"]
    assert "http(s) URL" in items.create(name="Acme", title="T", url="e.com/1")["message"]


def test_name_and_title_collapse_to_one_line(items):
    row = items.create(name="  Acme \n Corp ", title="Backend\nEngineer",
                       url="https://a.example/1")["item"]
    assert (row["name"], row["title"]) == ("Acme Corp", "Backend Engineer")
    assert row["name_key"] == name_key("acme corp")


def test_size_and_language_vocabularies_drop_with_a_warning(items):
    result = items.create(name="Acme", title="E", url="https://e.com/1",
                          size="gigantic", language="maybe")
    assert result["item"]["size"] is None and "ignored unknown size" in result["message"]
    assert items.create(name="Beta", title="E", url="https://e.com/2",
                        size="MID", language="YES")["item"]["size"] == "mid"


def test_duplicate_is_a_constraint(items, conn):
    first = items.create(name="Acme", title="Engineer", url="https://e.com/1")
    second = items.create(name="ACME ", title="Other", url="https://e.com/2")
    assert first["status"] == "created" and second["status"] == "duplicate"
    assert second["message"].startswith("Already tracked, so not added again")
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_concurrent_create_dedupe(cfg, conn, open_db):
    """Invariant 3: two workers racing on one name_key leave exactly one row."""
    results, lock, barrier = [], threading.Lock(), threading.Barrier(4)

    def run(index: int) -> None:
        local = open_db()
        try:
            barrier.wait()
            status = ItemStore(local, cfg).create(
                name="Acme", title=f"Engineer {index}", url=f"https://e.com/{index}")["status"]
            with lock:
                results.append(status)
        finally:
            local.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("created") == 1 and results.count("duplicate") == 3
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_archived_rows_still_block_duplicates_but_leave_the_queue(items):
    item_id = items.create(name="Acme", title="Engineer", url="https://e.com/1")["item"]["id"]
    assert items.archive(item_id, reason="no fit")[0]
    assert items.list_items() == [] and len(items.list_items(include_archived=True)) == 1
    row = items.get(item_id)
    assert row["archived_at"] and row["archive_reason"] == "no fit"
    assert items.create(name="Acme", title="Engineer", url="https://e.com/1")["status"] == "duplicate"


def test_url_hint_is_reported_but_not_blocking(items):
    items.create(name="Acme", title="E", url="https://e.com/jobs/1")
    result = items.create(name="Beta", title="E", url="https://e.com/jobs/1/")
    assert result["status"] == "created" and "already used by Acme" in result["message"]
    assert url_key("HTTPS://Example.COM/jobs/1/#top") == "https://example.com/jobs/1"


def test_state_values_are_validated(items):
    result = items.create(name="A", title="T", url="https://e.com/1", state="NOT_A_STATE")
    assert result["status"] == "rejected" and "unknown state" in result["message"]


def test_dry_run_create_writes_nothing(items, conn):
    result = items.create(name="Acme", title="E", url="https://e.com/1", dry_run=True)
    assert result["status"] == "created"
    assert result["message"].startswith("DRY RUN: would record")
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_status_counts(items, cfg):
    item_id = items.create(name="S1", title="T", url="https://e.com/1")["item"]["id"]
    items.create(name="S2", title="T", url="https://e.com/2")
    items.archive(item_id, reason="done")
    status = items.status()
    assert status["queued"] == 1 and status["archived"] == 1
    assert status["counts"][cfg.initial_state] == 1 and status["in_flight"] == []


def test_target_claim_is_exclusive(targets):
    assert targets.claim("Acme", "worker-a")[0]
    ok, message = targets.claim("Acme", "worker-b")
    assert not ok and "right now" in message
    assert targets.claim("ACME  ", "worker-a")[0], "the same worker may re-claim"
    targets.mark_unusable("Acme", "posting closed")
    ok, message = targets.claim("Acme", "worker-c")
    assert not ok and "unusable" in message
