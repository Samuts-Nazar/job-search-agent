import json
from dataclasses import dataclass, field

import pytest
from aiogram.exceptions import TelegramBadRequest

from job_agent import db, telegram_bot


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobs.db")
    db.init_db(connection)
    yield connection
    connection.close()


def insert_scored_posting(conn, **overrides) -> int:
    url = overrides.pop("url", "https://djinni.co/jobs/1/")
    dedup_hash = overrides.pop("dedup_hash", "hash1")
    posting = db.Posting(
        source="djinni",
        url=url,
        title="QA Automation Engineer",
        description="desc",
        company="Acme",
    )
    posting.canonical_url = posting.url
    posting.dedup_hash = dedup_hash
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
    assert "Failed" not in text


def test_format_digest_shows_failed_sources():
    text = telegram_bot.format_digest({"djinni": 3}, {"apply": 1}, failed_sources=["dou", "wwr"])
    assert "Failed: dou, wwr" in text


def test_format_posting_card_shows_flags(conn):
    posting_id = insert_scored_posting(conn)
    row = db.get_postings_by_status(conn, "scored")[0]
    text, keyboard = telegram_bot.format_posting_card(row)

    assert "QA Automation Engineer" in text
    assert f"ID: {posting_id}" in text
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
        ("/mark", telegram_bot.MARK_USAGE),
        ("/mark 1", telegram_bot.MARK_USAGE),
        ("/mark abc replied", "Posting id must be a number."),
    ],
)
def test_parse_mark_command(text, expected):
    result = telegram_bot.parse_mark_command(text)
    assert result == expected


def test_mark_usage_has_no_html_unsafe_brackets():
    """Sent via bot.send_message under the bot's default ParseMode.HTML --
    a literal "<id>" placeholder previously 400'd there as an unrecognized
    tag (confirmed live 2026-09-22)."""
    assert "<" not in telegram_bot.MARK_USAGE
    assert ">" not in telegram_bot.MARK_USAGE


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


def test_get_pending_rows_filters_by_min_score(conn):
    low_id = insert_scored_posting(conn, url="https://djinni.co/jobs/low/", fit_score=10)
    high_id = insert_scored_posting(
        conn, url="https://djinni.co/jobs/high/", fit_score=90, dedup_hash="hash2"
    )

    rows = telegram_bot.get_pending_rows(conn, min_score=40)

    ids = {row["id"] for row in rows}
    assert ids == {high_id}
    assert low_id not in ids


@dataclass
class _SentMessage:
    message_id: int


@dataclass
class _FakeBot:
    """Duck-types the subset of aiogram.Bot send_digest_and_cards/
    cleanup_old_cards actually call, so tests don't need a real token or
    network access."""

    next_message_id: int = 100
    undeletable_message_ids: set[int] = field(default_factory=set)
    sent: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))
        message = _SentMessage(message_id=self.next_message_id)
        self.next_message_id += 1
        return message

    async def delete_message(self, chat_id, message_id):
        if message_id in self.undeletable_message_ids:
            raise TelegramBadRequest(method=object(), message="message can't be deleted")
        self.deleted.append(message_id)


async def test_send_digest_and_cards_stores_message_id_and_filters_by_min_score(conn):
    low_id = insert_scored_posting(conn, url="https://djinni.co/jobs/low/", fit_score=10)
    high_id = insert_scored_posting(
        conn, url="https://djinni.co/jobs/high/", fit_score=90, dedup_hash="hash2"
    )
    rows = [db.get_posting(conn, low_id), db.get_posting(conn, high_id)]
    bot = _FakeBot()

    await telegram_bot.send_digest_and_cards(
        bot,
        "chat-1",
        conn,
        source_counts={"djinni": 2},
        scored_rows=rows,
        fit_score_min=40,
    )

    # digest + only the high-score card
    assert len(bot.sent) == 2
    assert "QA Automation Engineer" in bot.sent[1][1]

    stored = conn.execute("SELECT posting_id, message_id FROM telegram_messages").fetchall()
    assert len(stored) == 1
    assert stored[0]["posting_id"] == high_id


async def test_cleanup_old_cards_deletes_below_threshold_and_reports_too_old(conn):
    low_id = insert_scored_posting(conn, url="https://djinni.co/jobs/low/", fit_score=10)
    older_low_id = insert_scored_posting(
        conn, url="https://djinni.co/jobs/older-low/", fit_score=5, dedup_hash="hash2"
    )
    high_id = insert_scored_posting(
        conn, url="https://djinni.co/jobs/high/", fit_score=90, dedup_hash="hash3"
    )
    db.insert_telegram_message(conn, posting_id=low_id, message_id=101)
    db.insert_telegram_message(conn, posting_id=older_low_id, message_id=102)
    db.insert_telegram_message(conn, posting_id=high_id, message_id=103)

    bot = _FakeBot(undeletable_message_ids={102})

    deleted, too_old = await telegram_bot.cleanup_old_cards(
        bot, "chat-1", conn, below_score=40
    )

    assert deleted == 1
    assert too_old == 1
    assert bot.deleted == [101]

    rows = conn.execute("SELECT posting_id, deleted_at FROM telegram_messages").fetchall()
    by_posting = {row["posting_id"]: row["deleted_at"] for row in rows}
    assert by_posting[low_id] is not None
    assert by_posting[older_low_id] is None  # too old -- left for a future retry
    assert by_posting[high_id] is None  # above threshold, never attempted


async def test_cleanup_old_cards_does_not_retry_already_deleted(conn):
    posting_id = insert_scored_posting(conn, fit_score=10)
    db.insert_telegram_message(conn, posting_id=posting_id, message_id=101)
    bot = _FakeBot()

    await telegram_bot.cleanup_old_cards(bot, "chat-1", conn, below_score=40)
    deleted, too_old = await telegram_bot.cleanup_old_cards(bot, "chat-1", conn, below_score=40)

    assert deleted == 0
    assert too_old == 0
    assert bot.deleted == [101]


@dataclass
class _FakeUpdate:
    update_id: int


@dataclass
class _UpdatesBot:
    updates_to_return: list
    requested_offsets: list = field(default_factory=list)

    async def get_updates(self, offset=None, timeout=0, **kwargs):
        self.requested_offsets.append(offset)
        return self.updates_to_return


@dataclass
class _RecordingDispatcher:
    fed: list = field(default_factory=list)

    async def feed_update(self, bot, update, **kwargs):
        self.fed.append(update)


async def test_process_pending_updates_feeds_each_and_returns_new_offset():
    updates = [_FakeUpdate(update_id=10), _FakeUpdate(update_id=11)]
    bot = _UpdatesBot(updates_to_return=updates)
    dispatcher = _RecordingDispatcher()

    count, new_offset = await telegram_bot.process_pending_updates(bot, dispatcher, offset=5)

    assert bot.requested_offsets == [5]
    assert dispatcher.fed == updates
    assert count == 2
    assert new_offset == 12  # last update_id + 1


async def test_process_pending_updates_returns_none_when_nothing_pending():
    bot = _UpdatesBot(updates_to_return=[])
    dispatcher = _RecordingDispatcher()

    count, new_offset = await telegram_bot.process_pending_updates(bot, dispatcher, offset=5)

    assert count == 0
    assert new_offset is None
    assert dispatcher.fed == []
