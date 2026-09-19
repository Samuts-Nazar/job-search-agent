"""Structured-output schema for LLM scoring. See PROJECT.md §5.4.

SCORING_JSON_SCHEMA is the raw JSON Schema sent to OpenRouter's
`response_format: {type: "json_schema", ...}` (strict mode requires every
property listed in "required" and `additionalProperties: false` -- in
strict mode there is no true "optional/missing" property, so every field
the model can't determine must be nullable (`type: [X, "null"]`) rather
than absent).

The fields below fit_score..rationale are the original Phase 1 scoring
output. Everything from tech_stack onward is posting metadata the model
extracts as a side effect of reading the full posting text -- purely
additive data collection (PROJECT.md-adjacent, not used to change pipeline
behavior yet): every one of these is nullable/defaults to empty so a model
that can't determine a value never fails validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["apply", "maybe", "skip"]
SeniorityMatch = Literal["under", "match", "over"]
RelocationOffered = Literal["yes", "no", "unknown"]


class ScoringResult(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    verdict: Verdict
    matched_skills: list[str]
    missing_skills: list[str]
    seniority_match: SeniorityMatch
    required_languages: list[str]
    language_mismatch: bool
    relocation_offered: RelocationOffered
    remote_type: str
    salary: str | None
    recommended_cv_track: str
    rationale: str

    # Additional posting metadata extracted for later analysis (job stats,
    # job export) -- all optional, never gate scoring validity.
    tech_stack: list[str] = Field(default_factory=list)
    required_years_experience: float | None = None
    seniority_level: str | None = None  # e.g. "junior" / "middle" / "senior" / "lead"
    work_format: str | None = None  # e.g. "remote" / "hybrid" / "onsite"
    country: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # "hour" / "day" / "month" / "year"


SCORING_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["apply", "maybe", "skip"]},
        "matched_skills": {"type": "array", "items": {"type": "string"}},
        "missing_skills": {"type": "array", "items": {"type": "string"}},
        "seniority_match": {"type": "string", "enum": ["under", "match", "over"]},
        "required_languages": {"type": "array", "items": {"type": "string"}},
        "language_mismatch": {"type": "boolean"},
        "relocation_offered": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "remote_type": {"type": "string"},
        "salary": {"type": ["string", "null"]},
        "recommended_cv_track": {"type": "string"},
        "rationale": {"type": "string"},
        "tech_stack": {"type": "array", "items": {"type": "string"}},
        "required_years_experience": {"type": ["number", "null"]},
        "seniority_level": {"type": ["string", "null"]},
        "work_format": {"type": ["string", "null"]},
        "country": {"type": ["string", "null"]},
        "salary_min": {"type": ["number", "null"]},
        "salary_max": {"type": ["number", "null"]},
        "salary_currency": {"type": ["string", "null"]},
        "salary_period": {"type": ["string", "null"]},
    },
    "required": [
        "fit_score",
        "verdict",
        "matched_skills",
        "missing_skills",
        "seniority_match",
        "required_languages",
        "language_mismatch",
        "relocation_offered",
        "remote_type",
        "salary",
        "recommended_cv_track",
        "rationale",
        "tech_stack",
        "required_years_experience",
        "seniority_level",
        "work_format",
        "country",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    ],
    "additionalProperties": False,
}
