import pytest

from job_agent.ats_vendor import detect_ats_vendor


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://jobs.lever.co/acme/abcd", "lever"),
        ("https://apply.workable.com/acme/j/ABCDEF", "workable"),
        ("https://acme.teamtailor.com/jobs/123", "teamtailor"),
        ("https://acme.bamboohr.com/careers/123", "bamboohr"),
        ("https://careers.smartrecruiters.com/Acme/123", "smartrecruiters"),
        ("https://jobs.ashbyhq.com/acme/abc", "ashby"),
        ("https://acme.recruitee.com/o/role", "recruitee"),
        ("https://acme.myworkdayjobs.com/en-US/Acme/job/123", "workday"),
    ],
)
def test_detect_ats_vendor_matches_known_domains(url, expected):
    assert detect_ats_vendor(url) == expected


def test_detect_ats_vendor_unknown_domain_returns_none():
    assert detect_ats_vendor("https://djinni.co/jobs/123-qa/") is None


def test_detect_ats_vendor_none_or_empty_url():
    assert detect_ats_vendor(None) is None
    assert detect_ats_vendor("") is None


def test_detect_ats_vendor_does_not_false_positive_on_substring():
    # "notgreenhouse.io" contains "greenhouse.io" as a substring but is a
    # different domain entirely -- must not match.
    assert detect_ats_vendor("https://notgreenhouse.io/jobs/1") is None
