"""`job export`: dumps postings, scores, and applications to CSV for
analysis outside the tool. See PROJECT.md §5.8 ("these numbers are real and
may later be used as project metrics").
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

POSTINGS_COLUMNS = [
    "id",
    "source",
    "url",
    "canonical_url",
    "title",
    "company",
    "location",
    "remote_type",
    "country",
    "work_format",
    "published_at",
    "salary_raw",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "ats_vendor",
    "status",
    "filtered_reason",
    "created_at",
    "updated_at",
]

SCORES_COLUMNS = [
    "id",
    "title",
    "source",
    "status",
    "fit_score",
    "verdict",
    "matched_skills",
    "missing_skills",
    "seniority_match",
    "seniority_level",
    "required_years_experience",
    "required_languages",
    "language_mismatch",
    "relocation_offered",
    "tech_stack",
    "recommended_cv_track",
    "rationale",
    "filtered_reason",
    "score_failed_reason",
]

APPLICATIONS_COLUMNS = [
    "id",
    "posting_id",
    "cv_track",
    "letter_version",
    "model",
    "cost_usd",
    "submitted_at",
    "first_reply_at",
    "rejection_reason",
    "created_at",
    "updated_at",
]

SUPPORTED_FORMATS = ("csv",)


def _write_csv(rows: list[sqlite3.Row], columns: list[str], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[col] for col in columns])
    return len(rows)


def export_postings(conn: sqlite3.Connection, path: Path | str, *, fmt: str = "csv") -> int:
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported export format: {fmt!r}")
    rows = conn.execute(
        f"SELECT {', '.join(POSTINGS_COLUMNS)} FROM postings ORDER BY id"
    ).fetchall()
    return _write_csv(rows, POSTINGS_COLUMNS, Path(path))


def export_scores(conn: sqlite3.Connection, path: Path | str, *, fmt: str = "csv") -> int:
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported export format: {fmt!r}")
    rows = conn.execute(
        f"SELECT {', '.join(SCORES_COLUMNS)} FROM postings ORDER BY id"
    ).fetchall()
    return _write_csv(rows, SCORES_COLUMNS, Path(path))


def export_applications(conn: sqlite3.Connection, path: Path | str, *, fmt: str = "csv") -> int:
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported export format: {fmt!r}")
    rows = conn.execute(
        f"SELECT {', '.join(APPLICATIONS_COLUMNS)} FROM applications ORDER BY id"
    ).fetchall()
    return _write_csv(rows, APPLICATIONS_COLUMNS, Path(path))


def export_all(
    conn: sqlite3.Connection, out_dir: Path | str, *, fmt: str = "csv"
) -> dict[str, int]:
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported export format: {fmt!r}")
    out_dir = Path(out_dir)
    return {
        "postings": export_postings(conn, out_dir / f"postings.{fmt}", fmt=fmt),
        "scores": export_scores(conn, out_dir / f"scores.{fmt}", fmt=fmt),
        "applications": export_applications(conn, out_dir / f"applications.{fmt}", fmt=fmt),
    }
