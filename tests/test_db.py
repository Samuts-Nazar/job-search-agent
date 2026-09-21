import json

import pytest

from job_agent import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def make_posting(**overrides) -> db.Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/123-qa/",
        canonical_url="https://djinni.co/jobs/123-qa/",
        dedup_hash="hash123",
        title="QA Automation Engineer",
        description="We are looking for a QA engineer.",
    )
    defaults.update(overrides)
    return db.Posting(**defaults)


def test_insert_and_get_posting(conn):
    posting_id = db.insert_posting(conn, make_posting())
    row = db.get_posting(conn, posting_id)
    assert row["title"] == "QA Automation Engineer"
    assert row["status"] == "new"
    assert row["source"] == "djinni"


def test_posting_exists_by_canonical_url(conn):
    db.insert_posting(conn, make_posting())
    assert db.posting_exists(
        conn, canonical_url="https://djinni.co/jobs/123-qa/", dedup_hash="different-hash"
    )
    assert not db.posting_exists(
        conn, canonical_url="https://djinni.co/jobs/other/", dedup_hash="different-hash"
    )


def test_posting_exists_by_dedup_hash(conn):
    db.insert_posting(conn, make_posting())
    assert db.posting_exists(
        conn, canonical_url="https://otherhost.example/jobs/456/", dedup_hash="hash123"
    )


def test_canonical_url_is_unique(conn):
    db.insert_posting(conn, make_posting())
    with pytest.raises(db.sqlite3.IntegrityError):
        db.insert_posting(conn, make_posting(dedup_hash="another-hash"))


def test_update_posting_status(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.update_posting_status(conn, posting_id, "scored", fit_score=80, verdict="apply")
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "scored"
    assert row["fit_score"] == 80
    assert row["verdict"] == "apply"


def test_get_postings_by_status(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn,
        make_posting(canonical_url="https://djinni.co/jobs/999/", dedup_hash="hash999"),
    )
    db.update_posting_status(conn, id1, "scored")
    rows = db.get_postings_by_status(conn, "scored")
    assert len(rows) == 1
    assert rows[0]["id"] == id1
    rows_new = db.get_postings_by_status(conn, "new")
    assert len(rows_new) == 1
    assert rows_new[0]["id"] == id2


def test_record_llm_call_and_total_cost(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.record_llm_call(
        conn,
        posting_id=posting_id,
        tier="bulk",
        model="test/model",
        purpose="score",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
    )
    db.record_llm_call(
        conn,
        posting_id=posting_id,
        tier="bulk",
        model="test/model",
        purpose="score",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.002,
    )
    assert db.total_cost_usd(conn) == pytest.approx(0.003)


def test_status_counts_and_source_counts(conn):
    id1 = db.insert_posting(conn, make_posting())
    db.insert_posting(
        conn,
        make_posting(
            canonical_url="https://djinni.co/jobs/999/", dedup_hash="hash999", source="dou"
        ),
    )
    db.update_posting_status(conn, id1, "scored")
    assert db.status_counts(conn) == {"scored": 1, "new": 1}
    assert db.source_counts(conn) == {"djinni": 1, "dou": 1}


def test_category_not_allowed_counts_by_source(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn,
        make_posting(
            canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2", source="djinni"
        ),
    )
    id3 = db.insert_posting(
        conn, make_posting(canonical_url="https://dou.ua/jobs/1/", dedup_hash="hash3", source="dou")
    )
    db.update_posting_status(conn, id1, "filtered_out", filtered_reason="category_not_allowed")
    db.update_posting_status(conn, id2, "filtered_out", filtered_reason="seniority_stopword:lead")
    db.update_posting_status(conn, id3, "filtered_out", filtered_reason="category_not_allowed")

    counts = db.category_not_allowed_counts_by_source(conn)
    assert counts == {"djinni": 1, "dou": 1}


def test_insert_posting_stores_ats_vendor(conn):
    posting_id = db.insert_posting(conn, make_posting(ats_vendor="greenhouse"))
    row = db.get_posting(conn, posting_id)
    assert row["ats_vendor"] == "greenhouse"


def test_update_posting_status_stores_filtered_reason(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.update_posting_status(
        conn, posting_id, "filtered_out", filtered_reason="category_not_allowed"
    )
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "filtered_out"
    assert row["filtered_reason"] == "category_not_allowed"


def test_update_posting_status_stores_new_scoring_fields(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.update_posting_status(
        conn,
        posting_id,
        "scored",
        tech_stack='["Python", "AWS"]',
        required_years_experience=3.5,
        seniority_level="middle",
        work_format="remote",
        country="Ukraine",
        salary_min=2000.0,
        salary_max=3000.0,
        salary_currency="USD",
        salary_period="month",
    )
    row = db.get_posting(conn, posting_id)
    assert row["tech_stack"] == '["Python", "AWS"]'
    assert row["required_years_experience"] == 3.5
    assert row["seniority_level"] == "middle"
    assert row["work_format"] == "remote"
    assert row["country"] == "Ukraine"
    assert row["salary_min"] == 2000.0
    assert row["salary_max"] == 3000.0
    assert row["salary_currency"] == "USD"
    assert row["salary_period"] == "month"


# --- Applications --------------------------------------------------------


def test_insert_and_get_application(conn):
    posting_id = db.insert_posting(conn, make_posting())
    application_id = db.insert_application(
        conn,
        db.Application(posting_id=posting_id, cv_track="qa_automation", model="quality/model"),
    )
    row = db.get_application(conn, application_id)
    assert row["posting_id"] == posting_id
    assert row["cv_track"] == "qa_automation"
    assert row["model"] == "quality/model"
    assert row["submitted_at"] is None


def test_update_application(conn):
    posting_id = db.insert_posting(conn, make_posting())
    application_id = db.insert_application(conn, db.Application(posting_id=posting_id))
    db.update_application(conn, application_id, submitted_at="2026-09-19T00:00:00+00:00")
    row = db.get_application(conn, application_id)
    assert row["submitted_at"] == "2026-09-19T00:00:00+00:00"


def test_get_applications_for_posting(conn):
    posting_id = db.insert_posting(conn, make_posting())
    other_posting_id = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/999/", dedup_hash="hash999")
    )
    db.insert_application(conn, db.Application(posting_id=posting_id))
    db.insert_application(conn, db.Application(posting_id=posting_id))
    db.insert_application(conn, db.Application(posting_id=other_posting_id))

    rows = db.get_applications_for_posting(conn, posting_id)
    assert len(rows) == 2


def test_application_status_history(conn):
    posting_id = db.insert_posting(conn, make_posting())
    application_id = db.insert_application(conn, db.Application(posting_id=posting_id))
    db.record_application_status(conn, application_id, "submitted")
    db.record_application_status(conn, application_id, "replied")

    history = db.get_application_status_history(conn, application_id)
    assert [row["status"] for row in history] == ["submitted", "replied"]


# --- Aggregates ------------------------------------------------------------


def test_missing_skills_frequency(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn, id1, "scored", missing_skills='["Docker", "Kubernetes"]'
    )
    db.update_posting_status(conn, id2, "scored", missing_skills='["Docker", "AWS"]')

    freq = db.missing_skills_frequency(conn)
    assert freq[0] == ("Docker", 2)
    assert dict(freq)["Kubernetes"] == 1
    assert dict(freq)["AWS"] == 1


def test_reply_rate_by_cv_track(conn):
    posting_id = db.insert_posting(conn, make_posting())
    app1 = db.insert_application(
        conn,
        db.Application(
            posting_id=posting_id, cv_track="qa_automation", submitted_at="2026-09-01T00:00:00"
        ),
    )
    db.update_application(conn, app1, first_reply_at="2026-09-05T00:00:00")
    # submitted but never replied -- counts toward the denominator only
    db.insert_application(
        conn,
        db.Application(
            posting_id=posting_id, cv_track="qa_automation", submitted_at="2026-09-02T00:00:00"
        ),
    )
    # never submitted at all -- must not count toward either number
    db.insert_application(conn, db.Application(posting_id=posting_id, cv_track="qa_automation"))

    rates = db.reply_rate_by(conn, "cv_track")
    assert rates == {"qa_automation": (1, 2)}


def test_reply_rate_by_rejects_unknown_dimension(conn):
    with pytest.raises(ValueError):
        db.reply_rate_by(conn, "bogus")


def test_median_salary_by_tech_tag(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn,
        id1,
        "scored",
        tech_stack='["Python"]',
        salary_min=1000.0,
        salary_max=2000.0,
        salary_currency="USD",
        salary_period="month",
    )
    db.update_posting_status(
        conn,
        id2,
        "scored",
        tech_stack='["Python"]',
        salary_min=3000.0,
        salary_max=3000.0,
        salary_currency="USD",
        salary_period="month",
    )

    by_tag = db.median_salary_by_tech_tag(conn)
    median, count = by_tag["Python"]["USD"]
    assert median == 2250.0  # median of [1500, 3000]
    assert count == 2


def test_median_salary_by_tech_tag_excludes_unknown_currency_or_period(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn, id1, "scored", tech_stack='["Python"]', salary_min=2000.0, salary_currency=None
    )
    db.update_posting_status(
        conn,
        id2,
        "scored",
        tech_stack='["Python"]',
        salary_min=2000.0,
        salary_currency="USD",
        salary_period=None,
    )

    by_tag = db.median_salary_by_tech_tag(conn)
    assert by_tag == {}


def test_median_salary_by_tech_tag_separates_currencies_and_normalizes_period(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn,
        id1,
        "scored",
        tech_stack='["Python"]',
        salary_min=1200.0,
        salary_currency="USD",
        salary_period="year",  # -> 100/month
    )
    db.update_posting_status(
        conn,
        id2,
        "scored",
        tech_stack='["Python"]',
        salary_min=50000.0,
        salary_currency="UAH",
        salary_period="month",
    )

    by_tag = db.median_salary_by_tech_tag(conn)
    assert by_tag["Python"]["USD"] == (100.0, 1)
    assert by_tag["Python"]["UAH"] == (50000.0, 1)


def test_salary_stats_by_currency_normalizes_symbol_and_period(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn, id1, "scored", salary_min=5000.0, salary_currency="$", salary_period="month"
    )
    db.update_posting_status(
        conn, id2, "scored", salary_min=6000.0, salary_currency="USD", salary_period="month"
    )

    stats = db.salary_stats_by_currency(conn)
    assert stats["USD"] == {"median_monthly": 5500.0, "count": 2}


def test_stats_by_cv_track_reports_count_and_avg_fit_score(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    db.update_posting_status(
        conn, id1, "scored", recommended_cv_track="qa_automation", fit_score=60
    )
    db.update_posting_status(
        conn, id2, "scored", recommended_cv_track="qa_automation", fit_score=80
    )

    stats = db.stats_by_cv_track(conn)
    assert stats["qa_automation"] == {"count": 2, "avg_fit_score": 70.0}


def test_required_years_experience_distribution(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    id3 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/3/", dedup_hash="hash3")
    )
    db.update_posting_status(conn, id1, "scored", required_years_experience=0.5)
    db.update_posting_status(conn, id2, "scored", required_years_experience=4)
    db.update_posting_status(conn, id3, "scored", required_years_experience=7)

    dist = db.required_years_experience_distribution(conn)
    assert dist["0-1"] == 1
    assert dist["3-5"] == 1
    assert dist["5+"] == 1
    assert dist["1-3"] == 0


def test_median_days_to_first_reply(conn):
    posting_id = db.insert_posting(conn, make_posting())
    app1 = db.insert_application(
        conn, db.Application(posting_id=posting_id, submitted_at="2026-09-01T00:00:00+00:00")
    )
    db.update_application(conn, app1, first_reply_at="2026-09-03T00:00:00+00:00")

    result = db.median_days_to_first_reply(conn)
    assert result == (2.0, 1)


def test_median_days_to_first_reply_none_when_no_data(conn):
    assert db.median_days_to_first_reply(conn) is None


# --- raw_payload / get_all_postings ---------------------------------------


def test_insert_posting_stores_raw_payload(conn):
    posting_id = db.insert_posting(conn, make_posting(raw_payload='{"title": "raw"}'))
    row = db.get_posting(conn, posting_id)
    assert row["raw_payload"] == '{"title": "raw"}'


def test_get_all_postings(conn):
    db.insert_posting(conn, make_posting())
    db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    assert len(db.get_all_postings(conn)) == 2
    assert len(db.get_all_postings(conn, limit=1)) == 1


# --- scoring_runs ----------------------------------------------------------


def test_insert_scoring_run_and_get_for_posting(conn):
    posting_id = db.insert_posting(conn, make_posting())
    run_id = db.insert_scoring_run(
        conn,
        posting_id=posting_id,
        model="test/model",
        prompt_version="scoring_v3",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        fit_score=80,
        verdict="apply",
    )
    assert run_id > 0

    runs = db.get_scoring_runs_for_posting(conn, posting_id)
    assert len(runs) == 1
    assert runs[0]["model"] == "test/model"
    assert runs[0]["prompt_version"] == "scoring_v3"
    assert runs[0]["fit_score"] == 80
    assert runs[0]["verdict"] == "apply"


def test_scoring_runs_accumulate_across_rescores(conn):
    posting_id = db.insert_posting(conn, make_posting())
    db.insert_scoring_run(
        conn, posting_id=posting_id, model="model-a", prompt_version="scoring_v2", fit_score=50
    )
    db.insert_scoring_run(
        conn, posting_id=posting_id, model="model-b", prompt_version="scoring_v3", fit_score=60
    )

    runs = db.get_scoring_runs_for_posting(conn, posting_id)
    assert len(runs) == 2
    assert [r["model"] for r in runs] == ["model-a", "model-b"]

    # inserting scoring_runs must not touch the posting's own primary columns
    row = db.get_posting(conn, posting_id)
    assert row["fit_score"] is None
    assert row["status"] == "new"


# --- tech_tags aggregates ---------------------------------------------------


def _score_with_tech_tags(conn, posting_id, tech_tags, **extra):
    db.update_posting_status(
        conn, posting_id, "scored", tech_tags=json.dumps(tech_tags), **extra
    )


def test_tech_tag_cooccurrence(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    _score_with_tech_tags(
        conn,
        id1,
        [
            {"tag": "Python", "priority": "required"},
            {"tag": "AWS", "priority": "nice_to_have"},
            {"tag": "Docker", "priority": "required"},
        ],
    )
    _score_with_tech_tags(
        conn,
        id2,
        [{"tag": "Python", "priority": "required"}, {"tag": "AWS", "priority": "required"}],
    )

    cooccurrence = db.tech_tag_cooccurrence(conn)
    pairs = dict(cooccurrence["pairs"])
    assert pairs[("AWS", "Python")] == 2
    assert pairs[("Docker", "Python")] == 1
    triples = dict(cooccurrence["triples"])
    assert triples[("AWS", "Docker", "Python")] == 1


def test_tech_tag_stats(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    _score_with_tech_tags(
        conn,
        id1,
        [{"tag": "Python", "priority": "required"}],
        salary_min=2000.0,
        salary_max=2000.0,
        required_years_experience=0.5,
    )
    _score_with_tech_tags(
        conn,
        id2,
        [{"tag": "Python", "priority": "nice_to_have"}],
        salary_min=4000.0,
        salary_max=4000.0,
        required_years_experience=5,
    )

    stats = db.tech_tag_stats(conn)
    python_stats = stats["Python"]
    assert python_stats["frequency"] == 2
    assert python_stats["required_count"] == 1
    assert python_stats["required_share"] == 0.5
    assert python_stats["median_salary"] == 3000.0
    assert python_stats["salary_count"] == 2
    assert python_stats["junior_count"] == 1
    assert python_stats["years_count"] == 2
    assert python_stats["junior_share"] == 0.5


def test_tech_tag_monthly_trend(conn):
    id1 = db.insert_posting(conn, make_posting(published_at="2026-08-15T00:00:00+00:00"))
    id2 = db.insert_posting(
        conn,
        make_posting(
            canonical_url="https://djinni.co/jobs/2/",
            dedup_hash="hash2",
            published_at="2026-09-01T00:00:00+00:00",
        ),
    )
    _score_with_tech_tags(conn, id1, [{"tag": "Python", "priority": "required"}])
    _score_with_tech_tags(conn, id2, [{"tag": "Python", "priority": "required"}])

    trend = db.tech_tag_monthly_trend(conn)
    assert trend["Python"] == {"2026-08": 1, "2026-09": 1}


def test_portfolio_expectation_stats(conn):
    id1 = db.insert_posting(conn, make_posting())
    id2 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/2/", dedup_hash="hash2")
    )
    id3 = db.insert_posting(
        conn, make_posting(canonical_url="https://djinni.co/jobs/3/", dedup_hash="hash3")
    )
    db.update_posting_status(conn, id1, "scored", portfolio_expected="github")
    db.update_posting_status(conn, id2, "scored", portfolio_expected="github")
    db.update_posting_status(conn, id3, "scored", portfolio_expected=None)

    stats = db.portfolio_expectation_stats(conn)
    assert stats["github"] == (2, 3)
    assert stats["unstated"] == (1, 3)
    assert stats["portfolio"] == (0, 3)
