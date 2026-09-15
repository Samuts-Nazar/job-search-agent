from job_agent.db import Posting
from job_agent.llm.prompts import render_scoring_prompt


def make_posting(**overrides) -> Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/1/",
        title="QA Automation Engineer",
        description="Looking for a QA automation engineer with Python skills.",
        company="Acme",
        location="Kyiv",
        remote_type="remote",
        salary_raw="$2000-3000",
    )
    defaults.update(overrides)
    return Posting(**defaults)


def test_render_scoring_prompt_substitutes_all_fields():
    prompt = render_scoring_prompt(
        cv_track="qa_automation",
        facts_summary="Candidate knows Python, Playwright, pytest.",
        posting=make_posting(),
    )
    assert "qa_automation" in prompt
    assert "Candidate knows Python, Playwright, pytest." in prompt
    assert "QA Automation Engineer" in prompt
    assert "Acme" in prompt
    assert "Kyiv" in prompt
    assert "$2000-3000" in prompt
    assert "Looking for a QA automation engineer" in prompt


def test_render_scoring_prompt_handles_missing_optional_fields():
    posting = make_posting(company=None, location=None, remote_type=None, salary_raw=None)
    prompt = render_scoring_prompt(
        cv_track="general", facts_summary="Facts.", posting=posting
    )
    assert "unknown" in prompt
    assert "not stated" in prompt
