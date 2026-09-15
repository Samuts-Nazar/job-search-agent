import pytest

from job_agent import db, dedup


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def make_posting(**overrides) -> db.Posting:
    defaults = dict(
        source="djinni",
        url="https://djinni.co/jobs/123-qa-engineer/",
        title="QA Engineer",
        description="We are looking for a QA Engineer to join Acme LLC. " * 5,
        company="Acme LLC",
    )
    defaults.update(overrides)
    return db.Posting(**defaults)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://Djinni.co/Jobs/123-qa/", "https://djinni.co/Jobs/123-qa"),
        ("https://djinni.co/jobs/123-qa/?utm_source=twitter", "https://djinni.co/jobs/123-qa"),
        ("https://djinni.co/jobs/123-qa#section", "https://djinni.co/jobs/123-qa"),
        ("https://djinni.co/jobs/123-qa", "https://djinni.co/jobs/123-qa"),
    ],
)
def test_canonicalize_url(url, expected):
    assert dedup.canonicalize_url(url) == expected


def test_compute_dedup_hash_ignores_legal_suffix_and_case():
    hash_a = dedup.compute_dedup_hash(
        company="Acme LLC", title="QA Engineer", description="Great QA role."
    )
    hash_b = dedup.compute_dedup_hash(
        company="ACME", title="qa engineer", description="Great QA role."
    )
    assert hash_a == hash_b


def test_compute_dedup_hash_differs_for_different_descriptions():
    hash_a = dedup.compute_dedup_hash(company="Acme", title="QA Engineer", description="Role A.")
    hash_b = dedup.compute_dedup_hash(company="Acme", title="QA Engineer", description="Role B.")
    assert hash_a != hash_b


def test_dedup_new_postings_first_run(conn):
    postings = [make_posting()]
    new = dedup.dedup_new_postings(conn, postings)
    assert len(new) == 1
    assert new[0].canonical_url == "https://djinni.co/jobs/123-qa-engineer"
    assert new[0].dedup_hash


def test_dedup_new_postings_skips_within_batch_duplicates(conn):
    postings = [
        make_posting(url="https://djinni.co/jobs/123-qa-engineer/"),
        make_posting(url="https://djinni.co/jobs/123-qa-engineer/?utm=x"),
    ]
    new = dedup.dedup_new_postings(conn, postings)
    assert len(new) == 1


def test_dedup_new_postings_skips_already_seen_in_db(conn):
    posting = make_posting()
    new = dedup.dedup_new_postings(conn, [posting])
    db.insert_posting(conn, new[0])

    again = dedup.dedup_new_postings(conn, [make_posting()])
    assert again == []


def test_dedup_new_postings_cross_source_hash_catches_repost(conn):
    posting = make_posting(source="djinni", url="https://djinni.co/jobs/123/")
    new = dedup.dedup_new_postings(conn, [posting])
    db.insert_posting(conn, new[0])

    reposted_elsewhere = make_posting(source="dou", url="https://jobs.dou.ua/vacancies/456/")
    again = dedup.dedup_new_postings(conn, [reposted_elsewhere])
    assert again == []


def test_dedup_new_postings_same_company_title_different_project_not_deduped(conn):
    posting_a = make_posting(
        url="https://djinni.co/jobs/1/",
        description="Project Alpha: backend testing for a fintech client. " * 5,
    )
    new_a = dedup.dedup_new_postings(conn, [posting_a])
    db.insert_posting(conn, new_a[0])

    posting_b = make_posting(
        url="https://djinni.co/jobs/2/",
        description="Project Beta: mobile app testing for a retail client. " * 5,
    )
    new_b = dedup.dedup_new_postings(conn, [posting_b])
    assert len(new_b) == 1
