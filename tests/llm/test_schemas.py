import pytest
from pydantic import ValidationError

from job_agent.llm.schemas import ScoringResult

VALID = {
    "fit_score": 75,
    "verdict": "apply",
    "matched_skills": ["Python", "pytest"],
    "missing_skills": ["Kubernetes"],
    "seniority_match": "match",
    "required_languages": ["English"],
    "language_mismatch": False,
    "relocation_offered": "unknown",
    "remote_type": "remote",
    "salary": "$2000-3000",
    "recommended_cv_track": "qa_automation",
    "rationale": "Good match for automation skills.",
}


def test_scoring_result_accepts_valid_payload():
    result = ScoringResult.model_validate(VALID)
    assert result.fit_score == 75
    assert result.verdict == "apply"


def test_scoring_result_rejects_out_of_range_fit_score():
    with pytest.raises(ValidationError):
        ScoringResult.model_validate({**VALID, "fit_score": 150})


def test_scoring_result_rejects_unknown_verdict():
    with pytest.raises(ValidationError):
        ScoringResult.model_validate({**VALID, "verdict": "maybe-ish"})


def test_scoring_result_allows_null_salary():
    result = ScoringResult.model_validate({**VALID, "salary": None})
    assert result.salary is None
