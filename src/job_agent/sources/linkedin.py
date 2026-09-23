"""LinkedIn source via python-jobspy's guest search endpoint.

Verified live 2026-09-16 with the installed python-jobspy==1.1.82:
`scrape_jobs(site_name="linkedin", ...)` uses LinkedIn's public guest job
search -- no login, no user account. `description` is None unless
`linkedin_fetch_description=True`, which triggers one extra request per
result; PROJECT.md caps this source at ~25-50 results/run with randomized
delays, so `collect_linkedin` splits the budget across keywords and sleeps
a random interval between per-keyword searches.

Investigated live 2026-09-23 why real runs were landing around ~33
postings against a configured results_wanted of 40 (well under
Djinni/DOU's per-run volume): ran collect_linkedin_for_keyword directly
against the real guest endpoint for every configured keyword at the
then-current per-keyword budget. Every single keyword returned exactly
the number of results requested, with zero duplicates across keywords and
no 429s/shortfalls -- ruling out guest-endpoint throttling, the search
terms themselves, and category mapping as the bottleneck. The actual
cause was `_split_budget`'s predecessor (`total // len(keywords)`), which
silently discarded the remainder (40 // 6 = 6/keyword, using only 36 of
the configured 40 every run). `_split_budget` now distributes the
remainder instead of dropping it. The ~25-50/run ceiling itself is a
deliberate PROJECT.md safety choice (line 85), not a technical limit this
investigation found -- results_wanted should stay inside that band rather
than being raised past it.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from jobspy import scrape_jobs

from job_agent.config import LinkedinSourceConfig
from job_agent.db import Posting


def _clean(value: Any) -> Any:
    """pandas represents missing cells as float NaN, not None -- normalize both to None."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN != NaN
        return None
    return value


def _salary_raw(row: dict[str, Any]) -> str | None:
    min_amount = _clean(row.get("min_amount"))
    max_amount = _clean(row.get("max_amount"))
    if not min_amount and not max_amount:
        return None
    currency = _clean(row.get("currency")) or ""
    return f"{currency} {min_amount or '?'}-{max_amount or '?'}".strip()


def row_to_posting(row: dict[str, Any]) -> Posting:
    date_posted = _clean(row.get("date_posted"))
    return Posting(
        source="linkedin",
        url=_clean(row.get("job_url")) or "",
        title=_clean(row.get("title")) or "",
        description=_clean(row.get("description")) or "",
        company=_clean(row.get("company")),
        location=_clean(row.get("location")),
        remote_type="remote" if row.get("is_remote") else None,
        published_at=date_posted.isoformat() if date_posted else None,
        salary_raw=_salary_raw(row),
        raw_tags=[],
        raw_payload=json.dumps(row, default=str, ensure_ascii=False),
    )


def collect_linkedin_for_keyword(keyword: str, *, results_wanted: int) -> list[Posting]:
    df = scrape_jobs(
        site_name="linkedin",
        search_term=keyword,
        results_wanted=results_wanted,
        linkedin_fetch_description=True,
    )
    return [row_to_posting(row) for row in df.to_dict(orient="records")]


def _split_budget(total: int, n: int) -> list[int]:
    """Distributes `total` across `n` keywords as evenly as possible,
    handing the remainder to the first keywords one each rather than
    floor-dividing and silently discarding it -- verified live 2026-09-23
    that a plain `total // n` split throws away up to n-1 results/run for
    free (each keyword request returned exactly what was asked, no
    guest-endpoint shortfall observed), which was the actual reason
    real runs landed under the configured results_wanted rather than any
    LinkedIn-side limit."""
    base, remainder = divmod(total, n)
    return [max(1, base + (1 if i < remainder else 0)) for i in range(n)]


def collect_linkedin(config: LinkedinSourceConfig) -> list[Posting]:
    if not config.enabled or not config.keywords:
        return []
    budgets = _split_budget(config.results_wanted, len(config.keywords))
    postings: list[Posting] = []
    for index, (keyword, per_keyword) in enumerate(zip(config.keywords, budgets, strict=True)):
        postings.extend(collect_linkedin_for_keyword(keyword, results_wanted=per_keyword))
        if index < len(config.keywords) - 1:
            time.sleep(random.uniform(2, 6))
    return postings
