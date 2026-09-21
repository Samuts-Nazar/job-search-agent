import json
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

    def fake_score_posting(
        conn_, client, *, posting_id, posting, config, facts_summary, reasoning_overrides=None
    ):
        db.update_posting_status(
            conn_,
            posting_id,
            "scored",
            fit_score=valid_result.fit_score,
            verdict=cli.scoring.derive_verdict(valid_result.fit_score, config.active_threshold),
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
        "SELECT status, filtered_reason FROM postings WHERE title = 'Senior QA Architect'"
    ).fetchone()
    assert filtered_row["status"] == "filtered_out"
    # config fixture uses wide mode (seniority_strict=False) -> only the
    # relaxed stopwords apply, and "senior" isn't one of them
    assert filtered_row["filtered_reason"] == "seniority_stopword:architect"

    scored_row = conn.execute(
        "SELECT ats_vendor FROM postings WHERE title = 'QA Engineer'"
    ).fetchone()
    assert scored_row["ats_vendor"] is None  # https://a/1 isn't a known ATS domain

    runs = db.get_recent_pipeline_runs(conn)
    assert len(runs) == 1
    assert runs[0]["kind"] == "run"
    assert runs[0]["collected_count"] == 2
    assert runs[0]["new_count"] == 2
    assert runs[0]["filtered_out_count"] == 1
    assert runs[0]["scored_count"] == 1
    assert runs[0]["score_failed_count"] == 0
    assert json.loads(runs[0]["source_counts"]) == {"djinni": 2}


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
    assert "Extended stats" in result.stdout
    assert "Most frequent missing skills" in result.stdout


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


def test_export_command_writes_csv_files(tmp_path):
    db_path = tmp_path / "jobs.db"
    out_dir = tmp_path / "export"
    conn = db.connect(db_path)
    db.init_db(conn)
    posting = make_posting()
    posting.canonical_url = posting.url
    posting.dedup_hash = "hash1"
    db.insert_posting(conn, posting)
    conn.close()

    result = runner.invoke(
        cli.app, ["export", "--db", str(db_path), "--out-dir", str(out_dir)]
    )

    assert result.exit_code == 0
    assert (out_dir / "postings.csv").exists()
    assert (out_dir / "scores.csv").exists()
    assert (out_dir / "applications.csv").exists()
    assert "postings: 1 rows" in result.output


def test_export_command_rejects_unsupported_format(tmp_path):
    db_path = tmp_path / "jobs.db"
    result = runner.invoke(
        cli.app, ["export", "--db", str(db_path), "--format", "json"]
    )
    assert result.exit_code == 1


def test_posting_from_row_reconstructs_posting(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    posting = make_posting(raw_payload='{"raw": true}', ats_vendor="greenhouse")
    posting.canonical_url = posting.url
    posting.dedup_hash = "hash1"
    posting_id = db.insert_posting(conn, posting)
    row = db.get_posting(conn, posting_id)
    conn.close()

    rebuilt = cli._posting_from_row(row)

    assert rebuilt.source == "djinni"
    assert rebuilt.url == posting.url
    assert rebuilt.title == "QA Automation Engineer"
    assert rebuilt.raw_tags == ["QA"]
    assert rebuilt.raw_payload == '{"raw": true}'
    assert rebuilt.ats_vendor == "greenhouse"
    assert rebuilt.canonical_url == posting.url
    assert rebuilt.dedup_hash == "hash1"


def test_analyze_without_rescore_flag_errors(tmp_path):
    result = runner.invoke(cli.app, ["analyze", "--db", str(tmp_path / "jobs.db")])
    assert result.exit_code == 1
    assert "--rescore" in result.output


def test_analyze_rescore_calls_score_posting_for_every_stored_posting(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    posting1 = make_posting()
    posting1.canonical_url = posting1.url
    posting1.dedup_hash = "hash1"
    db.insert_posting(conn, posting1)

    posting2 = make_posting(url="https://a/2")
    posting2.canonical_url = "https://a/2"
    posting2.dedup_hash = "hash2"
    posting2_id = db.insert_posting(conn, posting2)
    db.update_posting_status(
        conn, posting2_id, "filtered_out", filtered_reason="category_not_allowed"
    )
    conn.close()

    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    monkeypatch.setattr(cli, "make_client", lambda api_key: _NullContextClient())
    monkeypatch.setattr(cli.facts, "load_facts", lambda path: {})
    monkeypatch.setattr(cli.facts, "render_facts_summary", lambda data: "facts")

    calls = []

    def fake_score_posting(conn_, client, *, posting_id, posting, config, facts_summary, **kwargs):
        calls.append((posting_id, kwargs.get("record_as_primary")))
        return None

    monkeypatch.setattr(cli.scoring, "score_posting", fake_score_posting)

    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--rescore",
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--facts",
            "data.example/facts.example.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 2  # both postings rescored, including the filtered-out one
    assert all(record_as_primary is False for _pid, record_as_primary in calls)


def test_analyze_readmit_scores_newly_passing_and_leaves_the_rest_filtered(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = db.connect(db_path)
    db.init_db(conn)

    # simulates a posting the OLD substring-matching seniority filter wrongly
    # rejected ("architect" inside "Architecture") -- the current whole-word
    # filter (item 1 fix) no longer flags it.
    newly_passing = make_posting(
        url="https://a/1", title="QA Architecture Engineer", raw_tags=["QA"]
    )
    newly_passing.canonical_url = newly_passing.url
    newly_passing.dedup_hash = "hash1"
    newly_passing_id = db.insert_posting(conn, newly_passing)
    db.update_posting_status(
        conn, newly_passing_id, "filtered_out", filtered_reason="seniority_stopword:architect"
    )

    # config.example.yaml's wide mode uses the relaxed stopword list, which
    # keeps "principal" -- unlike "senior", this one still rejects.
    still_filtered = make_posting(
        url="https://a/2", title="Principal QA Engineer", raw_tags=["QA"]
    )
    still_filtered.canonical_url = still_filtered.url
    still_filtered.dedup_hash = "hash2"
    still_filtered_id = db.insert_posting(conn, still_filtered)
    db.update_posting_status(
        conn, still_filtered_id, "filtered_out", filtered_reason="seniority_stopword:principal"
    )
    conn.close()

    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    monkeypatch.setattr(cli, "make_client", lambda api_key: _NullContextClient())
    monkeypatch.setattr(cli.facts, "load_facts", lambda path: {})
    monkeypatch.setattr(cli.facts, "render_facts_summary", lambda data: "facts")

    scored_ids = []

    def fake_score_posting(conn_, client, *, posting_id, posting, config, facts_summary, **kwargs):
        scored_ids.append(posting_id)
        db.update_posting_status(conn_, posting_id, "scored", fit_score=80, verdict="apply")
        return object()

    monkeypatch.setattr(cli.scoring, "score_posting", fake_score_posting)

    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--readmit",
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "--env",
            str(env_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert scored_ids == [newly_passing_id]

    conn = db.connect(db_path)
    admitted_row = db.get_posting(conn, newly_passing_id)
    assert admitted_row["status"] == "scored"
    assert admitted_row["filtered_reason"] is None

    still_filtered_row = db.get_posting(conn, still_filtered_id)
    assert still_filtered_row["status"] == "filtered_out"
    assert still_filtered_row["filtered_reason"] == "seniority_stopword:principal"
    conn.close()


def _prepare_main_invocation(monkeypatch, tmp_path):
    """Common setup for `job` (main) CLI tests: real config, dummy secrets,
    data_guard bypassed, and run_pipeline stubbed so no network is hit."""
    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )
    db_path = tmp_path / "jobs.db"

    monkeypatch.setattr(cli.data_guard, "check_data_safety", lambda facts_path, answers_path: [])
    monkeypatch.setattr(
        cli, "run_pipeline", lambda config, secrets, conn, facts_path=None: ([], {"djinni": 0})
    )
    return config_path, env_path, db_path


def test_main_no_telegram_skips_send_and_poll(monkeypatch, tmp_path):
    config_path, env_path, db_path = _prepare_main_invocation(monkeypatch, tmp_path)

    send_calls = []
    poll_calls = []

    async def fake_send_digest(*args, **kwargs):
        send_calls.append(1)

    async def fake_poll(*args, **kwargs):
        poll_calls.append(1)

    monkeypatch.setattr(cli, "_send_digest", fake_send_digest)
    monkeypatch.setattr(cli, "_poll", fake_poll)

    result = runner.invoke(
        cli.app,
        [
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--db",
            str(db_path),
            "--no-telegram",
        ],
    )

    assert result.exit_code == 0, result.output
    assert send_calls == []
    assert poll_calls == []
    assert "nothing sent" in result.output


def test_main_without_no_telegram_sends_and_polls(monkeypatch, tmp_path):
    config_path, env_path, db_path = _prepare_main_invocation(monkeypatch, tmp_path)

    send_calls = []
    poll_calls = []

    async def fake_send_digest(*args, **kwargs):
        send_calls.append(1)

    async def fake_poll(*args, **kwargs):
        poll_calls.append(1)

    monkeypatch.setattr(cli, "_send_digest", fake_send_digest)
    monkeypatch.setattr(cli, "_poll", fake_poll)

    result = runner.invoke(
        cli.app,
        ["--config", str(config_path), "--env", str(env_path), "--db", str(db_path)],
    )

    assert result.exit_code == 0, result.output
    assert send_calls == [1]
    assert poll_calls == [1]


def test_telegram_cleanup_command_reports_counts(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    async def fake_cleanup(secrets, conn, below_score):
        assert below_score == 40
        return 3, 1

    monkeypatch.setattr(cli, "_telegram_cleanup", fake_cleanup)

    result = runner.invoke(
        cli.app,
        ["telegram-cleanup", "--below", "40", "--env", str(env_path), "--db", str(db_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Deleted 3 messages, 1 too old" in result.output
