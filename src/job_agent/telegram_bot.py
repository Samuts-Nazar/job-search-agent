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
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from job_agent import db

MANUAL_STATUSES = {"replied", "rejected", "interview"}


class PostingAction(CallbackData, prefix="posting"):
    action: str  # "prepare" | "skip"
    posting_id: int


def format_digest(source_counts: dict[str, int], verdict_counts: dict[str, int]) -> str:
    lines = ["<b>Run digest</b>", "", "By source:"]
    for source, count in sorted(source_counts.items()):
        lines.append(f"  {source}: {count}")
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


def parse_mark_command(text: str) -> tuple[int, str] | str:
    """Returns (posting_id, status) on success, or an error message string."""
    parts = (text or "").split()
    if len(parts) != 3:
        return "Usage: /mark <id> <replied|rejected|interview>"
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


def build_router(conn: sqlite3.Connection) -> Router:
    router = Router()

    @router.message(Command("pending"))
    async def handle_pending(message: Message) -> None:
        rows = db.get_postings_by_status(conn, "scored")
        if not rows:
            await message.answer("No postings awaiting a decision.")
            return
        for row in rows:
            text, keyboard = format_posting_card(row)
            await message.answer(text, reply_markup=keyboard)

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
    *,
    source_counts: dict[str, int],
    scored_rows: list[sqlite3.Row],
    fit_score_min: int,
) -> None:
    verdict_counts: dict[str, int] = {}
    for row in scored_rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1

    await bot.send_message(chat_id, format_digest(source_counts, verdict_counts))

    for row in scored_rows:
        if row["fit_score"] is not None and row["fit_score"] < fit_score_min:
            continue
        text, keyboard = format_posting_card(row)
        await bot.send_message(chat_id, text, reply_markup=keyboard)


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(conn: sqlite3.Connection) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(conn))
    return dispatcher
