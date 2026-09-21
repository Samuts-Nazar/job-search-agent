"""Djinni RSS source.

Verified live 2026-09-16: https://djinni.co/jobs/rss/ takes a repeatable
`primary_keyword` query param (the feed's own category names, e.g. "QA",
"QA Automation", "Python") that OR-combines across repeats. Items have
title/link/description(HTML)/pubDate/guid/category -- no structured
company, location, salary or remote fields.

Verified live 2026-09-22: the company is genuinely absent from the RSS feed
itself (confirmed against stored raw_payload -- e.g. a raw item title is
just "Data Scientist", no company suffix), but each posting's own live page
has it in <meta property="og:title" content="<title> в <company> ">, the
same "в" (Ukrainian "at/in") construction DOU's RSS titles use elsewhere.
Recovering it means one extra HTTP GET per posting -- see backfill_company().
"""

from __future__ import annotations

import html
import random
import re
import time

import feedparser
import httpx

from job_agent.config import DjinniSourceConfig
from job_agent.db import Posting
from job_agent.sources.base import (
    entry_published_at,
    entry_raw_payload,
    fetch_text,
    make_client,
    parse_title_company_uk,
)

FEED_URL = "https://djinni.co/jobs/rss/"
_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"')

# Politeness delay between per-posting page fetches (backfill_company),
# since the RSS feed alone gives no company -- see module docstring.
_FETCH_DELAY_RANGE = (0.3, 0.7)


def parse_djinni_rss(raw_xml: str) -> list[Posting]:
    feed = feedparser.parse(raw_xml)
    postings = []
    for entry in feed.entries:
        tags = [tag.get("term", "") for tag in entry.get("tags", []) if tag.get("term")]
        postings.append(
            Posting(
                source="djinni",
                url=entry.link,
                title=entry.title,
                description=entry.get("summary", ""),
                published_at=entry_published_at(entry),
                raw_tags=tags,
                raw_payload=entry_raw_payload(entry),
            )
        )
    return postings


def fetch_djinni(client: httpx.Client, categories: list[str]) -> str:
    params = [("primary_keyword", category) for category in categories]
    return fetch_text(client, FEED_URL, params=params)


def fetch_djinni_posting_page(client: httpx.Client, url: str) -> str:
    return fetch_text(client, url)


def parse_djinni_company(page_html: str) -> str | None:
    """Extracts the company from a Djinni posting page's og:title meta tag
    (format: "<title> в <company> "). Returns None if the tag is missing or
    doesn't match the pattern."""
    match = _OG_TITLE_RE.search(page_html)
    if not match:
        return None
    og_title = html.unescape(match.group(1))
    _, company = parse_title_company_uk(og_title)
    return company


def backfill_company(client: httpx.Client, posting: Posting) -> None:
    """Fetches the posting's live page and fills in posting.company in
    place, if not already set. Best-effort: leaves company as None on any
    HTTP failure rather than failing the whole collection run."""
    if posting.company:
        return
    try:
        page = fetch_djinni_posting_page(client, posting.url)
    except httpx.HTTPError:
        return
    posting.company = parse_djinni_company(page)


def collect_djinni(config: DjinniSourceConfig, client: httpx.Client | None = None) -> list[Posting]:
    if not config.enabled or not config.categories:
        return []
    owns_client = client is None
    client = client or make_client()
    try:
        raw = fetch_djinni(client, config.categories)
        postings = parse_djinni_rss(raw)
        for i, posting in enumerate(postings):
            backfill_company(client, posting)
            if i < len(postings) - 1:
                time.sleep(random.uniform(*_FETCH_DELAY_RANGE))
        return postings
    finally:
        if owns_client:
            client.close()
