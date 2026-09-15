from pathlib import Path

from job_agent.sources.remoteok import parse_remoteok_json

FIXTURE = Path(__file__).parent / "fixtures" / "remoteok_sample.json"


def test_parse_remoteok_json_skips_legal_notice():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_remoteok_json(raw, tags=[])
    assert len(postings) == 5  # 6 array entries minus the legal notice
    assert all(p.source == "remoteok" for p in postings)
    assert all(p.remote_type == "remote" for p in postings)


def test_parse_remoteok_json_filters_by_tag_client_side():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_remoteok_json(raw, tags=["golang"])
    assert len(postings) == 1
    assert postings[0].title == "Software Engineer"


def test_parse_remoteok_json_no_tags_means_no_filtering():
    raw = FIXTURE.read_text(encoding="utf-8")
    postings = parse_remoteok_json(raw, tags=[])
    titles = {p.title for p in postings}
    assert "Junior Payroll Assistant" in titles
