"""Structured-output schema for LLM scoring. See PROJECT.md §5.4.

SCORING_JSON_SCHEMA is the raw JSON Schema sent to OpenRouter's
`response_format: {type: "json_schema", ...}` (strict mode requires every
property listed in "required" and `additionalProperties: false`).
ScoringResult is the matching Pydantic model used to validate the response.
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
    ],
    "additionalProperties": False,
}
