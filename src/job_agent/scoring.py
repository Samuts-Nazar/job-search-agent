"""Scoring pipeline: `bulk`-tier LLM call, validation, retry/fallback.

See PROJECT.md §5.4. On failure: retry (max 2 per model), then the next
fallback model, then mark the posting `score_failed`.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
from pydantic import ValidationError

from job_agent import db
from job_agent.config import Config
from job_agent.db import Posting
from job_agent.llm.client import (
    CompletionResult,
    OpenRouterError,
    build_structured_payload,
    complete_structured,
    parse_json_content,
)
from job_agent.llm.prompts import render_scoring_prompt
from job_agent.llm.schemas import SCORING_JSON_SCHEMA, ScoringResult

MAX_RETRIES_PER_MODEL = 2
SYSTEM_PROMPT = "You are a precise, conservative job-fit scorer."


def _attempt_score(
    client: httpx.Client, *, model: str, cv_track: str, facts_summary: str, posting: Posting
) -> tuple[ScoringResult, CompletionResult]:
    user_prompt = render_scoring_prompt(
        cv_track=cv_track, facts_summary=facts_summary, posting=posting
    )
    payload = build_structured_payload(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        json_schema=SCORING_JSON_SCHEMA,
        schema_name="posting_score",
    )
    completion = complete_structured(client, payload)
    data = parse_json_content(completion.raw_content)
    return ScoringResult.model_validate(data), completion


def score_posting(
    conn: sqlite3.Connection,
    client: httpx.Client,
    *,
    posting_id: int,
    posting: Posting,
    config: Config,
    cv_track: str,
    facts_summary: str,
) -> ScoringResult | None:
    models_to_try = [config.models.bulk, *config.models.fallbacks.bulk]
    last_error: Exception | None = None

    for model in models_to_try:
        for _attempt in range(MAX_RETRIES_PER_MODEL + 1):
            try:
                scoring_result, completion = _attempt_score(
                    client,
                    model=model,
                    cv_track=cv_track,
                    facts_summary=facts_summary,
                    posting=posting,
                )
            except (OpenRouterError, ValidationError, httpx.HTTPError) as exc:
                last_error = exc
                continue

            db.record_llm_call(
                conn,
                posting_id=posting_id,
                tier="bulk",
                model=completion.model,
                purpose="score",
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=completion.cost_usd,
            )
            db.update_posting_status(
                conn,
                posting_id,
                "scored",
                fit_score=scoring_result.fit_score,
                verdict=scoring_result.verdict,
                matched_skills=json.dumps(scoring_result.matched_skills),
                missing_skills=json.dumps(scoring_result.missing_skills),
                seniority_match=scoring_result.seniority_match,
                required_languages=json.dumps(scoring_result.required_languages),
                language_mismatch=int(scoring_result.language_mismatch),
                relocation_offered=scoring_result.relocation_offered,
                recommended_cv_track=scoring_result.recommended_cv_track,
                rationale=scoring_result.rationale,
            )
            return scoring_result

    db.update_posting_status(conn, posting_id, "score_failed", score_failed_reason=str(last_error))
    return None
