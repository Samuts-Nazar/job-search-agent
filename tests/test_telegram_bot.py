import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest

from job_agent import db, telegram_bot
from job_agent.config import SourcesConfig


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


def _get_handler(router, name, observer_name="message"):
    observer = getattr(router, observer_name)
    for handler in observer.handlers:
        if handler.callback.__name__ == name:
            return handler.callback
    raise AssertionError(f"no handler named {name!r} on router.{observer_name}")


@dataclass
class _FakeMessage:
    answered: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    markup_cleared: bool = False

    async def answer(self, text, reply_markup=None):
        self.answered.append(text)

    async def answer_document(self, document, caption=None, reply_markup=None):
        self.documents.append((document, caption))

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_cleared = True


@dataclass
class _FakeCallbackQuery:
    message: _FakeMessage
    answered: list = field(default_factory=list)

    async def answer(self, text=None, show_alert=False):
        self.answered.append((text, show_alert))


async def test_run_command_calls_on_run_and_acks(conn):
    calls = []

    async def on_run():
        calls.append(1)

    router = telegram_bot.build_router(conn, 40, on_run=on_run)
    handle_run = _get_handler(router, "handle_run")
    message = _FakeMessage()

    await handle_run(message)

    assert calls == [1]
    assert message.answered == ["Triggering a pipeline run."]


async def test_run_command_without_on_run_just_acks(conn):
    router = telegram_bot.build_router(conn, 40)
    handle_run = _get_handler(router, "handle_run")
    message = _FakeMessage()

    await handle_run(message)  # must not raise with on_run=None

    assert message.answered == ["Triggering a pipeline run."]


def _fake_package(pdf_path="output/packages/fake.pdf"):
    from job_agent.cv.ats_checks import AtsCheckResult
    from job_agent.package import Package

    return Package(
        pdf_path=Path(pdf_path),
        outreach_url="https://www.linkedin.com/search/results/people/?keywords=Acme",
        outreach_message="Hi! Draft outreach message.",
        ats_result=AtsCheckResult(ok=True),
        content={},
    )


async def test_prepare_without_facts_data_declines(conn):
    posting_id = insert_scored_posting(conn)
    router = telegram_bot.build_router(conn, 40, facts_data=None)
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert query.answered[0][1] is True  # show_alert
    assert message.documents == []


async def test_prepare_builds_and_sends_package(conn, monkeypatch):
    posting_id = insert_scored_posting(conn)
    pkg = _fake_package()
    monkeypatch.setattr(telegram_bot.package, "build_package", lambda *a, **k: pkg)

    router = telegram_bot.build_router(conn, 40, facts_data={"candidate": {"name": "X"}})
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert len(message.documents) == 1
    assert any("LinkedIn search" in text for text in message.answered)
    assert any("Cover letter not included" in text for text in message.answered)
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "package_ready"


async def test_prepare_passes_ukrainian_language_when_required(conn, monkeypatch):
    posting_id = insert_scored_posting(
        conn, required_languages=json.dumps(["Ukrainian"])
    )
    pkg = _fake_package()
    calls = []

    def fake_build_package(facts_data, **kwargs):
        calls.append(kwargs)
        return pkg

    monkeypatch.setattr(telegram_bot.package, "build_package", fake_build_package)
    router = telegram_bot.build_router(conn, 40, facts_data={"candidate": {"name": "X"}})
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert calls[0]["language"] == "Ukrainian"


async def test_prepare_defaults_to_english_language(conn, monkeypatch):
    posting_id = insert_scored_posting(conn, required_languages=json.dumps(["English"]))
    pkg = _fake_package()
    calls = []

    def fake_build_package(facts_data, **kwargs):
        calls.append(kwargs)
        return pkg

    monkeypatch.setattr(telegram_bot.package, "build_package", fake_build_package)
    router = telegram_bot.build_router(conn, 40, facts_data={"candidate": {"name": "X"}})
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert calls[0]["language"] == "English"


async def test_prepare_reports_package_error_without_changing_status(conn, monkeypatch):
    posting_id = insert_scored_posting(conn)

    def fake_build_package(*a, **k):
        raise telegram_bot.package.PackageError("skill not in facts.yaml")

    monkeypatch.setattr(telegram_bot.package, "build_package", fake_build_package)

    router = telegram_bot.build_router(conn, 40, facts_data={"candidate": {"name": "X"}})
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert message.documents == []
    assert any("Package preparation failed" in text for text in message.answered)
    row = db.get_posting(conn, posting_id)
    assert row["status"] == "scored"


def _fake_manual_package(
    checklist=None, apply_url="https://jobs.dou.ua/companies/acme/vacancies/1/"
):
    from job_agent.cv.ats_checks import AtsCheckResult
    from job_agent.package import Package

    return Package(
        pdf_path=Path("output/packages/fake.pdf"),
        outreach_url="",
        outreach_message="Message to paste.",
        ats_result=AtsCheckResult(ok=True),
        content={},
        apply_policy="manual",
        apply_url=apply_url,
        checklist=checklist or ["Step one.", "Step two."],
    )


def test_package_keyboard_manual_policy_shows_mark_as_submitted():
    keyboard = telegram_bot._package_keyboard(1, apply_policy="manual")
    button_texts = [button.text for button in keyboard.inline_keyboard[0]]
    assert button_texts == ["Mark as submitted", "Regenerate", "Skip"]


def test_package_keyboard_automated_policy_shows_approve():
    keyboard = telegram_bot._package_keyboard(1, apply_policy="automated")
    button_texts = [button.text for button in keyboard.inline_keyboard[0]]
    assert button_texts == ["Approve", "Regenerate", "Skip"]


async def test_prepare_sends_manual_apply_bundle_for_manual_policy_source(conn, monkeypatch):
    posting_id = insert_scored_posting(conn)  # source="djinni" by default -> manual policy
    pkg = _fake_manual_package()
    calls = []

    def fake_build_package(facts_data, **kwargs):
        calls.append(kwargs)
        return pkg

    monkeypatch.setattr(telegram_bot.package, "build_package", fake_build_package)
    router = telegram_bot.build_router(
        conn,
        40,
        facts_data={"candidate": {"name": "X"}},
        sources_config=SourcesConfig(),
    )
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert calls[0]["source"] == "djinni"
    assert calls[0]["apply_policy"] == "manual"
    assert calls[0]["apply_url"] == "https://djinni.co/jobs/1/"
    assert any("Apply link:" in text for text in message.answered)
    assert any("Checklist:" in text for text in message.answered)
    assert any("Message to paste:" in text for text in message.answered)
    assert not any("LinkedIn search" in text for text in message.answered)


async def test_prepare_without_sources_config_defaults_to_automated(conn, monkeypatch):
    posting_id = insert_scored_posting(conn)
    pkg = _fake_package()
    calls = []

    def fake_build_package(facts_data, **kwargs):
        calls.append(kwargs)
        return pkg

    monkeypatch.setattr(telegram_bot.package, "build_package", fake_build_package)
    router = telegram_bot.build_router(conn, 40, facts_data={"candidate": {"name": "X"}})
    handle_prepare = _get_handler(router, "handle_prepare", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_prepare(query, telegram_bot.PostingAction(action="prepare", posting_id=posting_id))

    assert calls[0]["apply_policy"] == "automated"


async def test_package_mark_submitted_updates_status_and_clears_markup(conn):
    posting_id = insert_scored_posting(conn)
    router = telegram_bot.build_router(conn, 40)
    handle_mark_submitted = _get_handler(
        router, "handle_package_mark_submitted", "callback_query"
    )
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_mark_submitted(
        query, telegram_bot.PackageAction(action="mark_submitted", posting_id=posting_id)
    )

    row = db.get_posting(conn, posting_id)
    assert row["status"] == "submitted"
    assert message.markup_cleared is True
    assert query.answered == [("Marked as submitted.", False)]


async def test_package_approve_acknowledges(conn):
    posting_id = insert_scored_posting(conn)
    router = telegram_bot.build_router(conn, 40)
    handle_approve = _get_handler(router, "handle_package_approve", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_approve(query, telegram_bot.PackageAction(action="approve", posting_id=posting_id))

    assert query.answered == [("Approved.", False)]
    assert any("approved" in text.lower() for text in message.answered)


async def test_package_regenerate_rebuilds_package(conn, monkeypatch):
    posting_id = insert_scored_posting(conn)
    pkg = _fake_package()
    monkeypatch.setattr(telegram_bot.package, "build_package", lambda *a, **k: pkg)

    router = telegram_bot.build_router(conn, 40, facts_data={"candidate": {"name": "X"}})
    handle_regenerate = _get_handler(router, "handle_package_regenerate", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_regenerate(
        query, telegram_bot.PackageAction(action="regenerate", posting_id=posting_id)
    )

    assert len(message.documents) == 1


async def test_package_skip_marks_skipped_and_clears_markup(conn):
    posting_id = insert_scored_posting(conn)
    router = telegram_bot.build_router(conn, 40)
    handle_skip = _get_handler(router, "handle_package_skip", "callback_query")
    message = _FakeMessage()
    query = _FakeCallbackQuery(message=message)

    await handle_skip(query, telegram_bot.PackageAction(action="skip", posting_id=posting_id))

    row = db.get_posting(conn, posting_id)
    assert row["status"] == "skipped"
    assert message.markup_cleared is True


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
