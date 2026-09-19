import pytest

from job_agent import db
from job_agent.stats_report import format_extended_stats


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def make_posting(**overrides) -> db.Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/1/",
        canonical_url="https://djinni.co/jobs/1/",
        dedup_hash="hash1",
        title="QA Engineer",
        description="desc",
    )
    defaults.update(overrides)
    return db.Posting(**defaults)


def test_format_extended_stats_on_empty_db(conn):
    text = format_extended_stats(conn)
    assert "no scored postings yet" in text
    assert "no submitted applications yet" in text
    assert "no salary data yet" in text
    assert "no replies recorded yet" in text


def test_format_extended_stats_shows_counts_alongside_percentages(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.update_posting_status(
        conn, posting_id, "scored", missing_skills='["Docker"]', tech_stack="[]"
    )
    application_id = db.insert_application(
        conn,
        db.Application(posting_id=posting_id, cv_track="qa_automation", submitted_at="2026-09-01"),
    )
    db.update_application(conn, application_id, first_reply_at="2026-09-03")

    text = format_extended_stats(conn)
    assert "Docker: 1" in text
    assert "qa_automation: 100% (1/1)" in text
