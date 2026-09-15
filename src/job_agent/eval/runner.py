"""Model evaluation harness for `job eval`. See PROJECT.md §7.

Phase 1 only evaluates the `bulk`-tier scoring call -- cover-letter
evaluation needs letters.py, which is Phase 2 work (§11.2). The real
20-posting test set lives at data/eval_postings.json (gitignored, not
populated yet); a small synthetic sample ships as a committed fixture so the
harness itself is exercised in tests without real scraped data.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from pydantic import ValidationError

from job_agent.config import Config, Secrets
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


@dataclass
class ModelEvalReport:
    model: str
    total: int = 0
    valid: int = 0
    total_retries: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def validity_rate(self) -> float:
        return self.valid / self.total if self.total else 0.0

    @property
    def avg_retries(self) -> float:
        return self.total_retries / self.total if self.total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total else 0.0


def load_eval_postings(path: Path | str | None = None) -> list[Posting]:
    candidate = Path(path) if path else Path(DEFAULT_EVAL_POSTINGS_PATH)
    if not candidate.exists():
        candidate = SAMPLE_EVAL_POSTINGS_PATH
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    return [Posting(**item) for item in raw]


def evaluate_model_on_posting(
    client: httpx.Client, *, model: str, facts_summary: str, posting: Posting
) -> tuple[bool, int, float, float | None, str | None]:
    """Returns (valid, retries_used, latency_ms, cost_usd, error)."""
    user_prompt = render_scoring_prompt(facts_summary=facts_summary, posting=posting)
    payload = build_structured_payload(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        json_schema=SCORING_JSON_SCHEMA,
        schema_name="posting_score",
    )

    last_error: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            completion = complete_structured(client, payload)
            data = parse_json_content(completion.raw_content)
            ScoringResult.model_validate(data)
        except (OpenRouterError, ValidationError, httpx.HTTPError) as exc:
            last_error = str(exc)
            continue
        latency_ms = (time.monotonic() - start) * 1000
        return True, attempt, latency_ms, completion.cost_usd, None

    return False, MAX_RETRIES, 0.0, None, last_error


def evaluate_model(
    client: httpx.Client, *, model: str, facts_summary: str, postings: list[Posting]
) -> ModelEvalReport:
    report = ModelEvalReport(model=model)
    for posting in postings:
        valid, retries, latency_ms, cost_usd, error = evaluate_model_on_posting(
            client, model=model, facts_summary=facts_summary, posting=posting
        )
        report.total += 1
        report.total_retries += retries
        if valid:
            report.valid += 1
            report.total_latency_ms += latency_ms
            report.total_cost_usd += cost_usd or 0.0
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
        for model in config.models.eval_candidates:
            reports[model] = evaluate_model(
                client, model=model, facts_summary=facts_summary, postings=postings
            )

    return reports


def format_eval_report(reports: dict[str, ModelEvalReport]) -> str:
    lines = ["Model evaluation (scoring only -- see PROJECT.md §7)", ""]
    for model, report in reports.items():
        lines.append(f"{model}:")
        lines.append(f"  validity rate: {report.validity_rate:.0%} ({report.valid}/{report.total})")
        lines.append(f"  avg retries:   {report.avg_retries:.2f}")
        lines.append(f"  avg latency:   {report.avg_latency_ms:.0f} ms")
        lines.append(f"  total cost:    ${report.total_cost_usd:.4f}")
        for error in report.errors:
            lines.append(f"  error: {error}")
        lines.append("")
    return "\n".join(lines)
