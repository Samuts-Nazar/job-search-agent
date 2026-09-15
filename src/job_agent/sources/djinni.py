"""Djinni RSS source.

Verified live 2026-09-16: https://djinni.co/jobs/rss/ takes a repeatable
`primary_keyword` query param (the feed's own category names, e.g. "QA",
"QA Automation", "Python") that OR-combines across repeats. Items have
title/link/description(HTML)/pubDate/guid/category -- no structured
company, location, salary or remote fields.
"""

from __future__ import annotations

import feedparser
import httpx

from job_agent.config import DjinniSourceConfig
from job_agent.db import Posting
from job_agent.sources.base import entry_published_at, fetch_text, make_client

FEED_URL = "https://djinni.co/jobs/rss/"


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
            )
        )
    return postings


def fetch_djinni(client: httpx.Client, categories: list[str]) -> str:
    params = [("primary_keyword", category) for category in categories]
    return fetch_text(client, FEED_URL, params=params)


def collect_djinni(config: DjinniSourceConfig, client: httpx.Client | None = None) -> list[Posting]:
    if not config.enabled or not config.categories:
        return []
    owns_client = client is None
    client = client or make_client()
    try:
        raw = fetch_djinni(client, config.categories)
        return parse_djinni_rss(raw)
    finally:
        if owns_client:
            client.close()
