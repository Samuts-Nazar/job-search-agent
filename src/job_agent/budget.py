"""Monthly LLM spend guard: warns in Telegram once the configured
budget.monthly_usd cap is approached. Same dedup idiom as deadman.py's
stale-pipeline check -- a flag file so the warning fires once, not on
every run for the rest of the month -- but keyed by calendar month
rather than by a recovering/re-occurring condition, since spend only
ever increases within a month (llm_calls rows are never deleted).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from job_agent import db

DEFAULT_WARN_FRACTION = 0.8


def _month_start_iso(now: datetime) -> str:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def check_budget_threshold(
    conn: sqlite3.Connection,
    *,
    monthly_usd_cap: float,
    alert_flag_path: Path | str,
    warn_fraction: float = DEFAULT_WARN_FRACTION,
    now: datetime | None = None,
) -> str | None:
    """Returns an alert message the first time this calendar month's LLM
    spend (db.total_cost_usd since the 1st) reaches warn_fraction of
    monthly_usd_cap; None otherwise, including every later call this same
    month once already alerted -- the flag file (holding the "YYYY-MM" it
    last fired for) is what dedups. A new calendar month always gets a
    fresh chance to alert, since spend resets to ~0 and the stored month
    no longer matches."""
    alert_flag_path = Path(alert_flag_path)
    now = now or datetime.now(UTC)
    month_key = now.strftime("%Y-%m")

    spend = db.total_cost_usd(conn, since_iso=_month_start_iso(now))
    warn_at = monthly_usd_cap * warn_fraction
    if spend < warn_at:
        return None

    already_alerted = (
        alert_flag_path.exists()
        and alert_flag_path.read_text(encoding="utf-8").strip() == month_key
    )
    if already_alerted:
        return None

    alert_flag_path.parent.mkdir(parents=True, exist_ok=True)
    alert_flag_path.write_text(month_key, encoding="utf-8")

    if spend >= monthly_usd_cap:
        return (
            f"Monthly LLM spend ${spend:.2f} has reached/exceeded the "
            f"${monthly_usd_cap:.2f} budget cap."
        )
    return (
        f"Monthly LLM spend ${spend:.2f} has reached {spend / monthly_usd_cap:.0%} of the "
        f"${monthly_usd_cap:.2f} budget cap."
    )
