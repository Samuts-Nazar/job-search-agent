import json

import pytest

from job_agent import db, scoring
from job_agent.config import Config, FallbacksConfig, ModelsConfig, ThresholdConfig
from job_agent.llm.client import CompletionResult, OpenRouterError
from job_agent.llm.schemas import ScoringResult

VALID_RESULT = ScoringResult(
    fit_score=80,
    verdict="apply",
    matched_skills=["Python"],
    missing_skills=[],
    seniority_match="match",
    required_languages=["English"],
    language_mismatch=False,
    relocation_offered="unknown",
    remote_type="remote",
    salary=None,
    recommended_cv_track="qa_automation",
    rationale="Strong match.",
)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def config():
    return Config(
        thresholds={
            "wide": ThresholdConfig(fit_score_min=40),
            "selective": ThresholdConfig(fit_score_min=70, seniority_strict=True),
        },
        models=ModelsConfig(
            bulk="primary/model",
            quality="quality/model",
            fallbacks=FallbacksConfig(bulk=["fallback/model"], quality=[]),
        ),
    )


@pytest.fixture
def posting():
    return db.Posting(
        source="djinni",
        url="https://djinni.co/jobs/1/",
        title="QA Automation Engineer",
        description="We need a QA automation engineer.",
    )


@pytest.fixture
def posting_id(conn, posting):
    new_posting = db.Posting(**vars(posting))
    new_posting.canonical_url = posting.url
    new_posting.dedup_hash = "hash1"
    return db.insert_posting(conn, new_posting)


def test_score_posting_success_on_first_attempt(monkeypatch, conn, config, posting, posting_id):
    def fake_attempt_score(client, *, model, cv_track, facts_summary, posting):
        completion = CompletionResult(
            raw_content=VALID_RESULT.model_dump_json(),
            model=model,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.002,
        )
        return VALID_RESULT, completion

    monkeypatch.setattr(scoring, "_attempt_score", fake_attempt_score)

    result = scoring.score_posting(
        conn,
        client=object(),
        posting_id=posting_id,
        posting=posting,
        config=config,
        cv_track="qa_automation",
        facts_summary="facts",
    )

    assert result == VALID_RESULT
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "scored"
    assert row["fit_score"] == 80
    assert row["verdict"] == "apply"
    assert json.loads(row["matched_skills"]) == ["Python"]

    calls = conn.execute("SELECT * FROM llm_calls").fetchall()
    assert len(calls) == 1
    assert calls[0]["model"] == "primary/model"
    assert calls[0]["cost_usd"] == 0.002


def test_score_posting_falls_back_after_retries_exhausted(
    monkeypatch, conn, config, posting, posting_id
):
    attempts = []

    def fake_attempt_score(client, *, model, cv_track, facts_summary, posting):
        attempts.append(model)
        if model == "primary/model":
            raise OpenRouterError("bad json")
        completion = CompletionResult(
            raw_content=VALID_RESULT.model_dump_json(),
            model=model,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
        )
        return VALID_RESULT, completion

    monkeypatch.setattr(scoring, "_attempt_score", fake_attempt_score)

    result = scoring.score_posting(
        conn,
        client=object(),
        posting_id=posting_id,
        posting=posting,
        config=config,
        cv_track="qa_automation",
        facts_summary="facts",
    )

    assert result == VALID_RESULT
    # primary model retried MAX_RETRIES_PER_MODEL+1 times before falling back
    assert attempts.count("primary/model") == scoring.MAX_RETRIES_PER_MODEL + 1
    assert attempts[-1] == "fallback/model"
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "scored"


def test_score_posting_marks_score_failed_when_all_models_fail(
    monkeypatch, conn, config, posting, posting_id
):
    def fake_attempt_score(client, *, model, cv_track, facts_summary, posting):
        raise OpenRouterError("always fails")

    monkeypatch.setattr(scoring, "_attempt_score", fake_attempt_score)

    result = scoring.score_posting(
        conn,
        client=object(),
        posting_id=posting_id,
        posting=posting,
        config=config,
        cv_track="qa_automation",
        facts_summary="facts",
    )

    assert result is None
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "score_failed"
    assert row["score_failed_reason"]
    assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
