"""DOU.ua RSS source.

Verified live 2026-09-16: https://jobs.dou.ua/vacancies/feeds/ takes a single
`category` query param per request (values like "QA", "Python", "Support",
"SysAdmin", "AI/ML" -- no separate "QA Automation", DOU's "QA" covers both
manual and automation). Items only have title/link/description(HTML) -- no
pubDate/guid/category per item, and no dedicated remote-only filter param.

Company, salary and location are packed into the title as free text, e.g.
"Manual QA Engineer в Solidium Technology, $700-1000, віддалено"
("<title> в <company>, <salary>, <location-or-remote-marker>").

Verified live 2026-09-24 (found via a real end-to-end CV package check --
posting #110's title showed up as "AI &amp; SaaS" verbatim): feedparser
marks DOU's <title>/<description> as content type text/html, and for that
type it deliberately leaves entities un-decoded (its convention is that
html-typed content may be double-escaped, so decoding is left to the
consumer). html.unescape() on both fixes it; description keeps its real
HTML tags (`&lt;`/no matching pair was never present in stored data, so
this isn't double-escaped HTML, just plain entity leakage).
"""

from __future__ import annotations

import html
import re

import feedparser
import httpx

from job_agent.config import DouSourceConfig
from job_agent.db import Posting
from job_agent.sources.base import entry_raw_payload, fetch_text, make_client

FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"

_TITLE_RE = re.compile(r"^(?P<title>.+?)\s+в\s+(?P<company>[^,]+?)(?:,\s*(?P<rest>.+))?$")
_SALARY_RE = re.compile(r"^\$[\d,‒–—\-]+$")
_REMOTE_MARKER = "віддалено"


def _parse_title(raw_title: str) -> tuple[str, str | None, str | None, str | None, bool]:
    """Returns (title, company, salary_raw, location, is_remote)."""
    match = _TITLE_RE.match(raw_title)
    if not match:
        return raw_title, None, None, None, False

    title = match.group("title").strip()
    company = match.group("company").strip()
    salary: str | None = None
    location_parts: list[str] = []
    is_remote = False

    rest = match.group("rest")
    if rest:
        for token in (t.strip() for t in rest.split(",")):
            if not token:
                continue
            elif _SALARY_RE.match(token):
                salary = token
            elif token == _REMOTE_MARKER:
                is_remote = True
            else:
                location_parts.append(token)

    location = ", ".join(location_parts) or None
    return title, company, salary, location, is_remote


def parse_dou_rss(raw_xml: str, category: str) -> list[Posting]:
    feed: feedparser.FeedParserDict = feedparser.parse(raw_xml)
    postings = []
    for entry in feed.entries:
        raw_title = html.unescape(entry.title)
        description = html.unescape(entry.get("summary", ""))
        title, company, salary, location, is_remote = _parse_title(raw_title)
        postings.append(
            Posting(
                source="dou",
                url=entry.link,
                title=title,
                description=description,
                company=company,
                location=location,
                remote_type="remote" if is_remote else None,
                salary_raw=salary,
                raw_tags=[category],
                raw_payload=entry_raw_payload(entry),
            )
        )
    return postings


def fetch_dou(client: httpx.Client, category: str) -> str:
    return fetch_text(client, FEED_URL, params={"category": category})


def collect_dou(config: DouSourceConfig, client: httpx.Client | None = None) -> list[Posting]:
    if not config.enabled or not config.categories:
        return []
    owns_client = client is None
    client = client or make_client()
    try:
        postings: list[Posting] = []
        for category in config.categories:
            raw = fetch_dou(client, category)
            postings.extend(parse_dou_rss(raw, category))
        return postings
    finally:
        if owns_client:
            client.close()
