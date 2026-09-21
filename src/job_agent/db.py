"""SQLite storage: postings, their pipeline status, applications, and
per-call LLM cost log.

See PROJECT.md §5.8 for the status lifecycle. Beyond that spec, this module
also stores posting metadata (tech stack, salary breakdown, ATS vendor,
raw source payload, etc.), a scoring_runs history table (one row per
scoring attempt, so `job analyze --rescore` can re-score with a newer
model/prompt without losing older results), and an applications/
status-history schema -- purely for later analysis (`job stats`,
`job export`), nothing here changes pipeline behavior.
"""

from __future__ import annotations

import itertools
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
    raw_payload TEXT,
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
    tech_tags TEXT,
    portfolio_expected TEXT,
    company_type TEXT,
    seniority_label TEXT,
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

-- Full snapshot of every scoring attempt, including re-scores (job analyze
-- --rescore). postings.* scoring columns above hold only the "primary"
-- (first successful) result for backward-compat display purposes
-- (Telegram cards, job stats); this table is the complete history.
CREATE TABLE IF NOT EXISTS scoring_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id INTEGER NOT NULL REFERENCES postings (id),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
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
    tech_tags TEXT,
    portfolio_expected TEXT,
    company_type TEXT,
    seniority_label TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scoring_runs_posting_id ON scoring_runs (posting_id);

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

-- One row per Telegram card message sent for a posting, so `job
-- telegram-cleanup` can find and delete the bot's own low-score cards.
-- deleted_at is set once cleanup successfully deletes the message, so a
-- later cleanup run doesn't retry it.
CREATE TABLE IF NOT EXISTS telegram_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id INTEGER NOT NULL REFERENCES postings (id),
    message_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_telegram_messages_posting_id ON telegram_messages (posting_id);

-- One row per `job` pipeline execution (run_pipeline / job analyze
-- --readmit), so run-over-run volume/cost/funnel numbers don't have to be
-- reconstructed from postings.created_at ranges.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    collected_count INTEGER NOT NULL,
    new_count INTEGER NOT NULL,
    filtered_out_count INTEGER NOT NULL,
    scored_count INTEGER NOT NULL,
    score_failed_count INTEGER NOT NULL,
    source_counts TEXT NOT NULL,
    cost_usd REAL NOT NULL
);
"""

YEARS_EXPERIENCE_BUCKETS: list[tuple[float, float | None, str]] = [
    (0, 1, "0-1"),
    (1, 3, "1-3"),
    (3, 5, "3-5"),
    (5, None, "5+"),
]

PORTFOLIO_EXPECTATION_KEYS = ["github", "portfolio", "test_task", "none", "unstated"]

# Salary normalization: postings state salary as hour/day/month/year, and
# the LLM extracts the currency as-written (symbol or code). Averaging
# these directly mixes incomparable numbers (an hourly $50 and a monthly
# $5000 aren't on the same scale, and neither are USD and UAH) -- every
# salary aggregate below normalizes period to a monthly-equivalent and
# currency to a canonical code first, and drops rows where either is
# unknown rather than guessing. Hours/days-per-month use a standard
# 40h/week, 5-day/week convention (52 weeks/year / 12 months); this is an
# approximation, not a payroll-accurate conversion. Cross-currency
# conversion (e.g. EUR -> USD) is deliberately NOT applied: that needs a
# live, dated FX rate, so figures are reported per currency instead of
# being combined into one number.
_HOURS_PER_MONTH = 40 * 52 / 12  # ~173.3
_DAYS_PER_MONTH = 5 * 52 / 12  # ~21.7
_CURRENCY_ALIASES = {"$": "USD", "US$": "USD", "USD": "USD"}


def normalize_salary_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().upper()
    if not cleaned:
        return None
    return _CURRENCY_ALIASES.get(cleaned, cleaned)


def normalize_salary_to_monthly(value: float, period: str | None) -> float | None:
    """None if period is missing/unrecognized -- excluded rather than
    guessed by callers."""
    if period == "hour":
        return value * _HOURS_PER_MONTH
    if period == "day":
        return value * _DAYS_PER_MONTH
    if period == "month":
        return value
    if period == "year":
        return value / 12
    return None


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
    ats_vendor.py). raw_payload is a JSON dump of every field the source
    module had available for this item (not just what we normalize into
    the columns below) -- kept so a posting can be re-analyzed later
    without re-scraping; never discarded. Everything else metadata-related
    (tech_stack, salary breakdown, etc.) is LLM-extracted during scoring
    and set via update_posting_status() instead."""

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
    raw_payload: str | None = None
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
            raw_payload, ats_vendor, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            posting.raw_payload,
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


def get_all_postings(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> list[sqlite3.Row]:
    query = "SELECT * FROM postings ORDER BY id"
    if limit is not None:
        query += " LIMIT ?"
        return conn.execute(query, (limit,)).fetchall()
    return conn.execute(query).fetchall()


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


def category_not_allowed_counts_by_source(conn: sqlite3.Connection) -> dict[str, int]:
    """How many postings per source are currently rejected by the category
    allowlist specifically (prefilter.passes_category_allowlist), as
    opposed to the seniority filter -- lets a thin source config (e.g. a
    source missing a category/tag/keyword mapping for one of the target
    categories) show up as a number instead of silently under-collecting.
    status = 'filtered_out' so a posting `job analyze --readmit` later
    promotes to scored (which also clears its filtered_reason) drops out."""
    rows = conn.execute(
        "SELECT source, COUNT(*) AS n FROM postings "
        "WHERE status = 'filtered_out' AND filtered_reason = 'category_not_allowed' GROUP BY source"
    ).fetchall()
    return {row["source"]: row["n"] for row in rows}


# --- Scoring runs ---------------------------------------------------------


def insert_scoring_run(
    conn: sqlite3.Connection,
    *,
    posting_id: int,
    model: str,
    prompt_version: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    **result_fields: Any,
) -> int:
    """`result_fields` are the same ScoringResult-derived kwargs passed to
    update_posting_status() -- see scoring.py, which builds one dict and
    passes it to both."""
    columns = {
        "posting_id": posting_id,
        "model": model,
        "prompt_version": prompt_version,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "created_at": now_iso(),
        **result_fields,
    }
    col_names = ", ".join(columns.keys())
    placeholders = ", ".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO scoring_runs ({col_names}) VALUES ({placeholders})",
        tuple(columns.values()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_scoring_runs_for_posting(conn: sqlite3.Connection, posting_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scoring_runs WHERE posting_id = ? ORDER BY created_at", (posting_id,)
    ).fetchall()


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


# --- Telegram messages ---------------------------------------------------


def insert_telegram_message(conn: sqlite3.Connection, *, posting_id: int, message_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO telegram_messages (posting_id, message_id, sent_at) VALUES (?, ?, ?)",
        (posting_id, message_id, now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_deletable_telegram_messages(
    conn: sqlite3.Connection, *, below_score: int
) -> list[sqlite3.Row]:
    """Not-yet-deleted card messages for postings scored under below_score."""
    return conn.execute(
        """
        SELECT tm.id AS id, tm.posting_id AS posting_id, tm.message_id AS message_id
        FROM telegram_messages tm
        JOIN postings p ON p.id = tm.posting_id
        WHERE tm.deleted_at IS NULL AND p.fit_score < ?
        ORDER BY tm.id
        """,
        (below_score,),
    ).fetchall()


def mark_telegram_message_deleted(conn: sqlite3.Connection, telegram_message_id: int) -> None:
    conn.execute(
        "UPDATE telegram_messages SET deleted_at = ? WHERE id = ?",
        (now_iso(), telegram_message_id),
    )
    conn.commit()


# --- Pipeline runs ---------------------------------------------------


def insert_pipeline_run(
    conn: sqlite3.Connection,
    *,
    kind: str,
    started_at: str,
    finished_at: str,
    collected_count: int,
    new_count: int,
    filtered_out_count: int,
    scored_count: int,
    score_failed_count: int,
    source_counts: dict[str, int],
    cost_usd: float,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO pipeline_runs (
            kind, started_at, finished_at, collected_count, new_count,
            filtered_out_count, scored_count, score_failed_count,
            source_counts, cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            started_at,
            finished_at,
            collected_count,
            new_count,
            filtered_out_count,
            scored_count,
            score_failed_count,
            json.dumps(source_counts),
            cost_usd,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_recent_pipeline_runs(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (limit,)
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


def _midpoint_salary(row: sqlite3.Row) -> float | None:
    if row["salary_min"] is not None and row["salary_max"] is not None:
        return (row["salary_min"] + row["salary_max"]) / 2
    return row["salary_min"] if row["salary_min"] is not None else row["salary_max"]


def median_salary_by_tech_tag(conn: sqlite3.Connection) -> dict[str, dict[str, tuple[float, int]]]:
    """Returns {tag: {currency: (median_monthly_salary, sample_count)}} from
    the flat tech_stack list. Rows with an unrecognized/missing currency or
    period are excluded -- see the module-level salary-normalization note."""
    rows = conn.execute(
        "SELECT tech_stack, salary_min, salary_max, salary_currency, salary_period "
        "FROM postings "
        "WHERE tech_stack IS NOT NULL AND (salary_min IS NOT NULL OR salary_max IS NOT NULL)"
    ).fetchall()
    by_tag_currency: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        currency = normalize_salary_currency(row["salary_currency"])
        raw = _midpoint_salary(row)
        monthly = (
            normalize_salary_to_monthly(raw, row["salary_period"]) if raw is not None else None
        )
        if currency is None or monthly is None:
            continue
        for tag in json.loads(row["tech_stack"] or "[]"):
            by_tag_currency.setdefault(tag, {}).setdefault(currency, []).append(monthly)
    return {
        tag: {
            currency: (statistics.median(values), len(values))
            for currency, values in by_currency.items()
        }
        for tag, by_currency in by_tag_currency.items()
    }


def salary_stats_by_currency(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Median monthly-equivalent salary per canonical currency, among scored
    postings with both a numeric salary and a recognized period. See the
    module-level salary-normalization note for why currencies aren't
    combined into one figure."""
    rows = conn.execute(
        "SELECT salary_min, salary_max, salary_currency, salary_period FROM postings "
        "WHERE status = 'scored' AND (salary_min IS NOT NULL OR salary_max IS NOT NULL)"
    ).fetchall()
    by_currency: dict[str, list[float]] = {}
    for row in rows:
        currency = normalize_salary_currency(row["salary_currency"])
        raw = _midpoint_salary(row)
        monthly = (
            normalize_salary_to_monthly(raw, row["salary_period"]) if raw is not None else None
        )
        if currency is None or monthly is None:
            continue
        by_currency.setdefault(currency, []).append(monthly)
    return {
        currency: {"median_monthly": statistics.median(values), "count": len(values)}
        for currency, values in by_currency.items()
    }


def stats_by_cv_track(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Per recommended_cv_track, among scored postings: count and average
    fit_score. (Reply rate by cv_track -- among submitted applications --
    is separately available via reply_rate_by("cv_track").)"""
    rows = conn.execute(
        "SELECT recommended_cv_track, fit_score FROM postings "
        "WHERE status = 'scored' AND recommended_cv_track IS NOT NULL"
    ).fetchall()
    by_track: dict[str, list[int]] = {}
    for row in rows:
        by_track.setdefault(row["recommended_cv_track"], []).append(row["fit_score"])
    return {
        track: {"count": len(scores), "avg_fit_score": statistics.mean(scores)}
        for track, scores in by_track.items()
    }


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


def _posting_tech_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT tech_tags, salary_min, salary_max, required_years_experience, "
        "published_at, created_at FROM postings WHERE tech_tags IS NOT NULL"
    ).fetchall()


def tech_tag_cooccurrence(
    conn: sqlite3.Connection, *, top_n: int = 10
) -> dict[str, list[tuple[tuple[str, ...], int]]]:
    """Top co-occurring tech_tags pairs and triples within the same posting."""
    pair_counter: Counter[tuple[str, ...]] = Counter()
    triple_counter: Counter[tuple[str, ...]] = Counter()
    for row in _posting_tech_tags(conn):
        tags = sorted({entry["tag"] for entry in json.loads(row["tech_tags"] or "[]")})
        pair_counter.update(itertools.combinations(tags, 2))
        triple_counter.update(itertools.combinations(tags, 3))
    return {
        "pairs": pair_counter.most_common(top_n),
        "triples": triple_counter.most_common(top_n),
    }


def tech_tag_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Per tag: frequency, required-vs-nice_to_have share, median salary of
    postings that mention it, and share of those postings asking for 0-1
    years of experience -- each with its underlying sample count."""
    accum: dict[str, dict[str, Any]] = {}
    for row in _posting_tech_tags(conn):
        if row["salary_min"] is not None and row["salary_max"] is not None:
            salary = (row["salary_min"] + row["salary_max"]) / 2
        else:
            salary = row["salary_min"] if row["salary_min"] is not None else row["salary_max"]
        years = row["required_years_experience"]

        seen: set[str] = set()
        for entry in json.loads(row["tech_tags"] or "[]"):
            tag = entry["tag"]
            if tag in seen:
                continue
            seen.add(tag)
            stat = accum.setdefault(
                tag,
                {
                    "frequency": 0,
                    "required_count": 0,
                    "salaries": [],
                    "junior_count": 0,
                    "years_count": 0,
                },
            )
            stat["frequency"] += 1
            if entry.get("priority") == "required":
                stat["required_count"] += 1
            if salary is not None:
                stat["salaries"].append(salary)
            if years is not None:
                stat["years_count"] += 1
                if years <= 1:
                    stat["junior_count"] += 1

    result: dict[str, dict[str, Any]] = {}
    for tag, stat in accum.items():
        frequency = stat["frequency"]
        years_count = stat["years_count"]
        result[tag] = {
            "frequency": frequency,
            "required_count": stat["required_count"],
            "required_share": stat["required_count"] / frequency if frequency else 0.0,
            "median_salary": statistics.median(stat["salaries"]) if stat["salaries"] else None,
            "salary_count": len(stat["salaries"]),
            "junior_count": stat["junior_count"],
            "years_count": years_count,
            "junior_share": stat["junior_count"] / years_count if years_count else 0.0,
        }
    return result


def tech_tag_monthly_trend(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """{tag: {"YYYY-MM": count}}, keyed by published_at (falling back to
    created_at when a source doesn't provide one)."""
    trend: dict[str, dict[str, int]] = {}
    for row in _posting_tech_tags(conn):
        date_str = row["published_at"] or row["created_at"]
        if not date_str:
            continue
        month = date_str[:7]
        for entry in json.loads(row["tech_tags"] or "[]"):
            tag = entry["tag"]
            trend.setdefault(tag, {}).setdefault(month, 0)
            trend[tag][month] += 1
    return trend


def portfolio_expectation_stats(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """Returns {key: (count, total_scored)} for github/portfolio/test_task/
    none/unstated, among scored postings."""
    rows = conn.execute(
        "SELECT portfolio_expected FROM postings WHERE status = 'scored'"
    ).fetchall()
    total = len(rows)
    counter: Counter[str] = Counter(row["portfolio_expected"] or "unstated" for row in rows)
    return {key: (counter.get(key, 0), total) for key in PORTFOLIO_EXPECTATION_KEYS}
