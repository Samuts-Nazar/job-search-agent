import datetime as dt

import pandas as pd

from job_agent.config import LinkedinSourceConfig
from job_agent.sources import linkedin

SAMPLE_ROWS = [
    {
        "job_url": "https://www.linkedin.com/jobs/view/1",
        "title": "Senior QA Automation Engineer",
        "description": "We need a QA automation engineer.",
        "company": "Cognizant",
        "location": "Tampa, FL",
        "is_remote": False,
        "date_posted": dt.date(2026, 9, 11),
        "min_amount": None,
        "max_amount": None,
        "currency": None,
    },
    {
        "job_url": "https://www.linkedin.com/jobs/view/2",
        "title": "QE Automation Engineer",
        "description": None,
        "company": "TCS",
        "location": "New York, NY",
        "is_remote": True,
        "date_posted": None,
        "min_amount": 80000,
        "max_amount": 100000,
        "currency": "USD",
    },
]


def test_row_to_posting_maps_fields():
    posting = linkedin.row_to_posting(SAMPLE_ROWS[0])
    assert posting.source == "linkedin"
    assert posting.title == "Senior QA Automation Engineer"
    assert posting.company == "Cognizant"
    assert posting.remote_type is None
    assert posting.published_at == "2026-09-11"


def test_row_to_posting_handles_missing_description_and_remote_and_salary():
    posting = linkedin.row_to_posting(SAMPLE_ROWS[1])
    assert posting.description == ""
    assert posting.remote_type == "remote"
    assert posting.salary_raw == "USD 80000-100000"
    assert posting.published_at is None


def test_row_to_posting_handles_pandas_nan_for_missing_cells():
    # Real jobspy DataFrames represent some missing cells as float NaN rather
    # than None/NaT (observed live: `date_posted` came back as `float('nan')`
    # for a row with no other date-bearing rows in the same batch, and
    # `.isoformat()` on that crashed). `float('nan')` is truthy in Python, so
    # a plain `if value:` check does not catch it.
    row = {
        "job_url": "u2",
        "title": "B",
        "date_posted": float("nan"),
        "min_amount": float("nan"),
        "max_amount": float("nan"),
        "currency": float("nan"),
        "company": float("nan"),
    }

    posting = linkedin.row_to_posting(row)
    assert posting.published_at is None
    assert posting.salary_raw is None
    assert posting.company is None


def test_collect_linkedin_for_keyword_uses_scrape_jobs(monkeypatch):
    captured = {}

    def fake_scrape_jobs(**kwargs):
        captured.update(kwargs)
        return pd.DataFrame(SAMPLE_ROWS)

    monkeypatch.setattr(linkedin, "scrape_jobs", fake_scrape_jobs)

    postings = linkedin.collect_linkedin_for_keyword("QA Automation", results_wanted=10)

    assert captured["site_name"] == "linkedin"
    assert captured["search_term"] == "QA Automation"
    assert captured["linkedin_fetch_description"] is True
    assert len(postings) == 2


def test_collect_linkedin_splits_budget_and_skips_disabled(monkeypatch):
    monkeypatch.setattr(linkedin.time, "sleep", lambda _seconds: None)
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs["search_term"])
        return pd.DataFrame(SAMPLE_ROWS[:1])

    monkeypatch.setattr(linkedin, "scrape_jobs", fake_scrape_jobs)

    config = LinkedinSourceConfig(enabled=True, keywords=["QA", "Python"], results_wanted=40)
    postings = linkedin.collect_linkedin(config)

    assert calls == ["QA", "Python"]
    assert len(postings) == 2

    disabled = LinkedinSourceConfig(enabled=False, keywords=["QA"])
    assert linkedin.collect_linkedin(disabled) == []
