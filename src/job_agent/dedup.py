"""Deduplication: canonical-URL primary key plus a cross-source content hash.

See PROJECT.md §5.2. Company + title alone is not enough (the same company can
post the same title for different projects), so the cross-source key also
folds in the first ~500 characters of the (normalized) description.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from job_agent import db
from job_agent.db import Posting

_LEGAL_SUFFIXES = [
    "llc",
    "inc",
    "ltd",
    "gmbh",
    "s.r.o",
    "sro",
    "tov",
    "тов",
    "pp",
    "ооо",
    "corp",
    "co",
]

_DESCRIPTION_PREFIX_CHARS = 500


def canonicalize_url(url: str) -> str:
    """Lowercase scheme/host, drop query string and fragment, strip trailing slash."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_company(company: str | None) -> str:
    if not company:
        return ""
    normalized = _normalize_text(company)
    words = [w for w in normalized.split(" ") if w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def compute_dedup_hash(*, company: str | None, title: str, description: str) -> str:
    normalized_company = _normalize_company(company)
    normalized_title = _normalize_text(title)
    normalized_description = _normalize_text(description)[:_DESCRIPTION_PREFIX_CHARS]
    key = f"{normalized_company}|{normalized_title}|{normalized_description}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def dedup_new_postings(conn: sqlite3.Connection, postings: list[Posting]) -> list[Posting]:
    """Return the subset of `postings` not already seen (in this batch or in the DB).

    Mutates each returned posting in place, setting canonical_url and dedup_hash.
    """
    seen_canonical_urls: set[str] = set()
    seen_dedup_hashes: set[str] = set()
    new_postings: list[Posting] = []

    for posting in postings:
        canonical_url = canonicalize_url(posting.url)
        dedup_hash = compute_dedup_hash(
            company=posting.company,
            title=posting.title,
            description=posting.description,
        )

        if canonical_url in seen_canonical_urls or dedup_hash in seen_dedup_hashes:
            continue
        if db.posting_exists(conn, canonical_url=canonical_url, dedup_hash=dedup_hash):
            continue

        posting.canonical_url = canonical_url
        posting.dedup_hash = dedup_hash
        seen_canonical_urls.add(canonical_url)
        seen_dedup_hashes.add(dedup_hash)
        new_postings.append(posting)

    return new_postings
