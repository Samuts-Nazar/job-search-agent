import json
from pathlib import Path

from job_agent.config import WwrSourceConfig
from job_agent.sources import wwr
from job_agent.sources.wwr import _split_title, matches_keywords, parse_wwr_rss

FIXTURE = Path(__file__).parent / "fixtures" / "wwr_devops_sysadmin.xml"


def test_split_title_splits_company_and_job_title():
    title, company = _split_title("Keeper Security: Bilingual Channel Account Manager")
    assert company == "Keeper Security"
    assert title == "Bilingual Channel Account Manager"


def test_split_title_without_colon():
    title, company = _split_title("Just A Title")
    assert title == "Just A Title"
    assert company is None


def test_parse_wwr_rss_returns_normalized_postings():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_wwr_rss(raw, category_slug="remote-devops-sysadmin-jobs")

    assert len(postings) >= 1
    first = postings[0]
    assert first.source == "wwr"
    assert first.company == "Keeper Security"
    assert first.remote_type == "remote"
    assert first.raw_tags == ["DevOps and Sysadmin"]
    assert first.published_at is not None
    assert first.raw_payload is not None
    payload = json.loads(first.raw_payload)
    assert payload["link"] == first.url


def test_matches_keywords_whole_word():
    assert matches_keywords("Senior Python Engineer", ["python"])
    assert not matches_keywords("Senior Pythonista Engineer", ["python"])
    assert not matches_keywords("Senior JS Engineer", ["python"])


def test_collect_wwr_keyword_categories_filters_client_side(monkeypatch):
    raw = FIXTURE.read_text(encoding="utf-8")

    def fake_fetch_wwr(client, slug):
        return raw

    monkeypatch.setattr(wwr, "fetch_wwr", fake_fetch_wwr)
    config = WwrSourceConfig(
        enabled=True,
        categories=[],
        keyword_categories={"remote-back-end-programming-jobs": ["python"]},
    )

    postings = wwr.collect_wwr(config, client=object())

    # the fixture's only item is a "Channel Account Manager" role -- no match
    assert postings == []


def test_collect_wwr_disabled_without_categories_or_keywords():
    config = WwrSourceConfig(enabled=True, categories=[], keyword_categories={})
    assert wwr.collect_wwr(config) == []
