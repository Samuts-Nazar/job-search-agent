"""Telegram bot: run digest, per-posting cards, /pending, /stats, /mark.

See PROJECT.md §5.5, §5.8, §2. Verified live against the installed
aiogram==3.31.0: Bot/Dispatcher/Router are async; CallbackData is a
Pydantic model requiring a `prefix` class kwarg, with `.pack()`/`.filter()`
for encoding/matching callback_data.

"Prepare package" acknowledges the button press but package building itself
is Phase 2 (§5.6) and not implemented yet.
"""

from __future__ import annotations

import json
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from job_agent import db

MANUAL_STATUSES = {"replied", "rejected", "interview"}


class PostingAction(CallbackData, prefix="posting"):
    action: str  # "prepare" | "skip"
    posting_id: int


def format_digest(
    source_counts: dict[str, int],
    verdict_counts: dict[str, int],
    failed_sources: list[str] | None = None,
) -> str:
    lines = ["<b>Run digest</b>", "", "By source:"]
    for source, count in sorted(source_counts.items()):
        lines.append(f"  {source}: {count}")
    if failed_sources:
        lines.append(f"  Failed: {', '.join(sorted(failed_sources))}")
    lines.append("")
    lines.append("By verdict:")
    for verdict, count in sorted(verdict_counts.items()):
        lines.append(f"  {verdict}: {count}")
    return "\n".join(lines)


def format_posting_card(row: sqlite3.Row) -> tuple[str, InlineKeyboardMarkup]:
    matched = ", ".join(json.loads(row["matched_skills"] or "[]"))
    missing = ", ".join(json.loads(row["missing_skills"] or "[]"))

    flags = []
    if row["language_mismatch"]:
        flags.append("language mismatch")
    if row["relocation_offered"] == "yes":
        flags.append("relocation offered")
    flags_text = ", ".join(flags) or "none"

    text = (
        f"<b>{row['title']}</b>\n"
        f"ID: {row['id']}\n"
        f"{row['company'] or 'Unknown company'}\n"
        f"{row['url']}\n\n"
        f"Score: {row['fit_score']} ({row['verdict']})\n"
        f"Matched: {matched or '-'}\n"
        f"Missing: {missing or '-'}\n"
        f"Flags: {flags_text}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Prepare package",
                    callback_data=PostingAction(action="prepare", posting_id=row["id"]).pack(),
                ),
                InlineKeyboardButton(
                    text="Skip",
                    callback_data=PostingAction(action="skip", posting_id=row["id"]).pack(),
                ),
            ]
        ]
    )
    return text, keyboard


def format_stats(
    status_counts: dict[str, int], source_counts: dict[str, int], spend_usd: float
) -> str:
    lines = ["<b>Stats</b>", "", "By status:"]
    for status, count in sorted(status_counts.items()):
        lines.append(f"  {status}: {count}")
    lines.append("")
    lines.append("By source:")
    for source, count in sorted(source_counts.items()):
        lines.append(f"  {source}: {count}")
    lines.append("")
    lines.append(f"LLM spend: ${spend_usd:.4f}")
    return "\n".join(lines)


MARK_USAGE = f"Usage: /mark POSTING_ID {'|'.join(sorted(MANUAL_STATUSES))}"


def parse_mark_command(text: str) -> tuple[int, str] | str:
    """Returns (posting_id, status) on success, or an error message string.

    Every returned string is sent via bot.send_message under the bot's
    default ParseMode.HTML (see build_bot) -- literal "<...>" placeholders
    (e.g. "<id>") 400 there as an unrecognized tag, so error text must stay
    free of angle brackets."""
    parts = (text or "").split()
    if len(parts) != 3:
        return MARK_USAGE
    _, raw_id, status = parts
    if status not in MANUAL_STATUSES:
        return f"Status must be one of: {', '.join(sorted(MANUAL_STATUSES))}"
    if not raw_id.isdigit():
        return "Posting id must be a number."
    return int(raw_id), status


def mark_posting(conn: sqlite3.Connection, posting_id: int, status: str) -> str:
    if db.get_posting(conn, posting_id) is None:
        return f"No posting with id {posting_id}."
    db.update_posting_status(conn, posting_id, status)
    return f"Marked posting {posting_id} as {status}."


def skip_posting(conn: sqlite3.Connection, posting_id: int) -> None:
    db.update_posting_status(conn, posting_id, "skipped")


def get_pending_rows(conn: sqlite3.Connection, min_score: int) -> list[sqlite3.Row]:
    """Same min_score filter as the initial digest send (item 10) --
    without it /pending resends a card for every scored posting ever
    collected, which spams the chat on a DB with any real history."""
    return [
        row
        for row in db.get_postings_by_status(conn, "scored")
        if row["fit_score"] is not None and row["fit_score"] >= min_score
    ]


def build_router(conn: sqlite3.Connection, min_score: int) -> Router:
    router = Router()

    @router.message(Command("pending"))
    async def handle_pending(message: Message) -> None:
        rows = get_pending_rows(conn, min_score)
        if not rows:
            await message.answer("No postings awaiting a decision.")
            return
        for row in rows:
            text, keyboard = format_posting_card(row)
            sent = await message.answer(text, reply_markup=keyboard)
            db.insert_telegram_message(conn, posting_id=row["id"], message_id=sent.message_id)

    @router.message(Command("stats"))
    async def handle_stats(message: Message) -> None:
        text = format_stats(db.status_counts(conn), db.source_counts(conn), db.total_cost_usd(conn))
        await message.answer(text)

    @router.message(Command("mark"))
    async def handle_mark(message: Message) -> None:
        parsed = parse_mark_command(message.text or "")
        if isinstance(parsed, str):
            await message.answer(parsed)
            return
        posting_id, status = parsed
        await message.answer(mark_posting(conn, posting_id, status))

    @router.callback_query(PostingAction.filter(F.action == "skip"))
    async def handle_skip(query: CallbackQuery, callback_data: PostingAction) -> None:
        skip_posting(conn, callback_data.posting_id)
        await query.answer("Skipped.")
        if query.message:
            await query.message.edit_reply_markup(reply_markup=None)

    @router.callback_query(PostingAction.filter(F.action == "prepare"))
    async def handle_prepare(query: CallbackQuery, callback_data: PostingAction) -> None:
        await query.answer("Package preparation isn't built yet (Phase 2).", show_alert=True)

    return router


async def send_digest_and_cards(
    bot: Bot,
    chat_id: str,
    conn: sqlite3.Connection,
    *,
    source_counts: dict[str, int],
    scored_rows: list[sqlite3.Row],
    fit_score_min: int,
    failed_sources: list[str] | None = None,
) -> None:
    """fit_score_min here is config.telegram.min_score -- which cards get
    sent -- decoupled from the apply/maybe/skip thresholds used to derive
    verdict (see scoring.derive_verdict). Every sent card's message_id is
    stored so `job telegram-cleanup` can find and delete it later."""
    verdict_counts: dict[str, int] = {}
    for row in scored_rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1

    await bot.send_message(
        chat_id, format_digest(source_counts, verdict_counts, failed_sources)
    )

    for row in scored_rows:
        if row["fit_score"] is not None and row["fit_score"] < fit_score_min:
            continue
        text, keyboard = format_posting_card(row)
        sent = await bot.send_message(chat_id, text, reply_markup=keyboard)
        db.insert_telegram_message(conn, posting_id=row["id"], message_id=sent.message_id)


async def cleanup_old_cards(
    bot: Bot, chat_id: str, conn: sqlite3.Connection, *, below_score: int
) -> tuple[int, int]:
    """Deletes the bot's own card messages for postings scored under
    below_score. Telegram only allows deleting messages younger than 48
    hours; anything older fails and is counted as too-old rather than
    retried forever. Returns (deleted_count, too_old_count)."""
    deleted = 0
    too_old = 0
    for row in db.get_deletable_telegram_messages(conn, below_score=below_score):
        try:
            await bot.delete_message(chat_id, row["message_id"])
        except TelegramBadRequest:
            too_old += 1
            continue
        db.mark_telegram_message_deleted(conn, row["id"])
        deleted += 1
    return deleted, too_old


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(conn: sqlite3.Connection, min_score: int) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(conn, min_score))
    return dispatcher


async def process_pending_updates(
    bot: Bot, dispatcher: Dispatcher, *, offset: int | None
) -> tuple[int, int | None]:
    """Fetches whatever updates are waiting right now (non-blocking --
    timeout=0) and feeds each into the dispatcher once via feed_update(),
    the same handler logic `start_polling()` uses locally. Returns
    (count_processed, new_offset) -- new_offset is the last update's id +
    1, or None if there were no updates (caller should keep the previous
    offset)."""
    updates = await bot.get_updates(offset=offset, timeout=0)
    for update in updates:
        await dispatcher.feed_update(bot, update)
    if not updates:
        return 0, None
    return len(updates), updates[-1].update_id + 1
