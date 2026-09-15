"""LinkedIn source via python-jobspy's guest search endpoint.

Verified live 2026-09-16 with the installed python-jobspy==1.1.82:
`scrape_jobs(site_name="linkedin", ...)` uses LinkedIn's public guest job
search -- no login, no user account. `description` is None unless
`linkedin_fetch_description=True`, which triggers one extra request per
result; PROJECT.md caps this source at ~25-50 results/run with randomized
delays, so `collect_linkedin` splits the budget across keywords and sleeps
a random interval between per-keyword searches.
"""

from __future__ import annotations

import random
import time
from typing import Any

from jobspy import scrape_jobs

from job_agent.config import LinkedinSourceConfig
from job_agent.db import Posting


def _salary_raw(row: dict[str, Any]) -> str | None:
    min_amount = row.get("min_amount")
    max_amount = row.get("max_amount")
    if not min_amount and not max_amount:
        return None
    currency = row.get("currency") or ""
    return f"{currency} {min_amount or '?'}-{max_amount or '?'}".strip()


def row_to_posting(row: dict[str, Any]) -> Posting:
    date_posted = row.get("date_posted")
    return Posting(
        source="linkedin",
        url=row.get("job_url", ""),
        title=row.get("title", ""),
        description=row.get("description") or "",
        company=row.get("company"),
        location=row.get("location"),
        remote_type="remote" if row.get("is_remote") else None,
        published_at=date_posted.isoformat() if date_posted else None,
        salary_raw=_salary_raw(row),
        raw_tags=[],
    )


def collect_linkedin_for_keyword(keyword: str, *, results_wanted: int) -> list[Posting]:
    df = scrape_jobs(
        site_name="linkedin",
        search_term=keyword,
        results_wanted=results_wanted,
        linkedin_fetch_description=True,
    )
    return [row_to_posting(row) for row in df.to_dict(orient="records")]


def collect_linkedin(config: LinkedinSourceConfig) -> list[Posting]:
    if not config.enabled or not config.keywords:
        return []
    per_keyword = max(1, config.results_wanted // len(config.keywords))
    postings: list[Posting] = []
    for index, keyword in enumerate(config.keywords):
        postings.extend(collect_linkedin_for_keyword(keyword, results_wanted=per_keyword))
        if index < len(config.keywords) - 1:
            time.sleep(random.uniform(2, 6))
    return postings
