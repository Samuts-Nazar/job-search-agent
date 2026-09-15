"""SQLite storage: postings, their pipeline status, and per-call LLM cost log.

See PROJECT.md §5.8 for the status lifecycle and the fields `job stats` reports.
"""

from __future__ import annotations

import json
import sqlite3
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
    status TEXT NOT NULL DEFAULT 'new',
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
"""


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
    source: str
    url: str
    canonical_url: str
    dedup_hash: str
    title: str
    description: str
    company: str | None = None
    location: str | None = None
    remote_type: str | None = None
    published_at: str | None = None
    salary_raw: str | None = None
    raw_tags: list[str] = field(default_factory=list)


def posting_exists(conn: sqlite3.Connection, *, canonical_url: str, dedup_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM postings WHERE canonical_url = ? OR dedup_hash = ? LIMIT 1",
        (canonical_url, dedup_hash),
    ).fetchone()
    return row is not None


def insert_posting(
    conn: sqlite3.Connection, posting: Posting, status: PostingStatus = "new"
) -> int:
    ts = now_iso()
    cur = conn.execute(
        """
        INSERT INTO postings (
            source, url, canonical_url, dedup_hash, title, company, location,
            remote_type, description, published_at, salary_raw, raw_tags,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
