"""Remote OK JSON source.

Verified live 2026-09-16: https://remoteok.com/api. The first array element
is a legal/attribution notice, not a posting -- their ToS requires a visible
link back to Remote OK wherever postings are shown (see ATTRIBUTION_*
below). The documented `?tag=` query param does NOT filter server-side
(confirmed live: `?tag=python` returned unrelated roles like HR Specialist),
so tag filtering is done client-side against each posting's `tags` array.
"""

from __future__ import annotations

import json

import httpx

from job_agent.config import RemoteOkSourceConfig
from job_agent.db import Posting
from job_agent.sources.base import fetch_text, make_client

API_URL = "https://remoteok.com/api"
ATTRIBUTION_URL = "https://remoteok.com"
ATTRIBUTION_TEXT = "Jobs via Remote OK"


def _salary_raw(item: dict) -> str | None:
    salary_min = item.get("salary_min") or None
    salary_max = item.get("salary_max") or None
    if not salary_min and not salary_max:
        return None
    return f"${salary_min or '?'}-{salary_max or '?'}"


def parse_remoteok_json(raw_json: str, *, tags: list[str]) -> list[Posting]:
    data = json.loads(raw_json)
    wanted = {t.lower() for t in tags}

    postings = []
    for item in data:
        if "id" not in item:  # the legal/attribution notice has no id
            continue
        item_tags: list[str] = item.get("tags", [])
        if wanted and not (wanted & {t.lower() for t in item_tags}):
            continue
        postings.append(
            Posting(
                source="remoteok",
                url=item.get("url") or item.get("apply_url"),
                title=item.get("position", ""),
                description=item.get("description", ""),
                company=item.get("company"),
                location=item.get("location") or None,
                remote_type="remote",
                published_at=item.get("date"),
                salary_raw=_salary_raw(item),
                raw_tags=item_tags,
            )
        )
    return postings


def fetch_remoteok(client: httpx.Client) -> str:
    return fetch_text(client, API_URL)


def collect_remoteok(
    config: RemoteOkSourceConfig, client: httpx.Client | None = None
) -> list[Posting]:
    if not config.enabled:
        return []
    owns_client = client is None
    client = client or make_client()
    try:
        raw = fetch_remoteok(client)
        return parse_remoteok_json(raw, tags=config.tags)
    finally:
        if owns_client:
            client.close()
