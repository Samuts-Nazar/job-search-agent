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
    text = format_extended_stats(conn, fit_score_min=40)
    assert "no runs recorded yet" in text
    assert "no scored postings yet" in text
    assert "no submitted applications yet" in text
    assert "no submitted postings yet" in text
    assert "no salary data with a known currency+period yet" in text
    assert "no replies recorded yet" in text
    assert "no tech_tags data yet" in text
    assert "scored: 0" in text
    assert "package_ready: 0" in text


def test_format_extended_stats_application_funnel(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn, id1, "scored", recommended_cv_track="qa_automation", fit_score=80
    )
    db.update_posting_status(
        conn, id2, "replied", recommended_cv_track="qa_automation", fit_score=70
    )

    text = format_extended_stats(conn, fit_score_min=40)

    assert "scored: 2" in text
    assert "package_ready: 1" in text
    assert "submitted: 1" in text
    assert "replied: 1" in text
    assert "replied: 1" in text  # outcome breakdown line
    assert "qa_automation: 100% (1/1)" in text


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

    text = format_extended_stats(conn, fit_score_min=40)
    assert "Docker: 1" in text
    assert "qa_automation: 100% (1/1)" in text


def test_format_extended_stats_tech_tag_sections(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn,
        id1,
        "scored",
        tech_tags=(
            '[{"tag": "Python", "priority": "required"}, '
            '{"tag": "AWS", "priority": "nice_to_have"}]'
        ),
        salary_min=2000.0,
        salary_max=2000.0,
        required_years_experience=0.5,
        portfolio_expected="github",
    )
    db.update_posting_status(
        conn,
        id2,
        "scored",
        tech_tags='[{"tag": "Python", "priority": "required"}]',
        salary_min=4000.0,
        salary_max=4000.0,
        required_years_experience=5,
        portfolio_expected="none",
    )

    text = format_extended_stats(conn, fit_score_min=40)
    assert "AWS + Python" in text  # pairs are alphabetically sorted before joining
    assert "Python: frequency=2" in text
    assert "median_salary=3000" in text
    assert "github: 50% (1/2)" in text
    assert "none: 50% (1/2)" in text


def test_format_extended_stats_ats_vendor_breakdown(conn):
    checked_id = db.insert_posting(conn, make_posting())
    unchecked_id = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn,
        checked_id,
        "scored",
        fit_score=80,
        ats_vendor="greenhouse",
        ats_checked_at="2026-09-23T00:00:00+00:00",
    )
    db.update_posting_status(conn, unchecked_id, "scored", fit_score=50)

    text = format_extended_stats(conn, fit_score_min=40)

    assert "ATS vendor breakdown, postings at/above fit_score 40:" in text
    assert "greenhouse: 1" in text
    assert "not_checked: 1" in text
