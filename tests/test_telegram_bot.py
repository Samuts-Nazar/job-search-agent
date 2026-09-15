import json

import pytest

from job_agent import db, telegram_bot


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def insert_scored_posting(conn, **overrides) -> int:
    posting = db.Posting(
        source="djinni",
        url="https://djinni.co/jobs/1/",
        title="QA Automation Engineer",
        description="desc",
        company="Acme",
    )
    posting.canonical_url = posting.url
    posting.dedup_hash = "hash1"
    posting_id = db.insert_posting(conn, posting)
    fields = dict(
        fit_score=80,
        verdict="apply",
        matched_skills=json.dumps(["Python"]),
        missing_skills=json.dumps(["Kubernetes"]),
        language_mismatch=0,
        relocation_offered="yes",
    )
    fields.update(overrides)
    db.update_posting_status(conn, posting_id, "scored", **fields)
    return posting_id


def test_format_digest():
    text = telegram_bot.format_digest({"djinni": 3, "dou": 2}, {"apply": 1, "skip": 4})
    assert "djinni: 3" in text
    assert "dou: 2" in text
    assert "apply: 1" in text
    assert "skip: 4" in text


def test_format_posting_card_shows_flags(conn):
    insert_scored_posting(conn)
    row = db.get_postings_by_status(conn, "scored")[0]
    text, keyboard = telegram_bot.format_posting_card(row)

    assert "QA Automation Engineer" in text
    assert "Acme" in text
    assert "Score: 80 (apply)" in text
    assert "Python" in text
    assert "Kubernetes" in text
    assert "relocation offered" in text

    buttons = keyboard.inline_keyboard[0]
    assert buttons[0].text == "Prepare package"
    assert buttons[1].text == "Skip"


def test_format_posting_card_no_flags(conn):
    insert_scored_posting(conn, language_mismatch=0, relocation_offered="unknown")
    row = db.get_postings_by_status(conn, "scored")[0]
    text, _ = telegram_bot.format_posting_card(row)
    assert "Flags: none" in text


def test_format_stats():
    text = telegram_bot.format_stats({"scored": 5, "new": 2}, {"djinni": 7}, 1.2345)
    assert "scored: 5" in text
    assert "djinni: 7" in text
    assert "$1.2345" in text


def test_posting_action_pack_and_unpack():
    action = telegram_bot.PostingAction(action="skip", posting_id=42)
    packed = action.pack()
    unpacked = telegram_bot.PostingAction.unpack(packed)
    assert unpacked.action == "skip"
    assert unpacked.posting_id == 42


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/mark 1 replied", (1, "replied")),
        ("/mark 2 rejected", (2, "rejected")),
        ("/mark", "Usage: /mark <id> <replied|rejected|interview>"),
        ("/mark 1", "Usage: /mark <id> <replied|rejected|interview>"),
        ("/mark abc replied", "Posting id must be a number."),
    ],
)
def test_parse_mark_command(text, expected):
    result = telegram_bot.parse_mark_command(text)
    assert result == expected


def test_parse_mark_command_rejects_unknown_status():
    result = telegram_bot.parse_mark_command("/mark 1 bogus")
    assert result.startswith("Status must be one of:")


def test_mark_posting_unknown_id(conn):
    result = telegram_bot.mark_posting(conn, 999, "replied")
    assert result == "No posting with id 999."


def test_mark_posting_updates_status(conn):
    posting_id = insert_scored_posting(conn)
    result = telegram_bot.mark_posting(conn, posting_id, "interview")
    assert result == f"Marked posting {posting_id} as interview."
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "interview"


def test_skip_posting_updates_status(conn):
    posting_id = insert_scored_posting(conn)
    telegram_bot.skip_posting(conn, posting_id)
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "skipped"
