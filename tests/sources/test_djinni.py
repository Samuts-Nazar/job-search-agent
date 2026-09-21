import json
from pathlib import Path

import httpx

from job_agent import db
from job_agent.db import Posting
from job_agent.dedup import canonicalize_url
from job_agent.sources import djinni
from job_agent.sources.djinni import backfill_company, parse_djinni_company, parse_djinni_rss

FIXTURE = Path(__file__).parent / "fixtures" / "djinni_rss_qa_automation.xml"

# Real og:title snippets, verified live 2026-09-22 against
# https://djinni.co/jobs/819756-tech-support-engineer-l2/ and similar pages.
OG_TITLE_HTML = (
    '<html><head>'
    '<meta property="og:title" content="Tech Support Engineer (L2) в Zvook " />'
    '</head><body></body></html>'
)
OG_TITLE_HTML_NO_COMPANY = "<html><head><title>Djinni</title></head><body></body></html>"


def test_parse_djinni_rss_returns_normalized_postings():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_djinni_rss(raw)

    assert len(postings) == 5
    first = postings[0]
    assert first.source == "djinni"
    assert first.url.startswith("https://djinni.co/jobs/")
    assert first.title
    assert "QA Automation" in first.raw_tags or first.raw_tags == []
    assert first.description
    assert first.published_at is not None


def test_parse_djinni_rss_has_no_structured_company_or_location():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_djinni_rss(raw)
    for posting in postings:
        assert posting.company is None
        assert posting.location is None


def test_parse_djinni_rss_captures_raw_payload():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_djinni_rss(raw)
    first = postings[0]
    assert first.raw_payload is not None
    payload = json.loads(first.raw_payload)
    assert payload["title"] == first.title
    assert payload["link"] == first.url


def test_parse_djinni_company_extracts_from_og_title():
    assert parse_djinni_company(OG_TITLE_HTML) == "Zvook"


def test_parse_djinni_company_returns_none_when_tag_missing():
    assert parse_djinni_company(OG_TITLE_HTML_NO_COMPANY) is None


def _make_posting(**overrides) -> Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/819756-tech-support-engineer-l2/",
        title="Tech Support Engineer (L2)",
        description="desc",
    )
    defaults.update(overrides)
    return Posting(**defaults)


def test_backfill_company_sets_company_from_live_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=OG_TITLE_HTML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    posting = _make_posting()

    backfill_company(client, posting)

    assert posting.company == "Zvook"


def test_backfill_company_skips_if_already_set():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not fetch when company is already set")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    posting = _make_posting(company="Already Known")

    backfill_company(client, posting)

    assert posting.company == "Already Known"


def test_backfill_company_leaves_none_on_http_error(monkeypatch):
    def fake_fetch(client, url):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("job_agent.sources.djinni.fetch_djinni_posting_page", fake_fetch)
    posting = _make_posting()

    backfill_company(object(), posting)

    assert posting.company is None


def test_collect_djinni_skips_backfill_for_already_stored_urls(monkeypatch, tmp_path):
    """The RSS feed always returns its full current listing (no "since"
    param), so most postings on any given run were already collected on a
    prior run -- collect_djinni must not re-fetch their page just because
    they showed up in the feed again."""
    raw = FIXTURE.read_text(encoding="utf-8")
    already_known_url = "https://djinni.co/jobs/508398-bdd-quality-lead-quality-architecture-lead/"

    conn = db.connect(tmp_path / "jobs.db")
    db.init_db(conn)
    known = Posting(
        source="djinni",
        url=already_known_url,
        title="BDD Quality Lead",
        description="desc",
        canonical_url=canonicalize_url(already_known_url),
        dedup_hash="already-known-hash",
    )
    db.insert_posting(conn, known)

    fetched_pages: list[str] = []

    def fake_fetch_djinni(client, categories):
        return raw

    def fake_fetch_page(client, url):
        fetched_pages.append(url)
        return OG_TITLE_HTML

    monkeypatch.setattr(djinni, "fetch_djinni", fake_fetch_djinni)
    monkeypatch.setattr(djinni, "fetch_djinni_posting_page", fake_fetch_page)
    monkeypatch.setattr(djinni.time, "sleep", lambda _seconds: None)

    from job_agent.config import DjinniSourceConfig

    config = DjinniSourceConfig(enabled=True, categories=["QA Automation"])
    postings = djinni.collect_djinni(config, client=object(), conn=conn)

    assert len(postings) == 5  # every RSS item is still returned
    assert already_known_url not in fetched_pages  # but its page wasn't re-fetched
    assert len(fetched_pages) == 4  # the 4 genuinely-new postings were
