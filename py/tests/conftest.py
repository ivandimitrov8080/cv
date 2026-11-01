import pytest

from itempipe import config as config_module
from itempipe import db as db_module
from itempipe.store import AnswerStore, HostLeases, ItemStore, TargetStore, WorkerRegistry


@pytest.fixture
def cfg(tmp_path):
    """A config pointed entirely at tmp_path: no test touches the real files."""
    template = tmp_path / "application-email-template.txt"
    template.write_text("Subject: {{position}} at {{company}}\n\nHello {{company}},\n"
                        "I apply for {{position}}.\n", encoding="utf-8")
    return config_module.load(
        db=str(tmp_path / "itempipe.db"), attachments=(), email_template=str(template),
        profile_dir=str(tmp_path / "profiles"), cv_path=str(tmp_path / "cv.typ"),
        tracker_org=str(tmp_path / "jobs.org"),
        answers_org=str(tmp_path / "application-questions.org"))


@pytest.fixture
def conn(cfg):
    connection = db_module.open_db(cfg.db)
    yield connection
    connection.close()


@pytest.fixture
def items(conn, cfg):
    return ItemStore(conn, cfg)


@pytest.fixture
def answers(conn, cfg):
    return AnswerStore(conn, cfg)


@pytest.fixture
def targets(conn, cfg):
    return TargetStore(conn, cfg)


@pytest.fixture
def hosts(conn):
    return HostLeases(conn)


@pytest.fixture
def workers(conn, cfg):
    return WorkerRegistry(conn, cfg)


@pytest.fixture
def open_db(cfg):
    """A second, independent connection: the point of the race tests."""
    return lambda: db_module.open_db(cfg.db)


@pytest.fixture
def make_items(items):
    def _make(count: int) -> list[int]:
        ids = []
        for index in range(count):
            result = items.create(name=f"Subject {index}", title=f"Title {index}",
                                  url=f"https://example.com/jobs/{index}")
            assert result["status"] == "created", result["message"]
            ids.append(result["item"]["id"])
        return ids

    return _make
