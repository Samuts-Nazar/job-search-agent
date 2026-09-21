from job_agent.sources.base import parse_title_company_uk


def test_parse_title_company_uk_splits_on_the_marker():
    title, company = parse_title_company_uk("Data Scientist в Fit4Me ")
    assert title == "Data Scientist"
    assert company == "Fit4Me"


def test_parse_title_company_uk_returns_none_company_when_no_marker():
    title, company = parse_title_company_uk("Just A Title")
    assert title == "Just A Title"
    assert company is None
