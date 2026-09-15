"""We Work Remotely category RSS source.

Verified live 2026-09-16: https://weworkremotely.com/categories/<slug>.rss.
Of PROJECT.md's 6 target categories, only 2 have a WWR equivalent: Support ->
"remote-customer-support-jobs", Sysadmin -> "remote-devops-sysadmin-jobs".
QA, QA Automation, ML AI and Python have no matching WWR category.

Item titles are "<Company>: <Job title>"; company is split out from there.
feedparser exposes WWR's non-standard RSS elements (region, country, skills,
type) as plain entry attributes, and its <category> element via both
entry.category and entry.tags (verified live against a real feed).
"""

from __future__ import annotations

import feedparser
import httpx

from job_agent.config import WwrSourceConfig
from job_agent.db import Posting
from job_agent.sources.base import entry_published_at, fetch_text, make_client

FEED_URL_TEMPLATE = "https://weworkremotely.com/categories/{slug}.rss"


def _split_title(raw_title: str) -> tuple[str, str | None]:
    if ": " in raw_title:
        company, title = raw_title.split(": ", 1)
        return title.strip(), company.strip()
    return raw_title.strip(), None


def parse_wwr_rss(raw_xml: str, category_slug: str) -> list[Posting]:
    feed: feedparser.FeedParserDict = feedparser.parse(raw_xml)
    postings = []
    for entry in feed.entries:
        title, company = _split_title(entry.title)
        postings.append(
            Posting(
                source="wwr",
                url=entry.link,
                title=title,
                description=entry.get("summary", ""),
                company=company,
                location=entry.get("region") or entry.get("country") or None,
                remote_type="remote",
                published_at=entry_published_at(entry),
                raw_tags=[entry.get("category") or category_slug],
            )
        )
    return postings


def fetch_wwr(client: httpx.Client, category_slug: str) -> str:
    return fetch_text(client, FEED_URL_TEMPLATE.format(slug=category_slug))


def collect_wwr(config: WwrSourceConfig, client: httpx.Client | None = None) -> list[Posting]:
    if not config.enabled or not config.categories:
        return []
    owns_client = client is None
    client = client or make_client()
    try:
        postings: list[Posting] = []
        for slug in config.categories:
            raw = fetch_wwr(client, slug)
            postings.extend(parse_wwr_rss(raw, slug))
        return postings
    finally:
        if owns_client:
            client.close()
