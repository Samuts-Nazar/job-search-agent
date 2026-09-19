import json

import httpx

from job_agent.config import ModelEntry
from job_agent.eval import runner


def test_load_eval_postings_falls_back_to_sample_fixture(tmp_path):
    postings = runner.load_eval_postings(tmp_path / "does-not-exist.json")
    assert len(postings) == 3
    assert postings[0].source == "sample"
    assert postings[0].title == "QA Automation Engineer"


def _make_client(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://openrouter.ai/api/v1")


VALID_CONTENT = (
    '{"fit_score": 80, "verdict": "apply", "matched_skills": ["Python"], '
    '"missing_skills": [], "seniority_match": "match", "required_languages": ["English"], '
    '"language_mismatch": false, "relocation_offered": "unknown", "remote_type": "remote", '
    '"salary": null, "recommended_cv_track": "qa_automation", "rationale": "Good fit."}'
)


def test_evaluate_model_on_posting_success_first_try():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "test/model",
                "choices": [{"message": {"content": VALID_CONTENT}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.002},
            },
        )

    with _make_client(handler) as client:
        postings = runner.load_eval_postings(runner.SAMPLE_EVAL_POSTINGS_PATH)
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
        ) = runner.evaluate_model_on_posting(
            client,
            model_entry=ModelEntry(id="test/model"),
            facts_summary="facts",
            posting=postings[0],
            reasoning_overrides={},
        )

    assert valid is True
    assert retries == 0
    assert latency_ms >= 0
    assert cost_usd == 0.002
    assert output_tokens == 50
    assert result is not None
    assert result.fit_score == 80
    assert payload["model"] == "test/model"
    assert reasoning_fallback_used is False
    assert error is None


def test_evaluate_model_on_posting_invalid_json_exhausts_retries():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "test/model",
                "choices": [{"message": {"content": "not json"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001},
            },
        )

    with _make_client(handler) as client:
        postings = runner.load_eval_postings(runner.SAMPLE_EVAL_POSTINGS_PATH)
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
        ) = runner.evaluate_model_on_posting(
            client,
            model_entry=ModelEntry(id="test/model"),
            facts_summary="facts",
            posting=postings[0],
            reasoning_overrides={},
        )

    assert valid is False
    assert retries == runner.MAX_RETRIES
    assert cost_usd is None
    assert output_tokens is None
    assert result is None
    assert payload["model"] == "test/model"
    assert reasoning_fallback_used is False
    assert error is not None


def test_evaluate_model_on_posting_uses_cached_reasoning_override():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "test/model",
                "choices": [{"message": {"content": VALID_CONTENT}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
            },
        )

    with _make_client(handler) as client:
        postings = runner.load_eval_postings(runner.SAMPLE_EVAL_POSTINGS_PATH)
        runner.evaluate_model_on_posting(
            client,
            model_entry=ModelEntry(id="test/model"),
            facts_summary="facts",
            posting=postings[0],
            reasoning_overrides={"test/model": None},
        )

    assert "reasoning" not in calls[0]


def test_evaluate_model_aggregates_across_postings():
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        # first posting succeeds, rest fail validation
        content = VALID_CONTENT if call_count["n"] == 1 else "not json"
        return httpx.Response(
            200,
            json={
                "model": "test/model",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
            },
        )

    with _make_client(handler) as client:
        postings = runner.load_eval_postings(runner.SAMPLE_EVAL_POSTINGS_PATH)
        report = runner.evaluate_model(
            client,
            model_entry=ModelEntry(id="test/model"),
            facts_summary="facts",
            postings=postings,
        )

    assert report.model == "test/model"
    assert report.total == 3
    assert report.valid == 1
    assert report.validity_rate == 1 / 3
    assert len(report.errors) == 2
    assert len(report.examples) == 1
    assert report.examples[0].result.fit_score == 80
    assert report.sample_request_payload is not None
    assert report.sample_output_tokens == 5
    assert report.total_output_tokens == 5
    assert report.reasoning_fallback_used is False


def test_evaluate_model_throttles_free_tier(monkeypatch):
    sleeps = []
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: sleeps.append(seconds))

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "test/model:free",
                "choices": [{"message": {"content": VALID_CONTENT}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0},
            },
        )

    with _make_client(handler) as client:
        postings = runner.load_eval_postings(runner.SAMPLE_EVAL_POSTINGS_PATH)
        runner.evaluate_model(
            client,
            model_entry=ModelEntry(id="test/model:free"),
            facts_summary="facts",
            postings=postings,
        )

    # 3 postings -> throttle check happens before calls 2 and 3 (not call 1)
    assert len(sleeps) == 2


def test_format_eval_report_includes_model_stats():
    report = runner.ModelEvalReport(
        model="test/model",
        total=2,
        valid=2,
        total_retries=0,
        latencies_ms=[100.0, 300.0],
        total_cost_usd=0.01,
        total_output_tokens=42,
    )
    text = runner.format_eval_report({"test/model": report})
    assert "test/model" in text
    assert "validity rate: 100%" in text
    assert "$0.0100" in text
    assert "median latency:200 ms" in text
    assert "max latency:   300 ms" in text
    assert "output tokens: 42" in text


def test_format_eval_report_notes_reasoning_fallback():
    report = runner.ModelEvalReport(
        model="strict/model", total=1, valid=1, reasoning_fallback_used=True
    )
    text = runner.format_eval_report({"strict/model": report})
    assert "rejected an explicit reasoning effort" in text
