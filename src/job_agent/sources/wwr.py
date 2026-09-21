"""We Work Remotely category RSS source.

Verified live 2026-09-16: https://weworkremotely.com/categories/<slug>.rss.
Of PROJECT.md's 6 target categories, only 2 have a WWR equivalent: Support ->
"remote-customer-support-jobs", Sysadmin -> "remote-devops-sysadmin-jobs".
QA, QA Automation, and ML AI have no WWR category, and no reasonable proxy
category to keyword-filter either -- left uncovered rather than
keyword-scanning unrelated categories for a low-precision match.

Verified live 2026-09-22: WWR's "Advanced Remote Job Search" page
(weworkremotely.com/remote-jobs/search?term=...) and its RSS's own ?term=
param are NOT filtered server-side -- the returned feed is identical
regardless of the term (same finding as remoteok.py's ?tag=). WWR does
publish "remote-back-end-programming-jobs" and
"remote-full-stack-programming-jobs" categories, though, which are a sound
proxy for Python: config.sources.wwr.keyword_categories maps a category
slug to keywords matched client-side against each item's title (see
_matches_keywords), reusing the same per-category RSS fetch as `categories`.

Item titles are "<Company>: <Job title>"; company is split out from there.
feedparser exposes WWR's non-standard RSS elements (region, country, skills,
type) as plain entry attributes, and its <category> element via both
entry.category and entry.tags (verified live against a real feed).
"""

from __future__ import annotations

import re

import feedparser
import httpx

from job_agent.config import WwrSourceConfig
from job_agent.db import Posting
from job_agent.sources.base import entry_published_at, entry_raw_payload, fetch_text, make_client

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
                raw_payload=entry_raw_payload(entry),
            )
        )
    return postings


def fetch_wwr(client: httpx.Client, category_slug: str) -> str:
    return fetch_text(client, FEED_URL_TEMPLATE.format(slug=category_slug))


def matches_keywords(title: str, keywords: list[str]) -> bool:
    haystack = title.lower()
    return any(re.search(rf"\b{re.escape(keyword.lower())}\b", haystack) for keyword in keywords)


def collect_wwr(config: WwrSourceConfig, client: httpx.Client | None = None) -> list[Posting]:
    if not config.enabled or not (config.categories or config.keyword_categories):
        return []
    owns_client = client is None
    client = client or make_client()
    try:
        postings: list[Posting] = []
        for slug in config.categories:
            raw = fetch_wwr(client, slug)
            postings.extend(parse_wwr_rss(raw, slug))
        for slug, keywords in config.keyword_categories.items():
            raw = fetch_wwr(client, slug)
            candidates = parse_wwr_rss(raw, slug)
            postings.extend(p for p in candidates if matches_keywords(p.title, keywords))
        return postings
    finally:
        if owns_client:
            client.close()
