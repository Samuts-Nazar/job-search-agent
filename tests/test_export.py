import csv

import pytest

from job_agent import db, export


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


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_export_postings(conn, tmp_path):
    db.insert_posting(conn, make_posting())
    out_path = tmp_path / "postings.csv"

    count = export.export_postings(conn, out_path)

    assert count == 1
    rows = read_csv(out_path)
    assert len(rows) == 1
    assert rows[0]["title"] == "QA Engineer"
    assert rows[0]["source"] == "djinni"


def test_export_scores(conn, tmp_path):
    posting_id = db.insert_posting(conn, make_posting())
    db.update_posting_status(conn, posting_id, "scored", fit_score=80, verdict="apply")
    out_path = tmp_path / "scores.csv"

    count = export.export_scores(conn, out_path)

    assert count == 1
    rows = read_csv(out_path)
    assert rows[0]["fit_score"] == "80"
    assert rows[0]["verdict"] == "apply"


def test_export_applications(conn, tmp_path):
    posting_id = db.insert_posting(conn, make_posting())
    db.insert_application(conn, db.Application(posting_id=posting_id, cv_track="qa_automation"))
    out_path = tmp_path / "applications.csv"

    count = export.export_applications(conn, out_path)

    assert count == 1
    rows = read_csv(out_path)
    assert rows[0]["cv_track"] == "qa_automation"


def test_export_unsupported_format_raises(conn, tmp_path):
    with pytest.raises(ValueError):
        export.export_postings(conn, tmp_path / "postings.json", fmt="json")


def test_export_all_writes_three_files(conn, tmp_path):
    posting_id = db.insert_posting(conn, make_posting())
    db.insert_application(conn, db.Application(posting_id=posting_id))

    counts = export.export_all(conn, tmp_path)

    assert counts == {"postings": 1, "scores": 1, "applications": 1}
    assert (tmp_path / "postings.csv").exists()
    assert (tmp_path / "scores.csv").exists()
    assert (tmp_path / "applications.csv").exists()
