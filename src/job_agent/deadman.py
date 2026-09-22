"""Secondary (in-Actions) stale-pipeline check.

See the GitHub Actions deployment plan, amendment 2: the *primary*
dead-man's switch is external (an HTTP heartbeat service pinged by
pipeline.yml on success), because a check that only runs inside GitHub
Actions dies right along with Actions itself if that's what's broken.
This module is the secondary signal, run from `job telegram-process`: it
catches the case where Actions is fine but the *pipeline* keeps failing
(bad credentials, a broken source, etc.), which a heartbeat that only
fires on success can't distinguish from "Actions is down" on its own.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from job_agent import db

DEFAULT_STALE_THRESHOLD_HOURS = 36.0


def check_pipeline_staleness(
    conn: sqlite3.Connection,
    *,
    alert_flag_path: Path | str,
    threshold_hours: float = DEFAULT_STALE_THRESHOLD_HOURS,
    now: datetime | None = None,
) -> str | None:
    """Returns an alert message the first time the most recent recorded
    pipeline run is older than threshold_hours; None otherwise (including
    on every subsequent call during the same staleness episode -- the
    flag file at alert_flag_path deduplicates so this doesn't page every
    poll run). The flag is cleared once a fresh run is recorded, so the
    next episode alerts again."""
    alert_flag_path = Path(alert_flag_path)
    now = now or datetime.now(UTC)

    runs = db.get_recent_pipeline_runs(conn, limit=1)
    if not runs:
        return None  # nothing recorded yet -- e.g. a brand new setup

    finished_at = datetime.fromisoformat(runs[0]["finished_at"])
    age_hours = (now - finished_at).total_seconds() / 3600

    if age_hours <= threshold_hours:
        if alert_flag_path.exists():
            alert_flag_path.unlink()
        return None

    if alert_flag_path.exists():
        return None  # already alerted for this episode

    alert_flag_path.parent.mkdir(parents=True, exist_ok=True)
    alert_flag_path.write_text(finished_at.isoformat(), encoding="utf-8")
    return (
        f"No successful pipeline run in over {threshold_hours:.0f}h "
        f"(last: {finished_at.isoformat()})."
    )
