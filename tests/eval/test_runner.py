import httpx

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
        valid, retries, latency_ms, cost_usd, error = runner.evaluate_model_on_posting(
            client, model="test/model", facts_summary="facts", posting=postings[0]
        )

    assert valid is True
    assert retries == 0
    assert latency_ms >= 0
    assert cost_usd == 0.002
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
        valid, retries, latency_ms, cost_usd, error = runner.evaluate_model_on_posting(
            client, model="test/model", facts_summary="facts", posting=postings[0]
        )

    assert valid is False
    assert retries == runner.MAX_RETRIES
    assert cost_usd is None
    assert error is not None


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
            client, model="test/model", facts_summary="facts", postings=postings
        )

    assert report.model == "test/model"
    assert report.total == 3
    assert report.valid == 1
    assert report.validity_rate == 1 / 3
    assert len(report.errors) == 2


def test_format_eval_report_includes_model_stats():
    report = runner.ModelEvalReport(
        model="test/model",
        total=2,
        valid=2,
        total_retries=0,
        total_latency_ms=200,
        total_cost_usd=0.01,
    )
    text = runner.format_eval_report({"test/model": report})
    assert "test/model" in text
    assert "validity rate: 100%" in text
    assert "$0.0100" in text
