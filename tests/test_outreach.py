from urllib.parse import parse_qs, urlparse

import pytest

from job_agent.outreach import DEFAULT_ROLE_KEYWORDS, build_people_search_url


def test_build_people_search_url_base_path():
    url = build_people_search_url("Acme Corp")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.linkedin.com"
    assert parsed.path == "/search/results/people/"


def test_build_people_search_url_includes_company_and_default_roles():
    url = build_people_search_url("Acme Corp")
    keywords = parse_qs(urlparse(url).query)["keywords"][0]
    assert keywords.startswith("Acme Corp ")
    for role in DEFAULT_ROLE_KEYWORDS:
        assert role in keywords
    assert " OR " in keywords


def test_build_people_search_url_custom_roles():
    url = build_people_search_url("Acme Corp", role_keywords=["engineering manager"])
    keywords = parse_qs(urlparse(url).query)["keywords"][0]
    assert keywords == "Acme Corp engineering manager"


def test_build_people_search_url_empty_roles_means_company_only():
    url = build_people_search_url("Acme Corp", role_keywords=[])
    keywords = parse_qs(urlparse(url).query)["keywords"][0]
    assert keywords == "Acme Corp"


def test_build_people_search_url_strips_whitespace():
    url = build_people_search_url("  Acme Corp  ", role_keywords=[])
    keywords = parse_qs(urlparse(url).query)["keywords"][0]
    assert keywords == "Acme Corp"


def test_build_people_search_url_rejects_empty_company():
    with pytest.raises(ValueError):
        build_people_search_url("")
    with pytest.raises(ValueError):
        build_people_search_url("   ")


def test_build_people_search_url_encodes_special_characters():
    url = build_people_search_url("R&D Corp", role_keywords=[])
    assert "%26" in url  # '&' is percent-encoded, not left as a literal query separator
    keywords = parse_qs(urlparse(url).query)["keywords"][0]
    assert keywords == "R&D Corp"
