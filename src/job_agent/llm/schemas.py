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

Note: seniority_label duplicates seniority_level (both are "the posting's
own stated level, as written") -- added because it was requested by name
in a later data-collection pass; kept as a separate field rather than
silently dropped, but the two should probably be consolidated later.

verdict is NOT part of this schema -- it used to be model output, but a
real run showed the same fit_score getting different verdicts across
postings (the model's own apply/maybe/skip judgment was inconsistent).
It's now derived deterministically in code from fit_score using
config.yaml's per-mode thresholds; see scoring.derive_verdict().
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["apply", "maybe", "skip"]
SeniorityMatch = Literal["under", "match", "over"]
RelocationOffered = Literal["yes", "no", "unknown"]
TechTagPriority = Literal["required", "nice_to_have"]
PortfolioExpected = Literal["github", "portfolio", "test_task", "none"]


class TechTag(BaseModel):
    tag: str
    priority: TechTagPriority


class ScoringResult(BaseModel):
    fit_score: int = Field(ge=0, le=100)
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

    # Second data-collection pass.
    tech_tags: list[TechTag] = Field(default_factory=list)
    portfolio_expected: PortfolioExpected | None = None
    company_type: str | None = None  # e.g. "product" / "outsource" / "agency" / "startup"
    seniority_label: str | None = None  # see module docstring re: overlap with seniority_level


_TECH_TAG_SCHEMA = {
    "type": "object",
    "properties": {
        "tag": {"type": "string"},
        "priority": {"type": "string", "enum": ["required", "nice_to_have"]},
    },
    "required": ["tag", "priority"],
    "additionalProperties": False,
}

SCORING_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
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
        "tech_tags": {"type": "array", "items": _TECH_TAG_SCHEMA},
        "portfolio_expected": {
            "type": ["string", "null"],
            "enum": ["github", "portfolio", "test_task", "none", None],
        },
        "company_type": {"type": ["string", "null"]},
        "seniority_label": {"type": ["string", "null"]},
    },
    "required": [
        "fit_score",
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
        "tech_tags",
        "portfolio_expected",
        "company_type",
        "seniority_label",
    ],
    "additionalProperties": False,
}
