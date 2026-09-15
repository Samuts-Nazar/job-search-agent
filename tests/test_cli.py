import pytest
from typer.testing import CliRunner

from job_agent import cli, db
from job_agent.config import (
    Config,
    DjinniSourceConfig,
    FallbacksConfig,
    LinkedinSourceConfig,
    ModelsConfig,
    SourcesConfig,
    ThresholdConfig,
)
from job_agent.llm.schemas import ScoringResult

runner = CliRunner()


def make_posting(**overrides) -> db.Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/1/",
        title="QA Automation Engineer",
        description="We need a QA automation engineer.",
        raw_tags=["QA"],
    )
    defaults.update(overrides)
    return db.Posting(**defaults)


@pytest.fixture
def config():
    return Config(
        thresholds={
            "wide": ThresholdConfig(fit_score_min=40, seniority_strict=False),
            "selective": ThresholdConfig(fit_score_min=70, seniority_strict=True),
        },
        models=ModelsConfig(
            bulk="primary/model", quality="quality/model", fallbacks=FallbacksConfig()
        ),
        sources=SourcesConfig(
            djinni=DjinniSourceConfig(enabled=True, categories=["QA"]),
            linkedin=LinkedinSourceConfig(enabled=False),
        ),
        categories=["QA"],
    )


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def test_collect_all_combines_sources(monkeypatch, config):
    monkeypatch.setattr(cli, "collect_djinni", lambda c: [make_posting(url="https://a/1")])
    monkeypatch.setattr(cli, "collect_dou", lambda c: [make_posting(url="https://a/2")])
    monkeypatch.setattr(cli, "collect_remoteok", lambda c: [])
    monkeypatch.setattr(cli, "collect_wwr", lambda c: [])
    monkeypatch.setattr(cli, "collect_linkedin", lambda c: [])

    postings = cli.collect_all(config)
    assert len(postings) == 2


def test_run_pipeline_inserts_filters_and_scores(monkeypatch, config, conn):
    kept_posting = make_posting(url="https://a/1", title="QA Engineer", raw_tags=["QA"])
    filtered_posting = make_posting(
        url="https://a/2", title="Senior QA Architect", raw_tags=["QA"]
    )

    monkeypatch.setattr(cli, "collect_all", lambda c: [kept_posting, filtered_posting])
    monkeypatch.setattr(cli.facts, "load_facts", lambda path: {})
    monkeypatch.setattr(cli.facts, "render_facts_summary", lambda data: "facts")
    monkeypatch.setattr(cli, "make_client", lambda api_key: _NullContextClient())

    valid_result = ScoringResult(
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
        rationale="Good fit.",
    )

    def fake_score_posting(conn_, client, *, posting_id, posting, config, facts_summary):
        db.update_posting_status(
            conn_,
            posting_id,
            "scored",
            fit_score=valid_result.fit_score,
            verdict=valid_result.verdict,
        )
        return valid_result

    monkeypatch.setattr(cli.scoring, "score_posting", fake_score_posting)

    class DummySecrets:
        openrouter_api_key = "key"
        telegram_bot_token = "token"
        telegram_chat_id = "chat"

    scored_rows, source_counts_this_run = cli.run_pipeline(config, DummySecrets(), conn)

    assert source_counts_this_run == {"djinni": 2}
    assert len(scored_rows) == 1
    assert scored_rows[0]["title"] == "QA Engineer"

    filtered_row = conn.execute(
        "SELECT status FROM postings WHERE title = 'Senior QA Architect'"
    ).fetchone()
    assert filtered_row["status"] == "filtered_out"


class _NullContextClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_stats_command_runs_on_empty_db(tmp_path):
    db_path = tmp_path / "jobs.db"
    result = runner.invoke(cli.app, ["stats", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "Stats" in result.stdout
    assert "LLM spend" in result.stdout


def test_render_test_command_reports_not_implemented():
    result = runner.invoke(cli.app, ["render-test"])
    assert result.exit_code == 0
    assert "not implemented yet" in result.stdout
