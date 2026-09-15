from pathlib import Path

from job_agent.sources.djinni import parse_djinni_rss

FIXTURE = Path(__file__).parent / "fixtures" / "djinni_rss_qa_automation.xml"


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
