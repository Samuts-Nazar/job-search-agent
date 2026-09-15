from pathlib import Path

from job_agent.sources.wwr import _split_title, parse_wwr_rss

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
