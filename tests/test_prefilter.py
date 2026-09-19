from job_agent import db, prefilter


def make_posting(**overrides) -> db.Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/1/",
        title="QA Engineer",
        description="Join our team as a QA engineer testing web applications.",
        raw_tags=["QA"],
    )
    defaults.update(overrides)
    return db.Posting(**defaults)


def test_passes_category_allowlist_match():
    posting = make_posting(raw_tags=["QA Automation"])
    assert prefilter.passes_category_allowlist(posting, ["QA", "QA Automation", "Python"])


def test_passes_category_allowlist_no_match():
    posting = make_posting(raw_tags=["DevOps"])
    assert not prefilter.passes_category_allowlist(posting, ["QA", "Python"])


def test_passes_category_allowlist_empty_allowlist_passes_everything():
    posting = make_posting(raw_tags=["Anything"])
    assert prefilter.passes_category_allowlist(posting, [])


def test_seniority_filter_strict_rejects_senior_title():
    posting = make_posting(title="Senior QA Engineer")
    assert not prefilter.passes_seniority_filter(posting, seniority_strict=True)


def test_seniority_filter_relaxed_allows_senior_title():
    posting = make_posting(title="Senior QA Engineer")
    assert prefilter.passes_seniority_filter(posting, seniority_strict=False)


def test_seniority_filter_relaxed_still_rejects_architect():
    posting = make_posting(title="QA Architect")
    assert not prefilter.passes_seniority_filter(posting, seniority_strict=False)


def test_seniority_filter_strict_rejects_years_requirement():
    posting = make_posting(description="Requires 5+ years of experience in QA.")
    assert not prefilter.passes_seniority_filter(posting, seniority_strict=True)


def test_seniority_filter_relaxed_allows_moderate_years_requirement():
    posting = make_posting(description="Requires 5+ years of experience in QA.")
    assert prefilter.passes_seniority_filter(posting, seniority_strict=False)


def test_seniority_filter_relaxed_rejects_extreme_years_requirement():
    posting = make_posting(description="Requires 10+ years of experience in QA.")
    assert not prefilter.passes_seniority_filter(posting, seniority_strict=False)


def test_seniority_filter_passes_junior_role():
    posting = make_posting(title="Junior QA Engineer", description="No experience required.")
    assert prefilter.passes_seniority_filter(posting, seniority_strict=True)


def test_prefilter_postings_splits_kept_and_filtered_out():
    postings = [
        make_posting(url="https://djinni.co/jobs/1/", raw_tags=["QA"]),
        make_posting(
            url="https://djinni.co/jobs/2/", raw_tags=["QA"], title="Senior QA Engineer"
        ),
        make_posting(url="https://djinni.co/jobs/3/", raw_tags=["DevOps"]),
    ]
    kept, filtered_out = prefilter.prefilter_postings(
        postings, categories=["QA"], seniority_strict=True
    )
    assert [p.url for p in kept] == ["https://djinni.co/jobs/1/"]
    filtered_by_url = {p.url: reason for p, reason in filtered_out}
    assert filtered_by_url["https://djinni.co/jobs/2/"] == "seniority_stopword:senior"
    assert filtered_by_url["https://djinni.co/jobs/3/"] == "category_not_allowed"


def test_seniority_reject_reason_distinguishes_stopword_and_years():
    stopword_posting = make_posting(title="Senior QA Engineer")
    years_posting = make_posting(description="Requires 7 years of experience.")
    assert (
        prefilter._seniority_reject_reason(stopword_posting, seniority_strict=True)
        == "seniority_stopword:senior"
    )
    assert (
        prefilter._seniority_reject_reason(years_posting, seniority_strict=True)
        == "seniority_years:7"
    )
    assert (
        prefilter._seniority_reject_reason(make_posting(), seniority_strict=True) is None
    )
