import json

import feedparser

from job_agent.sources.base import entry_raw_payload, parse_title_company_uk

_MINIMAL_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
<title>Company: Job Title</title>
<link>https://example.com/job/1</link>
<description>&lt;p&gt;Some description&lt;/p&gt;</description>
<pubDate>Thu, 03 Sep 2026 01:35:18 +0000</pubDate>
<guid>https://example.com/job/1</guid>
</item>
</channel></rss>"""


def _parse_entry():
    return feedparser.parse(_MINIMAL_RSS).entries[0]


def test_entry_raw_payload_drops_redundant_detail_and_links_keys():
    entry = _parse_entry()
    assert "title_detail" in entry  # sanity: feedparser really does populate these
    assert "summary_detail" in entry
    assert "links" in entry

    payload = json.loads(entry_raw_payload(entry))

    assert "title_detail" not in payload
    assert "summary_detail" not in payload
    assert "links" not in payload


def test_entry_raw_payload_keeps_title_summary_link_and_other_fields():
    entry = _parse_entry()

    payload = json.loads(entry_raw_payload(entry))

    assert payload["title"] == "Company: Job Title"
    assert payload["summary"] == "<p>Some description</p>"
    assert payload["link"] == "https://example.com/job/1"
    assert payload["id"] == "https://example.com/job/1"


def test_parse_title_company_uk_splits_on_the_marker():
    title, company = parse_title_company_uk("Data Scientist в Fit4Me ")
    assert title == "Data Scientist"
    assert company == "Fit4Me"


def test_parse_title_company_uk_returns_none_company_when_no_marker():
    title, company = parse_title_company_uk("Just A Title")
    assert title == "Just A Title"
    assert company is None
