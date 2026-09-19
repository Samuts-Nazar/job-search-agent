"""SQLite storage: postings, their pipeline status, applications, and
per-call LLM cost log.

See PROJECT.md §5.8 for the status lifecycle. Beyond that spec, this module
also stores posting metadata (tech stack, salary breakdown, ATS vendor,
etc.) and an applications/status-history schema purely for later analysis
(`job stats`, `job export`) -- nothing here changes pipeline behavior.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

PostingStatus = Literal[
    "new",
    "filtered_out",
    "scored",
    "score_failed",
    "skipped",
    "package_ready",
    "form_filled",
    "submitted",
    "manual",
    "replied",
    "rejected",
    "interview",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    dedup_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    remote_type TEXT,
    description TEXT NOT NULL,
    published_at TEXT,
    salary_raw TEXT,
    raw_tags TEXT,
    ats_vendor TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    filtered_reason TEXT,
    fit_score INTEGER,
    verdict TEXT,
    matched_skills TEXT,
    missing_skills TEXT,
    seniority_match TEXT,
    required_languages TEXT,
    language_mismatch INTEGER,
    relocation_offered TEXT,
    recommended_cv_track TEXT,
    rationale TEXT,
    tech_stack TEXT,
    required_years_experience REAL,
    seniority_level TEXT,
    work_format TEXT,
    country TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    salary_period TEXT,
    score_failed_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_postings_dedup_hash ON postings (dedup_hash);
CREATE INDEX IF NOT EXISTS idx_postings_status ON postings (status);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id INTEGER REFERENCES postings (id),
    tier TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_posting_id ON llm_calls (posting_id);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id INTEGER NOT NULL REFERENCES postings (id),
    cv_track TEXT,
    letter_version TEXT,
    model TEXT,
    cost_usd REAL,
    submitted_at TEXT,
    first_reply_at TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_posting_id ON applications (posting_id);

CREATE TABLE IF NOT EXISTS application_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications (id),
    status TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_application_status_history_application_id
    ON application_status_history (application_id);
"""

YEARS_EXPERIENCE_BUCKETS: list[tuple[float, float | None, str]] = [
    (0, 1, "0-1"),
    (1, 3, "1-3"),
    (3, 5, "3-5"),
    (5, None, "5+"),
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@dataclass
class Posting:
    """A normalized posting. Sources produce these; dedup.py fills in
    canonical_url and dedup_hash before insert_posting() is called.
    ats_vendor is detected from the URL at collection time (see
    ats_vendor.py) -- everything else metadata-related (tech_stack,
    salary breakdown, etc.) is LLM-extracted during scoring and set via
    update_posting_status() instead."""

    source: str
    url: str
    title: str
    description: str
    company: str | None = None
    location: str | None = None
    remote_type: str | None = None
    published_at: str | None = None
    salary_raw: str | None = None
    raw_tags: list[str] = field(default_factory=list)
    ats_vendor: str | None = None
    canonical_url: str | None = None
    dedup_hash: str | None = None


def posting_exists(conn: sqlite3.Connection, *, canonical_url: str, dedup_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM postings WHERE canonical_url = ? OR dedup_hash = ? LIMIT 1",
        (canonical_url, dedup_hash),
    ).fetchone()
    return row is not None


def insert_posting(
    conn: sqlite3.Connection, posting: Posting, status: PostingStatus = "new"
) -> int:
    if posting.canonical_url is None or posting.dedup_hash is None:
        raise ValueError("posting.canonical_url and .dedup_hash must be set before insert")
    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO postings (
            source, url, canonical_url, dedup_hash, title, company, location,
            remote_type, description, published_at, salary_raw, raw_tags,
            ats_vendor, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            posting.source,
            posting.url,
            posting.canonical_url,
            posting.dedup_hash,
            posting.title,
            posting.company,
            posting.location,
            posting.remote_type,
            posting.description,
            posting.published_at,
            posting.salary_raw,
            json.dumps(posting.raw_tags),
            posting.ats_vendor,
            status,
            ts,
            ts,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_posting_status(
    conn: sqlite3.Connection, posting_id: int, status: PostingStatus, **fields: Any
) -> None:
    columns = {"status": status, "updated_at": now_iso(), **fields}
    set_clause = ", ".join(f"{col} = ?" for col in columns)
    conn.execute(
        f"UPDATE postings SET {set_clause} WHERE id = ?",
        (*columns.values(), posting_id),
    )
    conn.commit()


def get_posting(conn: sqlite3.Connection, posting_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()


def get_postings_by_status(conn: sqlite3.Connection, status: PostingStatus) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM postings WHERE status = ? ORDER BY created_at", (status,)
    ).fetchall()


def record_llm_call(
    conn: sqlite3.Connection,
    *,
    posting_id: int | None,
    tier: str,
    model: str,
    purpose: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO llm_calls (
            posting_id, tier, model, purpose, input_tokens, output_tokens, cost_usd, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (posting_id, tier, model, purpose, input_tokens, output_tokens, cost_usd, now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def total_cost_usd(conn: sqlite3.Connection, *, since_iso: str | None = None) -> float:
    if since_iso:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE created_at >= ?",
            (since_iso,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()
    return float(row[0])


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM postings GROUP BY status").fetchall()
    return {row["status"]: row["n"] for row in rows}


def source_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT source, COUNT(*) AS n FROM postings GROUP BY source").fetchall()
    return {row["source"]: row["n"] for row in rows}


# --- Applications ------------------------------------------------------
#
# Schema-only for now: no Phase 1/2 code path creates rows here yet (that's
# Phase 3's form-fill/submission flow). Exists so the data is there once
# that flow lands.


@dataclass
class Application:
    posting_id: int
    cv_track: str | None = None
    letter_version: str | None = None
    model: str | None = None
    cost_usd: float | None = None
    submitted_at: str | None = None
    first_reply_at: str | None = None
    rejection_reason: str | None = None


def insert_application(conn: sqlite3.Connection, application: Application) -> int:
    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO applications (
            posting_id, cv_track, letter_version, model, cost_usd,
            submitted_at, first_reply_at, rejection_reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application.posting_id,
            application.cv_track,
            application.letter_version,
            application.model,
            application.cost_usd,
            application.submitted_at,
            application.first_reply_at,
            application.rejection_reason,
            ts,
            ts,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_application(conn: sqlite3.Connection, application_id: int, **fields: Any) -> None:
    columns = {"updated_at": now_iso(), **fields}
    set_clause = ", ".join(f"{col} = ?" for col in columns)
    conn.execute(
        f"UPDATE applications SET {set_clause} WHERE id = ?",
        (*columns.values(), application_id),
    )
    conn.commit()


def get_application(conn: sqlite3.Connection, application_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()


def get_applications_for_posting(
    conn: sqlite3.Connection, posting_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM applications WHERE posting_id = ? ORDER BY created_at", (posting_id,)
    ).fetchall()


def record_application_status(
    conn: sqlite3.Connection, application_id: int, status: str
) -> None:
    conn.execute(
        "INSERT INTO application_status_history (application_id, status, changed_at) "
        "VALUES (?, ?, ?)",
        (application_id, status, now_iso()),
    )
    conn.commit()


def get_application_status_history(
    conn: sqlite3.Connection, application_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM application_status_history WHERE application_id = ? ORDER BY changed_at",
        (application_id,),
    ).fetchall()


# --- Aggregates for `job stats` -----------------------------------------


def missing_skills_frequency(conn: sqlite3.Connection, limit: int = 20) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT missing_skills FROM postings WHERE missing_skills IS NOT NULL"
    ).fetchall()
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(json.loads(row["missing_skills"] or "[]"))
    return counter.most_common(limit)


def reply_rate_by(
    conn: sqlite3.Connection, dimension: Literal["cv_track", "source", "country"]
) -> dict[str, tuple[int, int]]:
    """Returns {group: (replied_count, submitted_count)} among applications
    that were actually submitted."""
    column_map = {
        "cv_track": "a.cv_track",
        "source": "p.source",
        "country": "p.country",
    }
    if dimension not in column_map:
        raise ValueError(f"unknown dimension: {dimension!r}")
    column = column_map[dimension]
    rows = conn.execute(
        f"""
        SELECT {column} AS grp,
               SUM(CASE WHEN a.first_reply_at IS NOT NULL THEN 1 ELSE 0 END) AS replied,
               COUNT(*) AS total
        FROM applications a
        JOIN postings p ON p.id = a.posting_id
        WHERE a.submitted_at IS NOT NULL
        GROUP BY {column}
        """
    ).fetchall()
    return {(row["grp"] or "unknown"): (row["replied"], row["total"]) for row in rows}


def median_salary_by_tech_tag(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    """Returns {tag: (median_salary, sample_count)}."""
    rows = conn.execute(
        "SELECT tech_stack, salary_min, salary_max FROM postings "
        "WHERE tech_stack IS NOT NULL AND (salary_min IS NOT NULL OR salary_max IS NOT NULL)"
    ).fetchall()
    by_tag: dict[str, list[float]] = {}
    for row in rows:
        if row["salary_min"] is not None and row["salary_max"] is not None:
            value = (row["salary_min"] + row["salary_max"]) / 2
        else:
            value = row["salary_min"] if row["salary_min"] is not None else row["salary_max"]
        for tag in json.loads(row["tech_stack"] or "[]"):
            by_tag.setdefault(tag, []).append(value)
    return {tag: (statistics.median(values), len(values)) for tag, values in by_tag.items()}


def required_years_experience_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT required_years_experience FROM postings WHERE required_years_experience IS NOT NULL"
    ).fetchall()
    buckets = {label: 0 for _, _, label in YEARS_EXPERIENCE_BUCKETS}
    for row in rows:
        years = row["required_years_experience"]
        for lo, hi, label in YEARS_EXPERIENCE_BUCKETS:
            if years >= lo and (hi is None or years < hi):
                buckets[label] += 1
                break
    return buckets


def median_days_to_first_reply(conn: sqlite3.Connection) -> tuple[float, int] | None:
    rows = conn.execute(
        "SELECT submitted_at, first_reply_at FROM applications "
        "WHERE submitted_at IS NOT NULL AND first_reply_at IS NOT NULL"
    ).fetchall()
    if not rows:
        return None
    deltas = []
    for row in rows:
        submitted = datetime.fromisoformat(row["submitted_at"])
        replied = datetime.fromisoformat(row["first_reply_at"])
        deltas.append((replied - submitted).total_seconds() / 86400)
    return statistics.median(deltas), len(deltas)
