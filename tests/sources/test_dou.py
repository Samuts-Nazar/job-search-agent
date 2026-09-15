from pathlib import Path

from job_agent.sources.dou import _parse_title, parse_dou_rss

FIXTURE = Path(__file__).parent / "fixtures" / "dou_rss_qa.xml"


def test_parse_title_extracts_company_salary_location():
    title, company, salary, location, is_remote = _parse_title(
        "Manual QA Engineer в Solidium Technology, $700–1000, віддалено"
    )
    assert title == "Manual QA Engineer"
    assert company == "Solidium Technology"
    assert salary == "$700–1000"
    assert location is None
    assert is_remote is True


def test_parse_title_with_location_only():
    title, company, salary, location, is_remote = _parse_title(
        "QA Manual Engineer (iGaming) в Uptowin, $1000–1500, Київ"
    )
    assert title == "QA Manual Engineer (iGaming)"
    assert company == "Uptowin"
    assert salary == "$1000–1500"
    assert location == "Київ"
    assert is_remote is False


def test_parse_title_without_extra_info():
    title, company, salary, location, is_remote = _parse_title("Some vague title")
    assert title == "Some vague title"
    assert company is None
    assert salary is None
    assert location is None
    assert is_remote is False


def test_parse_dou_rss_returns_normalized_postings():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_dou_rss(raw, category="QA")

    assert len(postings) == 5
    first = postings[0]
    assert first.source == "dou"
    assert first.company == "Solidium Technology"
    assert first.salary_raw == "$700–1000"
    assert first.remote_type == "remote"
    assert first.raw_tags == ["QA"]
    assert first.description
