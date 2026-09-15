"""No-LLM prefilter: category allowlist + seniority stop-words.

See PROJECT.md §5.3. No country filter is applied here, deliberately, in
either mode.
"""

from __future__ import annotations

import re

from job_agent.db import Posting

# Titles/keywords that signal a role above what the candidate is targeting.
# Relaxed in wide mode: only the strongest signals are kept, so more postings
# reach LLM scoring (which can weigh seniority more precisely).
STRICT_SENIORITY_STOPWORDS = ["senior", "lead", "principal", "head", "architect"]
RELAXED_SENIORITY_STOPWORDS = ["principal", "head", "architect"]

# "5+ years", "5 + years", "5 years of experience", etc.
_YEARS_PATTERN = re.compile(r"(\d+)\s*\+?\s*years?\b", re.IGNORECASE)

STRICT_MIN_YEARS_THRESHOLD = 5
RELAXED_MIN_YEARS_THRESHOLD = 8


def _stopwords_for(seniority_strict: bool) -> tuple[list[str], int]:
    if seniority_strict:
        return STRICT_SENIORITY_STOPWORDS, STRICT_MIN_YEARS_THRESHOLD
    return RELAXED_SENIORITY_STOPWORDS, RELAXED_MIN_YEARS_THRESHOLD


def passes_category_allowlist(posting: Posting, allowed_categories: list[str]) -> bool:
    if not allowed_categories:
        return True
    allowed = {c.strip().lower() for c in allowed_categories}
    tags = {t.strip().lower() for t in posting.raw_tags}
    return bool(allowed & tags)


def passes_seniority_filter(posting: Posting, *, seniority_strict: bool) -> bool:
    stopwords, years_threshold = _stopwords_for(seniority_strict)
    haystack = f"{posting.title}\n{posting.description}".lower()

    if any(word in haystack for word in stopwords):
        return False

    for match in _YEARS_PATTERN.finditer(haystack):
        if int(match.group(1)) >= years_threshold:
            return False

    return True


def prefilter_postings(
    postings: list[Posting],
    *,
    categories: list[str],
    seniority_strict: bool,
) -> tuple[list[Posting], list[Posting]]:
    """Split postings into (kept, filtered_out)."""
    kept: list[Posting] = []
    filtered_out: list[Posting] = []

    for posting in postings:
        if not passes_category_allowlist(posting, categories):
            filtered_out.append(posting)
            continue
        if not passes_seniority_filter(posting, seniority_strict=seniority_strict):
            filtered_out.append(posting)
            continue
        kept.append(posting)

    return kept, filtered_out
