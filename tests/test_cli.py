import json
import shutil

import pytest
from typer.testing import CliRunner

from job_agent import cli, db, dedup
from job_agent.config import (
    Config,
    DjinniSourceConfig,
    FallbacksConfig,
    LinkedinSourceConfig,
    ModelsConfig,
    Secrets,
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
    monkeypatch.setattr(
        cli, "collect_djinni", lambda c, conn=None: [make_posting(url="https://a/1")]
    )
    monkeypatch.setattr(cli, "collect_dou", lambda c: [make_posting(url="https://a/2")])
    monkeypatch.setattr(cli, "collect_remoteok", lambda c: [])
    monkeypatch.setattr(cli, "collect_wwr", lambda c: [])
    monkeypatch.setattr(cli, "collect_linkedin", lambda c: [])

    postings, failed = cli.collect_all(config)
    assert len(postings) == 2
    assert failed == []


def test_collect_all_isolates_a_failing_source(monkeypatch, config):
    def broken_collect_dou(c):
        raise ConnectionError("dou is down")

    monkeypatch.setattr(
        cli, "collect_djinni", lambda c, conn=None: [make_posting(url="https://a/1")]
    )
    monkeypatch.setattr(cli, "collect_dou", broken_collect_dou)
    monkeypatch.setattr(
        cli, "collect_remoteok", lambda c: [make_posting(url="https://a/2")]
    )
    monkeypatch.setattr(cli, "collect_wwr", lambda c: [])
    monkeypatch.setattr(cli, "collect_linkedin", lambda c: [])

    postings, failed = cli.collect_all(config)

    # the other 4 sources' postings still come back
    assert {p.url for p in postings} == {"https://a/1", "https://a/2"}
    assert failed == ["dou"]


def test_run_pipeline_inserts_filters_and_scores(monkeypatch, config, conn):
    kept_posting = make_posting(url="https://a/1", title="QA Engineer", raw_tags=["QA"])
    filtered_posting = make_posting(
        url="https://a/2", title="Senior QA Architect", raw_tags=["QA"]
    )

    monkeypatch.setattr(
        cli, "collect_all", lambda c, conn=None: ([kept_posting, filtered_posting], [])
    )
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

    scored_rows, source_counts_this_run, failed_sources = cli.run_pipeline(
        config, DummySecrets(), conn
    )
    assert failed_sources == []

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


def test_run_pipeline_under_ci_mode_never_prints_posting_or_fact_content(
    monkeypatch, config, conn, capsys
):
    """Public-repo Actions logs are world-readable -- under JOB_CI, no
    posting title/description or fact value may ever reach stdout/stderr,
    even indirectly via an exception message."""
    monkeypatch.setattr(cli, "CI_MODE", True)

    secret_marker = "ZzMarkerCandidateNameZz"
    posting_marker = "ZzMarkerPostingTitleZz"

    kept_posting = make_posting(
        url="https://a/1", title=f"QA Engineer {posting_marker}", raw_tags=["QA"]
    )
    monkeypatch.setattr(cli, "collect_all", lambda c, conn=None: ([kept_posting], []))
    monkeypatch.setattr(
        cli.facts, "load_facts", lambda path: {"candidate": {"name": secret_marker}}
    )
    monkeypatch.setattr(cli.facts, "render_facts_summary", lambda data: secret_marker)
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

    cli.run_pipeline(config, DummySecrets(), conn)

    captured = capsys.readouterr()
    assert secret_marker not in captured.out
    assert secret_marker not in captured.err
    assert posting_marker not in captured.out
    assert posting_marker not in captured.err


class DummySecrets:
    openrouter_api_key = "key"
    telegram_bot_token = "token"
    telegram_chat_id = "chat"


def _patch_for_scoring(monkeypatch, fit_score=80):
    """Shared setup for tests that need score_posting to actually flip a
    posting to 'scored' without a real LLM call."""
    monkeypatch.setattr(cli.facts, "load_facts", lambda path: {})
    monkeypatch.setattr(cli.facts, "render_facts_summary", lambda data: "facts")
    monkeypatch.setattr(cli, "make_client", lambda api_key: _NullContextClient())

    valid_result = ScoringResult(
        fit_score=fit_score,
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


def _insert_stuck_posting(conn, **overrides) -> int:
    """A posting left in status='new' (the default on insert) -- exactly
    what an interrupted prior run leaves behind: collected and inserted,
    but never reached prefilter/scoring."""
    posting = make_posting(**overrides)
    posting.canonical_url = posting.url
    posting.dedup_hash = dedup.compute_dedup_hash(
        company=posting.company, title=posting.title, description=posting.description
    )
    return db.insert_posting(conn, posting)


def test_resume_stuck_postings_picks_up_new_status_rows(monkeypatch, config, conn):
    stuck_id = _insert_stuck_posting(
        conn, url="https://a/stuck", title="QA Engineer", raw_tags=["QA"]
    )
    assert db.get_posting(conn, stuck_id)["status"] == "new"

    _patch_for_scoring(monkeypatch)

    result = cli.resume_stuck_postings(config, DummySecrets(), conn)

    assert result.resumed_count == 1
    assert result.filtered_out_count == 0
    assert result.score_failed_count == 0
    assert [row["id"] for row in result.scored_rows] == [stuck_id]
    assert db.get_posting(conn, stuck_id)["status"] == "scored"

    runs = db.get_recent_pipeline_runs(conn)
    assert len(runs) == 1
    assert runs[0]["kind"] == "resume"
    assert runs[0]["scored_count"] == 1


def test_resume_stuck_postings_noop_when_nothing_stuck(config, conn):
    result = cli.resume_stuck_postings(config, DummySecrets(), conn)

    assert result == cli.ResumeResult(
        resumed_count=0, filtered_out_count=0, scored_rows=[], score_failed_count=0
    )
    assert db.get_recent_pipeline_runs(conn) == []  # no-op leaves no telemetry noise


def test_resume_stuck_postings_also_filters_out_ones_that_no_longer_pass(monkeypatch, config, conn):
    stuck_id = _insert_stuck_posting(
        conn, url="https://a/stuck", title="Senior QA Architect", raw_tags=["QA"]
    )
    _patch_for_scoring(monkeypatch)

    result = cli.resume_stuck_postings(config, DummySecrets(), conn)

    assert result.filtered_out_count == 1
    assert result.scored_rows == []
    row = db.get_posting(conn, stuck_id)
    assert row["status"] == "filtered_out"
    assert row["filtered_reason"] == "seniority_stopword:architect"


def test_run_pipeline_auto_resumes_stuck_postings_before_collecting(monkeypatch, config, conn):
    stuck_id = _insert_stuck_posting(
        conn, url="https://a/stuck", title="QA Engineer", raw_tags=["QA"]
    )

    monkeypatch.setattr(cli, "collect_all", lambda c, conn=None: ([], []))
    _patch_for_scoring(monkeypatch)

    scored_rows, source_counts, failed = cli.run_pipeline(config, DummySecrets(), conn)

    assert failed == []
    assert source_counts == {}
    assert [row["id"] for row in scored_rows] == [stuck_id]
    assert db.get_posting(conn, stuck_id)["status"] == "scored"

    kinds = sorted(row["kind"] for row in db.get_recent_pipeline_runs(conn, limit=10))
    assert kinds == ["resume", "run"]


def test_run_pipeline_resumes_the_stuck_original_even_though_dedup_hides_a_recollected_duplicate(
    monkeypatch, config, conn
):
    """The scenario item 1 exists to fix: a posting gets stuck in status=
    'new' by an interrupted run, and a *later* run's collection re-fetches
    the same posting from the source. dedup correctly refuses to insert a
    second copy (same canonical_url) -- but the original must still get
    finished via the automatic resume step, not sit stuck forever just
    because dedup considers it "already seen"."""
    stuck_id = _insert_stuck_posting(
        conn,
        url="https://a/stuck",
        title="QA Engineer",
        description="We need a QA automation engineer.",
        raw_tags=["QA"],
    )

    recollected_duplicate = make_posting(
        url="https://a/stuck",
        title="QA Engineer",
        description="We need a QA automation engineer.",
        raw_tags=["QA"],
    )
    monkeypatch.setattr(cli, "collect_all", lambda c, conn=None: ([recollected_duplicate], []))
    _patch_for_scoring(monkeypatch)

    scored_rows, source_counts, failed = cli.run_pipeline(config, DummySecrets(), conn)

    rows = conn.execute(
        "SELECT id, status FROM postings WHERE canonical_url = 'https://a/stuck'"
    ).fetchall()
    assert len(rows) == 1  # dedup did not insert a second row for the recollected duplicate
    assert rows[0]["id"] == stuck_id
    assert rows[0]["status"] == "scored"  # the original was resumed, not left stuck
    assert [row["id"] for row in scored_rows] == [stuck_id]


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


def test_render_test_refuses_to_render_invalid_content(monkeypatch, tmp_path):
    from job_agent.cv import content as cv_content_module

    real_build = cv_content_module.build_cv_content

    def poisoned_build(facts_data, **kwargs):
        content = real_build(facts_data, **kwargs)
        content["skills"][0]["items"].append("Invented Skill Not In Facts")
        return content

    monkeypatch.setattr(cv_content_module, "build_cv_content", poisoned_build)

    output_path = tmp_path / "cv.pdf"
    result = runner.invoke(cli.app, ["render-test", "--output", str(output_path)])

    assert result.exit_code == 1
    assert "Content validation FAILED" in result.output
    assert "Invented Skill Not In Facts" in result.output
    assert not output_path.exists()


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


def test_echo_error_shows_full_message_outside_ci_mode(monkeypatch, capsys):
    monkeypatch.setattr(cli, "CI_MODE", False)
    cli._echo_error(FileNotFoundError("data/facts.yaml not found"))
    assert "data/facts.yaml not found" in capsys.readouterr().err


def test_echo_error_hides_message_under_ci_mode(monkeypatch, capsys):
    monkeypatch.setattr(cli, "CI_MODE", True)
    cli._echo_error(FileNotFoundError("data/facts.yaml not found"))
    err = capsys.readouterr().err
    assert "data/facts.yaml not found" not in err
    assert "FileNotFoundError" in err


def test_echo_error_prefix_always_shown(monkeypatch, capsys):
    monkeypatch.setattr(cli, "CI_MODE", True)
    cli._echo_error(ValueError("secret leaked value"), prefix="Missing secrets: ")
    err = capsys.readouterr().err
    assert err.startswith("Missing secrets: ")
    assert "secret leaked value" not in err


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


def test_analyze_rescore_ids_and_record_primary_scope_and_overwrite(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = db.connect(db_path)
    db.init_db(conn)
    posting1 = make_posting()
    posting1.canonical_url = posting1.url
    posting1.dedup_hash = "hash1"
    posting1_id = db.insert_posting(conn, posting1)

    posting2 = make_posting(url="https://a/2")
    posting2.canonical_url = "https://a/2"
    posting2.dedup_hash = "hash2"
    db.insert_posting(conn, posting2)
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
            "--ids",
            str(posting1_id),
            "--record-primary",
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
    assert calls == [(posting1_id, True)]  # only the scoped id, with primary overwritten


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
        cli,
        "run_pipeline",
        lambda config, secrets, conn, facts_path=None: ([], {"djinni": 0}, []),
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


def test_main_no_poll_sends_digest_but_skips_poll(monkeypatch, tmp_path):
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
            "--no-poll",
        ],
    )

    assert result.exit_code == 0, result.output
    assert send_calls == [1]
    assert poll_calls == []
    assert "not polling" in result.output


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


def test_telegram_process_command_reports_count(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    db_path = tmp_path / "jobs.db"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    async def fake_process(conn, secrets, config, *, offset_path, alert_flag_path, **kwargs):
        assert offset_path == tmp_path / "offset.txt"
        return 4

    monkeypatch.setattr(cli, "_telegram_process", fake_process)

    result = runner.invoke(
        cli.app,
        [
            "telegram-process",
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--db",
            str(db_path),
            "--offset-file",
            str(tmp_path / "offset.txt"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Processed 4 update(s)." in result.output


def test_read_write_offset_roundtrip(tmp_path):
    path = tmp_path / "offset.txt"
    assert cli._read_offset(path) is None

    cli._write_offset(path, 42)
    assert cli._read_offset(path) == 42


def test_db_pull_requires_token(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_REPO_TOKEN", raising=False)
    result = runner.invoke(cli.app, ["db-pull", "--repo", "owner/data-repo"])
    assert result.exit_code == 1
    assert "DATA_REPO_TOKEN" in result.output


def test_db_pull_downloads_via_release_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_REPO_TOKEN", "tok")
    db_path = tmp_path / "jobs.db"

    calls = []

    def fake_download_latest(client, repo, tag, dest):
        calls.append((repo, tag, dest))
        return True

    monkeypatch.setattr(cli.release_assets, "download_latest", fake_download_latest)

    result = runner.invoke(
        cli.app, ["db-pull", "--repo", "owner/data-repo", "--db", str(db_path)]
    )

    assert result.exit_code == 0, result.output
    assert calls == [("owner/data-repo", "jobs-db", db_path)]
    assert "downloaded" in result.output


def test_db_push_requires_token(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_REPO_TOKEN", raising=False)
    result = runner.invoke(cli.app, ["db-push", "--repo", "owner/data-repo"])
    assert result.exit_code == 1
    assert "DATA_REPO_TOKEN" in result.output


def test_db_push_uploads_via_release_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_REPO_TOKEN", "tok")
    db_path = tmp_path / "jobs.db"

    calls = []

    def fake_upload_and_rotate(client, repo, tag, src, *, keep):
        calls.append((repo, tag, src, keep))
        return "jobs-20260101T000000Z.db"

    monkeypatch.setattr(cli.release_assets, "upload_and_rotate", fake_upload_and_rotate)

    result = runner.invoke(
        cli.app, ["db-push", "--repo", "owner/data-repo", "--db", str(db_path)]
    )

    assert result.exit_code == 0, result.output
    assert calls == [("owner/data-repo", "jobs-db", db_path, 7)]
    assert "jobs-20260101T000000Z.db" in result.output


async def test_trigger_pipeline_workflow_noop_without_github_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not make any request without GITHUB_TOKEN/REPOSITORY")

    monkeypatch.setattr(cli.httpx, "AsyncClient", fail_if_called)

    await cli._trigger_pipeline_workflow()  # must not raise


async def test_trigger_pipeline_workflow_calls_dispatch_api(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/job-search-agent")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None):
            calls.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(cli.httpx, "AsyncClient", FakeAsyncClient)

    await cli._trigger_pipeline_workflow()

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "/repos/owner/job-search-agent/actions/workflows/pipeline.yml/dispatches"
    assert payload == {"ref": "main"}


def test_doctor_command_reports_missing_config_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.doctor_module, "check_data_files", lambda *a, **k: [])
    monkeypatch.setattr(cli.doctor_module, "check_binaries", lambda: [])

    result = runner.invoke(
        cli.app,
        [
            "doctor",
            "--config",
            str(tmp_path / "missing-config.yaml"),
            "--env",
            str(tmp_path / "missing.env"),
            "--db",
            str(tmp_path / "jobs.db"),
        ],
    )

    assert result.exit_code == 1
    assert "[FAILED] config.yaml" in result.output
    assert "[FAILED] secrets (.env)" in result.output


def test_doctor_command_passes_when_every_check_is_ok(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    def ok(name):
        return cli.doctor_module.DoctorCheck(name=name, ok=True, detail="fine")

    monkeypatch.setattr(cli.doctor_module, "check_data_files", lambda *a, **k: [ok("data")])
    monkeypatch.setattr(cli.doctor_module, "check_binaries", lambda: [ok("binary")])
    monkeypatch.setattr(cli.doctor_module, "check_openrouter", lambda api_key: ok("openrouter"))

    async def fake_check_telegram(token, chat_id):
        return ok("telegram")

    monkeypatch.setattr(cli.doctor_module, "check_telegram", fake_check_telegram)
    monkeypatch.setattr(cli.doctor_module, "check_sources", lambda config: [ok("sources")])

    result = runner.invoke(
        cli.app,
        [
            "doctor",
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--db",
            str(tmp_path / "jobs.db"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "checks passed" in result.output
    assert "[FAILED]" not in result.output


async def test_send_digest_sends_budget_alert_when_present(monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "jobs.db")
    db.init_db(conn)

    sent_messages = []

    class FakeBot:
        def __init__(self):
            self.session = self

        async def close(self):
            pass

        async def send_message(self, chat_id, text):
            sent_messages.append((chat_id, text))

    fake_bot = FakeBot()
    monkeypatch.setattr(cli.telegram_bot, "build_bot", lambda token: fake_bot)

    async def fake_send_digest_and_cards(*args, **kwargs):
        pass

    monkeypatch.setattr(cli.telegram_bot, "send_digest_and_cards", fake_send_digest_and_cards)

    secrets = Secrets(
        openrouter_api_key="x", telegram_bot_token="x", telegram_chat_id="chat-1"
    )

    await cli._send_digest(secrets, conn, {}, [], 40, [], "Monthly LLM spend $9.00 ...")

    assert sent_messages == [("chat-1", "Monthly LLM spend $9.00 ...")]
    conn.close()


async def test_send_digest_sends_nothing_extra_when_no_budget_alert(monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "jobs.db")
    db.init_db(conn)

    sent_messages = []

    class FakeBot:
        def __init__(self):
            self.session = self

        async def close(self):
            pass

        async def send_message(self, chat_id, text):
            sent_messages.append((chat_id, text))

    fake_bot = FakeBot()
    monkeypatch.setattr(cli.telegram_bot, "build_bot", lambda token: fake_bot)

    async def fake_send_digest_and_cards(*args, **kwargs):
        pass

    monkeypatch.setattr(cli.telegram_bot, "send_digest_and_cards", fake_send_digest_and_cards)

    secrets = Secrets(
        openrouter_api_key="x", telegram_bot_token="x", telegram_chat_id="chat-1"
    )

    await cli._send_digest(secrets, conn, {}, [], 40)

    assert sent_messages == []
    conn.close()


def test_main_passes_budget_alert_to_send_digest(monkeypatch, tmp_path):
    config_path, env_path, db_path = _prepare_main_invocation(monkeypatch, tmp_path)

    captured = []

    async def fake_send_digest(*args, **kwargs):
        captured.append(args)

    monkeypatch.setattr(cli, "_send_digest", fake_send_digest)
    monkeypatch.setattr(cli, "_poll", lambda *a, **k: None)
    monkeypatch.setattr(
        cli.budget, "check_budget_threshold", lambda conn, **kwargs: "budget cap approached"
    )

    result = runner.invoke(
        cli.app,
        [
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--db",
            str(db_path),
            "--no-poll",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured[0][-1] == "budget cap approached"


async def test_telegram_process_sends_budget_alert(monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "jobs.db")
    db.init_db(conn)

    sent_messages = []

    class FakeBot:
        def __init__(self):
            self.session = self

        async def close(self):
            pass

        async def send_message(self, chat_id, text):
            sent_messages.append((chat_id, text))

    fake_bot = FakeBot()
    monkeypatch.setattr(cli.telegram_bot, "build_bot", lambda token: fake_bot)
    monkeypatch.setattr(cli.telegram_bot, "build_dispatcher", lambda *a, **k: object())

    async def fake_process_pending_updates(bot, dispatcher, *, offset):
        return 0, None

    monkeypatch.setattr(cli.telegram_bot, "process_pending_updates", fake_process_pending_updates)
    monkeypatch.setattr(cli.deadman, "check_pipeline_staleness", lambda *a, **k: None)
    monkeypatch.setattr(
        cli.budget, "check_budget_threshold", lambda *a, **k: "budget cap approached"
    )

    secrets = Secrets(
        openrouter_api_key="x", telegram_bot_token="x", telegram_chat_id="chat-1"
    )
    config = Config(
        thresholds={"wide": ThresholdConfig(fit_score_min=40)},
        models=ModelsConfig(
            bulk="test/bulk", quality="test/quality", fallbacks=FallbacksConfig()
        ),
        sources=SourcesConfig(),
    )

    await cli._telegram_process(
        conn,
        secrets,
        config,
        offset_path=tmp_path / "offset.txt",
        alert_flag_path=tmp_path / "stale.flag",
        stale_threshold_hours=36.0,
        facts_data=None,
        output_dir=tmp_path / "packages",
        budget_alert_flag_path=tmp_path / "budget.flag",
    )

    assert sent_messages == [("chat-1", "budget cap approached")]
    conn.close()


def test_resume_command_reports_nothing_stuck(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    result = runner.invoke(
        cli.app,
        [
            "resume",
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--db",
            str(tmp_path / "jobs.db"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Nothing stuck" in result.output


def test_resume_command_reports_resumed_counts(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    shutil.copy("config.example.yaml", config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY=x\nTELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=x\n", encoding="utf-8"
    )

    fake_result = cli.ResumeResult(
        resumed_count=2, filtered_out_count=1, scored_rows=[{"id": 1}], score_failed_count=0
    )
    monkeypatch.setattr(cli, "resume_stuck_postings", lambda *a, **k: fake_result)

    result = runner.invoke(
        cli.app,
        [
            "resume",
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "--db",
            str(tmp_path / "jobs.db"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Resumed 2 posting(s)" in result.output
    assert "1 filtered out" in result.output
    assert "1 scored" in result.output
