import pytest

from job_agent import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def make_posting(**overrides) -> db.Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/123-qa/",
        canonical_url="https://djinni.co/jobs/123-qa/",
        dedup_hash="hash123",
        title="QA Automation Engineer",
        description="We are looking for a QA engineer.",
    )
    defaults.update(overrides)
    return db.Posting(**defaults)


def test_insert_and_get_posting(conn):
    posting_id = db.insert_posting(conn, make_posting())
    row = db.get_posting(conn, posting_id)
    assert row["title"] == "QA Automation Engineer"
    assert row["status"] == "new"
    assert row["source"] == "djinni"


def test_posting_exists_by_canonical_url(conn):
    db.insert_posting(conn, make_posting())
    assert db.posting_exists(
        conn, canonical_url="https://djinni.co/jobs/123-qa/", dedup_hash="different-hash"
    )
    assert not db.posting_exists(
        conn, canonical_url="https://djinni.co/jobs/other/", dedup_hash="different-hash"
    )


def test_posting_exists_by_dedup_hash(conn):
    db.insert_posting(conn, make_posting())
    assert db.posting_exists(
        conn, canonical_url="https://otherhost.example/jobs/456/", dedup_hash="hash123"
    )


def test_canonical_url_is_unique(conn):
    db.insert_posting(conn, make_posting())
    with pytest.raises(db.sqlite3.IntegrityError):
        db.insert_posting(conn, make_posting(dedup_hash="another-hash"))


def test_update_posting_status(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.update_posting_status(conn, posting_id, "scored", fit_score=80, verdict="apply")
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "scored"
    assert row["fit_score"] == 80
    assert row["verdict"] == "apply"


def test_get_postings_by_status(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn,
        make_posting(canonical_url="https://djinni.co/jobs/999/", dedup_hash="hash999"),
    )
    db.update_posting_status(conn, id1, "scored")
    rows = db.get_postings_by_status(conn, "scored")
    assert len(rows) == 1
    assert rows[0]["id"] == id1
    rows_new = db.get_postings_by_status(conn, "new")
    assert len(rows_new) == 1
    assert rows_new[0]["id"] == id2


def test_record_llm_call_and_total_cost(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.record_llm_call(
        conn,
        posting_id=posting_id,
        tier="bulk",
        model="test/model",
        purpose="score",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
    )
    db.record_llm_call(
        conn,
        posting_id=posting_id,
        tier="bulk",
        model="test/model",
        purpose="score",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.002,
    )
    assert db.total_cost_usd(conn) == pytest.approx(0.003)


def test_status_counts_and_source_counts(conn):
    id1 = db.insert_posting(conn, make_posting())
    db.insert_posting(
        conn,
        make_posting(
            canonical_url="https://djinni.co/jobs/999/", dedup_hash="hash999", source="dou"
        ),
    )
    db.update_posting_status(conn, id1, "scored")
    assert db.status_counts(conn) == {"scored": 1, "new": 1}
    assert db.source_counts(conn) == {"djinni": 1, "dou": 1}
