"""Org import: heading parsing, state mapping, archive handling, idempotency."""

from itempipe.org_import import import_org, parse, parse_questions
from itempipe.store import ItemStore

TRACKER = """\
#+TITLE: Job Search Tracker
#+TODO: LEAD(l) APPLIED(a) INTERVIEW(i) OFFER(o) HOLD(w) | HIRED(h) REJECTED(r) IGNORED(x)

* Pipeline

** APPLIED Acme — Backend Engineer :large:english:
   :PROPERTIES:
   :URL: https://acme.example/jobs/1
   :LOCATION: Remote (EU)
   :NATION: Bulgaria
   :SIZE: mid
   :ENGLISH: yes
   :CREATED: 2026-01-01
   :APPLIED_ON: 2026-01-03
   :INTERVIEW: 2026-02-01
   :END:

   - Note [2026-01-03 Sat 10:00] Application sent.

** HOLD Beta — Platform Engineer

   - NOTE: waiting on the salary answer

** LEAD Delta — Solutions Engineer

* Archive

** REJECTED Gamma — Old Role
   :PROPERTIES:
   :ARCHIVE_TIME: 2026-02-01 Sat 12:00
   :ARCHIVE_OLPATH: Pipeline/Gamma
   :END:

   - Note [2026-02-01 Sat 12:00] Closed.
"""

QUESTIONS = """\
#+TITLE: Application Questions and Answers
* Recurring answers

** What is your notice period?
   :PROPERTIES:
   :ANSWER: One month
   :END:

** Do you need a visa?

** Salary expectation
   :PROPERTIES:
   :ANSWER: 12000 BGN gross monthly
   :NOTE: monthly, gross, BGN
   :END:
"""


def test_parse_headings_properties_and_notes():
    entries = parse(TRACKER)
    assert [e.name for e in entries] == ["Acme", "Beta", "Delta", "Gamma"]
    acme = entries[0]
    assert (acme.todo, acme.title) == ("APPLIED", "Backend Engineer")
    assert acme.props["URL"] == "https://acme.example/jobs/1"
    assert acme.notes == ["Application sent."]
    assert entries[-1].archived is True and entries[-1].notes == ["Closed."]


def test_import_counts_mapping_and_archive(cfg, conn, tmp_path):
    jobs = tmp_path / "jobs.org"
    jobs.write_text(TRACKER, encoding="utf-8")
    questions = tmp_path / "application-questions.org"
    questions.write_text(QUESTIONS, encoding="utf-8")

    summary = import_org(conn, cfg, jobs_path=jobs, questions_path=questions)
    assert summary["created"] == 4, summary
    assert summary["states"] == {"ACTIVE": 1, "PAUSED": 1, "NEW": 1, "REJECTED": 1}
    assert (summary["notes"], summary["archived"]) == (3, 1)
    assert (summary["questions"], summary["answers"]) == (3, 2)

    items = ItemStore(conn, cfg)
    acme = items.get_by_name("Acme")
    assert (acme["state"], acme["region"], acme["size"]) == ("ACTIVE", "Bulgaria", "mid")
    assert acme["advanced_on"] == "2026-01-03"
    assert "INTERVIEW" in (acme["meta_json"] or "")
    assert items.get_by_name("Gamma")["archived_at"] == "2026-02-01 Sat 12:00"


def test_import_is_idempotent(cfg, conn, tmp_path):
    jobs = tmp_path / "jobs.org"
    jobs.write_text(TRACKER, encoding="utf-8")
    import_org(conn, cfg, jobs_path=jobs, questions_path=None)
    again = import_org(conn, cfg, jobs_path=jobs, questions_path=None)
    assert again["created"] == 0 and again["duplicate"] == 4


def test_import_dry_run_writes_nothing(cfg, conn, tmp_path):
    jobs = tmp_path / "jobs.org"
    jobs.write_text(TRACKER, encoding="utf-8")
    assert import_org(conn, cfg, jobs_path=jobs, questions_path=None, dry_run=True)["created"] == 4
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_parse_questions_skips_section_headings():
    rows = dict(parse_questions(QUESTIONS))
    assert "Recurring answers" not in rows
    assert rows["Salary expectation"]["ANSWER"] == "12000 BGN gross monthly"
    assert rows["Salary expectation"]["NOTE"] == "monthly, gross, BGN"
