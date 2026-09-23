from datetime import UTC, datetime

import pytest

from job_agent import budget, db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def _insert_llm_call(conn, *, cost_usd: float, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls (posting_id, tier, model, purpose, cost_usd, created_at)
        VALUES (NULL, 'bulk', 'test/model', 'score', ?, ?)
        """,
        (cost_usd, created_at),
    )
    conn.commit()


def test_spend_below_warn_fraction_returns_none(conn, tmp_path):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=1.0, created_at=now.isoformat())

    result = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=tmp_path / "flag", now=now
    )

    assert result is None


def test_spend_at_warn_fraction_alerts_once(conn, tmp_path):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=8.5, created_at=now.isoformat())
    flag_path = tmp_path / "flag"

    first = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=flag_path, now=now
    )
    second = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=flag_path, now=now
    )

    assert first is not None
    assert "$8.50" in first
    assert "85%" in first
    assert second is None  # deduplicated within the same month
    assert flag_path.exists()


def test_spend_over_cap_reports_exceeded(conn, tmp_path):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=12.0, created_at=now.isoformat())

    result = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=tmp_path / "flag", now=now
    )

    assert result is not None
    assert "reached/exceeded" in result
    assert "$12.00" in result


def test_only_current_calendar_month_spend_counts(conn, tmp_path):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=9.0, created_at="2026-08-20T00:00:00+00:00")
    _insert_llm_call(conn, cost_usd=1.0, created_at=now.isoformat())

    result = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=tmp_path / "flag", now=now
    )

    assert result is None  # only this month's $1 counts, well under the 80% warn line


def test_new_month_gets_a_fresh_alert(conn, tmp_path):
    flag_path = tmp_path / "flag"
    august = datetime(2026, 8, 15, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=9.0, created_at=august.isoformat())
    first = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=flag_path, now=august
    )
    assert first is not None

    september = datetime(2026, 9, 1, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=8.5, created_at=september.isoformat())
    second = budget.check_budget_threshold(
        conn, monthly_usd_cap=10.0, alert_flag_path=flag_path, now=september
    )

    assert second is not None  # new calendar month -> re-eligible even though flag exists


def test_custom_warn_fraction(conn, tmp_path):
    now = datetime(2026, 9, 15, tzinfo=UTC)
    _insert_llm_call(conn, cost_usd=5.0, created_at=now.isoformat())

    result = budget.check_budget_threshold(
        conn,
        monthly_usd_cap=10.0,
        alert_flag_path=tmp_path / "flag",
        warn_fraction=0.4,
        now=now,
    )

    assert result is not None
    assert "50%" in result
