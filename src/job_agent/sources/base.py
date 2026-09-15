"""Shared polite-HTTP helper for RSS/JSON sources.

Each source module separates fetch (network I/O) from parse (pure function
over the raw response body) so unit tests can exercise parsing against
recorded fixtures without any network access.
"""

from __future__ import annotations

from datetime import UTC, datetime

import feedparser
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

USER_AGENT = (
    "Mozilla/5.0 (compatible; job-search-agent/0.1; "
    "+https://github.com/Samuts-Nazar/job-search-agent)"
)

DEFAULT_TIMEOUT = 15.0


def make_client() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT})


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
def fetch_text(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
) -> str:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.text


def entry_published_at(entry: feedparser.FeedParserDict) -> str | None:
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=UTC).isoformat()
