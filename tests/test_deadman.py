from datetime import UTC, datetime, timedelta

import pytest

from job_agent import db, deadman


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def _insert_run(conn, finished_at: str) -> None:
    db.insert_pipeline_run(
        conn,
        kind="run",
        started_at=finished_at,
        finished_at=finished_at,
        collected_count=0,
        new_count=0,
        filtered_out_count=0,
        scored_count=0,
        score_failed_count=0,
        source_counts={},
        cost_usd=0.0,
    )


def test_no_runs_recorded_returns_none(conn, tmp_path):
    result = deadman.check_pipeline_staleness(conn, alert_flag_path=tmp_path / "flag")
    assert result is None


def test_fresh_run_returns_none(conn, tmp_path):
    now = datetime.now(UTC)
    _insert_run(conn, (now - timedelta(hours=1)).isoformat())

    result = deadman.check_pipeline_staleness(conn, alert_flag_path=tmp_path / "flag", now=now)

    assert result is None


def test_stale_run_returns_alert_once(conn, tmp_path):
    now = datetime.now(UTC)
    _insert_run(conn, (now - timedelta(hours=40)).isoformat())
    flag_path = tmp_path / "flag"

    first = deadman.check_pipeline_staleness(conn, alert_flag_path=flag_path, now=now)
    second = deadman.check_pipeline_staleness(conn, alert_flag_path=flag_path, now=now)

    assert first is not None
    assert "36h" in first
    assert second is None  # deduplicated -- same episode
    assert flag_path.exists()


def test_flag_clears_once_a_fresh_run_is_recorded(conn, tmp_path):
    now = datetime.now(UTC)
    flag_path = tmp_path / "flag"
    _insert_run(conn, (now - timedelta(hours=40)).isoformat())
    deadman.check_pipeline_staleness(conn, alert_flag_path=flag_path, now=now)
    assert flag_path.exists()

    _insert_run(conn, now.isoformat())
    result = deadman.check_pipeline_staleness(conn, alert_flag_path=flag_path, now=now)

    assert result is None
    assert not flag_path.exists()

    # a later episode alerts again
    _insert_run(conn, (now - timedelta(hours=40)).isoformat())
    result = deadman.check_pipeline_staleness(conn, alert_flag_path=flag_path, now=now)
    assert result is not None


def test_custom_threshold(conn, tmp_path):
    now = datetime.now(UTC)
    _insert_run(conn, (now - timedelta(hours=2)).isoformat())

    result = deadman.check_pipeline_staleness(
        conn, alert_flag_path=tmp_path / "flag", threshold_hours=1.0, now=now
    )

    assert result is not None
    assert "1h" in result
