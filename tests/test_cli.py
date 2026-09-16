import shutil

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


@pytest.mark.skipif(shutil.which("typst") is None, reason="typst CLI not on PATH")
def test_render_test_command_renders_and_checks(tmp_path):
    output_path = tmp_path / "cv.pdf"
    result = runner.invoke(cli.app, ["render-test", "--output", str(output_path)])
    assert result.exit_code == 0
    assert output_path.exists()
    assert "ATS checks: OK" in result.stdout


def test_check_data_command_fails_on_missing_files(tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "check-data",
            "--facts",
            str(tmp_path / "facts.yaml"),
            "--answers",
            str(tmp_path / "answers.yaml"),
        ],
    )
    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    assert "file not found" in result.stdout


def test_check_data_command_passes_on_edited_real_data(tmp_path):
    facts_path = tmp_path / "facts.yaml"
    answers_path = tmp_path / "answers.yaml"
    facts_path.write_text(
        "example: false\n"
        "candidate:\n"
        "  name: Real Person\n"
        '  email: "real@realmail.com"\n'
        '  phone: "+1 555"\n'
        "  city: Kyiv\n"
        "  country: Ukraine\n"
        "  timezone: UTC+2\n"
        "  links:\n"
        "    linkedin: https://linkedin.com/in/real\n"
        "cv_tracks:\n"
        "  - id: general\n"
        "    title: Engineer\n"
        "languages:\n"
        "  - name: English\n"
        "    cefr: B2\n",
        encoding="utf-8",
    )
    answers_path.write_text(
        "example: false\n"
        "work_authorization:\n"
        "  eu: authorized\n"
        "  us: requires_sponsorship\n"
        "  uk: requires_sponsorship\n"
        "  ukraine: authorized\n"
        "relocation:\n"
        "  willing: false\n"
        "notice_period_days: 14\n"
        "salary_expectation:\n"
        "  currency: USD\n"
        "  monthly_min: 3000\n"
        "  monthly_max: 4000\n"
        "timezone: UTC+2\n"
        "languages:\n"
        "  - name: English\n"
        "    cefr: B2\n"
        "links:\n"
        "  linkedin: https://linkedin.com/in/real\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app, ["check-data", "--facts", str(facts_path), "--answers", str(answers_path)]
    )
    assert result.exit_code == 0
    assert "OK" in result.stdout


def test_main_refuses_to_run_with_example_data(tmp_path):
    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    result = runner.invoke(
        cli.app,
        [
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--facts",
            "data.example/facts.example.yaml",
            "--answers",
            "data.example/answers.example.yaml",
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to run" in result.output
    assert "candidate.email" in result.output
