"""Model evaluation harness for `job eval`. See PROJECT.md §7.

Phase 1 only evaluates the `bulk`-tier scoring call -- cover-letter
evaluation needs letters.py, which is Phase 2 work (§11.2). The real
20-posting test set lives at data/eval_postings.json (gitignored, not
committed); a small synthetic sample ships as a committed fixture so the
harness itself is exercised in tests without real scraped data.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from job_agent.config import Config, ModelEntry, Secrets
from job_agent.db import Posting
from job_agent.llm.client import (
    OpenRouterError,
    build_structured_payload,
    complete_structured,
    make_client,
    parse_json_content,
)
from job_agent.llm.prompts import render_scoring_prompt
from job_agent.llm.schemas import SCORING_JSON_SCHEMA, ScoringResult

DEFAULT_EVAL_POSTINGS_PATH = "data/eval_postings.json"
SAMPLE_EVAL_POSTINGS_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "eval" / "fixtures" / "sample_postings.json"
)
MAX_RETRIES = 2
SYSTEM_PROMPT = "You are a precise, conservative job-fit scorer."

# PROJECT.md §6: ":free" variants are capped at 20 requests/minute,
# account-wide. Self-throttle to stay under that regardless of how fast the
# endpoint actually responds. Note (verified live 2026-09-19): this does
# NOT protect against an upstream provider's own shared-pool congestion --
# google/gemma-4-26b-a4b-it:free 429'd on the very first (already-throttled)
# request with `limit_source: upstream_provider_shared_pool`, unrelated to
# our own request pace.
FREE_TIER_MIN_INTERVAL_SECONDS = 3.1


@dataclass
class ScoredExample:
    posting: Posting
    result: ScoringResult


@dataclass
class ModelEvalReport:
    model: str
    total: int = 0
    valid: int = 0
    total_retries: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_output_tokens: int = 0
    reasoning_fallback_used: bool = False
    errors: list[str] = field(default_factory=list)
    examples: list[ScoredExample] = field(default_factory=list)
    sample_request_payload: dict[str, Any] | None = None
    sample_output_tokens: int | None = None

    @property
    def validity_rate(self) -> float:
        return self.valid / self.total if self.total else 0.0

    @property
    def avg_retries(self) -> float:
        return self.total_retries / self.total if self.total else 0.0

    @property
    def median_latency_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max_latency_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0


def load_eval_postings(path: Path | str | None = None) -> list[Posting]:
    candidate = Path(path) if path else Path(DEFAULT_EVAL_POSTINGS_PATH)
    if not candidate.exists():
        candidate = SAMPLE_EVAL_POSTINGS_PATH
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    return [Posting(**item) for item in raw]


EvalAttempt = tuple[
    bool,
    int,
    float,
    float | None,
    int | None,
    ScoringResult | None,
    dict[str, Any],
    bool,
    str | None,
]


def evaluate_model_on_posting(
    client: httpx.Client,
    *,
    model_entry: ModelEntry,
    facts_summary: str,
    posting: Posting,
    reasoning_overrides: dict[str, str | None],
) -> EvalAttempt:
    """Returns (valid, retries_used, latency_ms, cost_usd, output_tokens,
    result, payload, reasoning_fallback_used, error)."""
    reasoning = reasoning_overrides.get(model_entry.id, model_entry.reasoning_effort)
    user_prompt = render_scoring_prompt(facts_summary=facts_summary, posting=posting)
    payload = build_structured_payload(
        model=model_entry.id,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        json_schema=SCORING_JSON_SCHEMA,
        schema_name="posting_score",
        reasoning=reasoning,
    )

    last_error: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            completion = complete_structured(client, payload)
            data = parse_json_content(completion.raw_content)
            result = ScoringResult.model_validate(data)
        except (OpenRouterError, ValidationError, httpx.HTTPError) as exc:
            last_error = str(exc)
            continue
        latency_ms = (time.monotonic() - start) * 1000
        if completion.reasoning_fallback_used and model_entry.id not in reasoning_overrides:
            reasoning_overrides[model_entry.id] = None
        return (
            True,
            attempt,
            latency_ms,
            completion.cost_usd,
            completion.output_tokens,
            result,
            payload,
            completion.reasoning_fallback_used,
            None,
        )

    return False, MAX_RETRIES, 0.0, None, None, None, payload, False, last_error


def evaluate_model(
    client: httpx.Client,
    *,
    model_entry: ModelEntry,
    facts_summary: str,
    postings: list[Posting],
    max_examples: int = 5,
) -> ModelEvalReport:
    report = ModelEvalReport(model=model_entry.id)
    is_free = model_entry.id.endswith(":free")
    last_call_start: float | None = None
    reasoning_overrides: dict[str, str | None] = {}

    for posting in postings:
        if is_free and last_call_start is not None:
            elapsed = time.monotonic() - last_call_start
            if elapsed < FREE_TIER_MIN_INTERVAL_SECONDS:
                time.sleep(FREE_TIER_MIN_INTERVAL_SECONDS - elapsed)
        last_call_start = time.monotonic()

        (
            valid,
            retries,
            latency_ms,
            cost_usd,
            output_tokens,
            result,
            payload,
            reasoning_fallback_used,
            error,
        ) = evaluate_model_on_posting(
            client,
            model_entry=model_entry,
            facts_summary=facts_summary,
            posting=posting,
            reasoning_overrides=reasoning_overrides,
        )
        report.total += 1
        report.total_retries += retries
        report.reasoning_fallback_used = report.reasoning_fallback_used or reasoning_fallback_used
        if report.sample_request_payload is None:
            report.sample_request_payload = payload
        if valid:
            report.valid += 1
            report.latencies_ms.append(latency_ms)
            report.total_cost_usd += cost_usd or 0.0
            report.total_output_tokens += output_tokens or 0
            if report.sample_output_tokens is None:
                report.sample_output_tokens = output_tokens
            if len(report.examples) < max_examples and result is not None:
                report.examples.append(ScoredExample(posting=posting, result=result))
        else:
            report.errors.append(f"{posting.url}: {error}")

    return report


def run_eval(
    config: Config,
    secrets: Secrets,
    *,
    facts_summary: str,
    postings_path: Path | str | None = None,
) -> dict[str, ModelEvalReport]:
    postings = load_eval_postings(postings_path)
    reports: dict[str, ModelEvalReport] = {}

    with make_client(secrets.openrouter_api_key) as client:
        for model_entry in config.models.eval_candidates:
            reports[model_entry.id] = evaluate_model(
                client, model_entry=model_entry, facts_summary=facts_summary, postings=postings
            )

    return reports


def format_eval_report(reports: dict[str, ModelEvalReport]) -> str:
    lines = ["Model evaluation (scoring only -- see PROJECT.md §7)", ""]
    for model, report in reports.items():
        lines.append(f"{model}:")
        lines.append(f"  validity rate: {report.validity_rate:.0%} ({report.valid}/{report.total})")
        lines.append(f"  avg retries:   {report.avg_retries:.2f}")
        lines.append(f"  median latency:{report.median_latency_ms:.0f} ms")
        lines.append(f"  max latency:   {report.max_latency_ms:.0f} ms")
        lines.append(f"  total cost:    ${report.total_cost_usd:.4f}")
        lines.append(f"  output tokens: {report.total_output_tokens}")
        if report.reasoning_fallback_used:
            lines.append("  note: this model rejected an explicit reasoning effort at least")
            lines.append("        once; reasoning was omitted from the request on retry/later")
            lines.append("        calls")
        for error in report.errors:
            lines.append(f"  error: {error}")
        lines.append("")
    return "\n".join(lines)
