import pytest
from pydantic import ValidationError

from job_agent.llm.schemas import ScoringResult, TechTag

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


def test_scoring_result_new_fields_default_when_omitted():
    result = ScoringResult.model_validate(VALID)
    assert result.tech_stack == []
    assert result.required_years_experience is None
    assert result.seniority_level is None
    assert result.work_format is None
    assert result.country is None
    assert result.salary_min is None
    assert result.salary_max is None
    assert result.salary_currency is None
    assert result.salary_period is None


def test_scoring_result_accepts_new_fields_when_provided():
    result = ScoringResult.model_validate(
        {
            **VALID,
            "tech_stack": ["Python", "AWS"],
            "required_years_experience": 3.5,
            "seniority_level": "middle",
            "work_format": "remote",
            "country": "Ukraine",
            "salary_min": 2000,
            "salary_max": 3000,
            "salary_currency": "USD",
            "salary_period": "month",
        }
    )
    assert result.tech_stack == ["Python", "AWS"]
    assert result.required_years_experience == 3.5
    assert result.seniority_level == "middle"
    assert result.work_format == "remote"
    assert result.country == "Ukraine"
    assert result.salary_min == 2000
    assert result.salary_max == 3000
    assert result.salary_currency == "USD"
    assert result.salary_period == "month"


def test_scoring_json_schema_lists_every_field_as_required_and_nullable():
    from job_agent.llm.schemas import SCORING_JSON_SCHEMA

    for field in (
        "tech_stack",
        "required_years_experience",
        "seniority_level",
        "work_format",
        "country",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "tech_tags",
        "portfolio_expected",
        "company_type",
        "seniority_label",
    ):
        assert field in SCORING_JSON_SCHEMA["required"]
        assert field in SCORING_JSON_SCHEMA["properties"]


def test_scoring_result_second_pass_fields_default_when_omitted():
    result = ScoringResult.model_validate(VALID)
    assert result.tech_tags == []
    assert result.portfolio_expected is None
    assert result.company_type is None
    assert result.seniority_label is None


def test_scoring_result_accepts_tech_tags():
    result = ScoringResult.model_validate(
        {
            **VALID,
            "tech_tags": [
                {"tag": "Python", "priority": "required"},
                {"tag": "Docker", "priority": "nice_to_have"},
            ],
        }
    )
    assert result.tech_tags == [
        TechTag(tag="Python", priority="required"),
        TechTag(tag="Docker", priority="nice_to_have"),
    ]


def test_scoring_result_rejects_invalid_tech_tag_priority():
    with pytest.raises(ValidationError):
        ScoringResult.model_validate(
            {**VALID, "tech_tags": [{"tag": "Python", "priority": "sometimes"}]}
        )


def test_scoring_result_accepts_portfolio_expected_values():
    for value in ("github", "portfolio", "test_task", "none"):
        result = ScoringResult.model_validate({**VALID, "portfolio_expected": value})
        assert result.portfolio_expected == value


def test_scoring_result_rejects_unknown_portfolio_expected():
    with pytest.raises(ValidationError):
        ScoringResult.model_validate({**VALID, "portfolio_expected": "resume"})


def test_scoring_result_accepts_company_type_and_seniority_label():
    result = ScoringResult.model_validate(
        {**VALID, "company_type": "outsource", "seniority_label": "Middle"}
    )
    assert result.company_type == "outsource"
    assert result.seniority_label == "Middle"
