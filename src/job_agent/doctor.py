"""`job doctor`: one command that checks everything a real run needs and
reports pass/fail per item, so a broken setup is diagnosable in one shot
instead of failing partway through a pipeline run (a dead API key after
collection has already spent time, a stale chat id after a package was
already rendered, etc.). Every check here is read-only: no posting is
inserted, no message is sent beyond a `getChat` lookup, no config file is
written.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from shutil import which

import httpx
from aiogram.exceptions import TelegramAPIError
from jobspy import scrape_jobs

from job_agent import data_guard, db, telegram_bot
from job_agent.config import SourcesConfig
from job_agent.llm.client import make_client as make_llm_client
from job_agent.sources import djinni, dou, remoteok, wwr
from job_agent.sources.base import DEFAULT_TIMEOUT
from job_agent.sources.base import make_client as make_source_client


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str = ""


def check_data_files(
    facts_path: str = data_guard.FACTS_PATH, answers_path: str = data_guard.ANSWERS_PATH
) -> list[DoctorCheck]:
    results = data_guard.check_data_safety(facts_path, answers_path)
    checks = []
    for result in results:
        detail = "valid" if result.ok else "; ".join(result.problems)
        checks.append(DoctorCheck(name=f"data file: {result.path}", ok=result.ok, detail=detail))
    return checks


def check_binaries() -> list[DoctorCheck]:
    checks = []
    for binary in ("typst", "node"):
        path = which(binary)
        checks.append(
            DoctorCheck(
                name=f"binary: {binary}", ok=path is not None, detail=path or "not found on PATH"
            )
        )
    return checks


def check_openrouter(api_key: str) -> DoctorCheck:
    """Hits OpenRouter's /key endpoint (verified live 2026-09-23): it
    reports the key's own spend limit/usage/remaining -- this is the
    OpenRouter-side credit balance, separate from this app's own
    budget.monthly_usd spend tracking (see the monthly-spend check in
    cli.py, which reads db.total_cost_usd instead)."""
    try:
        with make_llm_client(api_key) as client:
            response = client.get("/key")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return DoctorCheck(name="OpenRouter API key", ok=False, detail=str(exc))
    data = response.json().get("data", {})
    usage = data.get("usage_monthly")
    limit = data.get("limit")
    if limit is not None:
        remaining = data.get("limit_remaining", limit - (usage or 0))
        detail = f"${usage:.2f} used this month of ${limit:.2f} key limit (${remaining:.2f} left)"
    else:
        detail = f"${usage:.2f} used this month (no limit set on this key)"
    return DoctorCheck(name="OpenRouter API key", ok=True, detail=detail)


async def check_telegram(token: str, chat_id: str) -> DoctorCheck:
    """get_me() verifies the token; get_chat() verifies the configured
    chat id is actually reachable by this bot -- distinguishing "bad
    token" from "bad chat id" (e.g. the bot was never messaged, or was
    removed from the chat) rather than reporting one opaque failure."""
    bot = telegram_bot.build_bot(token)
    try:
        try:
            me = await bot.get_me()
        except TelegramAPIError as exc:
            return DoctorCheck(name="Telegram bot", ok=False, detail=f"bot token invalid: {exc}")
        try:
            chat = await bot.get_chat(chat_id)
        except TelegramAPIError as exc:
            return DoctorCheck(
                name="Telegram bot",
                ok=False,
                detail=f"token OK (@{me.username}) but chat id {chat_id!r} unreachable: {exc}",
            )
    finally:
        await bot.session.close()
    chat_label = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat.id)
    return DoctorCheck(
        name="Telegram bot", ok=True, detail=f"@{me.username} reachable, chat {chat_label!r} OK"
    )


def _check_feed_url(name: str, url: str, *, params: dict[str, str] | None = None) -> DoctorCheck:
    try:
        with make_source_client() as client:
            response = client.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return DoctorCheck(name=f"source: {name}", ok=False, detail=str(exc))
    return DoctorCheck(name=f"source: {name}", ok=True, detail=f"{url} -> {response.status_code}")


def _check_linkedin(config: SourcesConfig) -> DoctorCheck:
    linkedin_config = config.linkedin
    if not linkedin_config.enabled:
        return DoctorCheck(name="source: linkedin", ok=True, detail="skipped (disabled in config)")
    keyword = linkedin_config.keywords[0] if linkedin_config.keywords else "QA"
    try:
        df = scrape_jobs(
            site_name="linkedin",
            search_term=keyword,
            results_wanted=1,
            linkedin_fetch_description=False,
        )
    except Exception as exc:  # jobspy's own scraper mostly swallows request errors internally
        return DoctorCheck(name="source: linkedin", ok=False, detail=str(exc))
    if len(df) == 0:
        return DoctorCheck(
            name="source: linkedin",
            ok=False,
            detail=f"guest search for {keyword!r} returned 0 results -- could be a real "
            "empty result or a silent block (jobspy logs request errors, doesn't raise them)",
        )
    return DoctorCheck(
        name="source: linkedin", ok=True, detail=f"guest endpoint reachable ({keyword!r})"
    )


def check_sources(config: SourcesConfig) -> list[DoctorCheck]:
    checks = []

    if config.djinni.enabled:
        checks.append(_check_feed_url("djinni", djinni.FEED_URL))
    else:
        checks.append(DoctorCheck(name="source: djinni", ok=True, detail="skipped (disabled)"))

    if config.dou.enabled:
        checks.append(_check_feed_url("dou", dou.FEED_URL))
    else:
        checks.append(DoctorCheck(name="source: dou", ok=True, detail="skipped (disabled)"))

    if config.remoteok.enabled:
        checks.append(_check_feed_url("remoteok", remoteok.API_URL))
    else:
        checks.append(DoctorCheck(name="source: remoteok", ok=True, detail="skipped (disabled)"))

    if config.wwr.enabled:
        slug = next(iter(config.wwr.categories), None) or next(
            iter(config.wwr.keyword_categories), None
        )
        if slug is None:
            checks.append(
                DoctorCheck(name="source: wwr", ok=True, detail="skipped (no category configured)")
            )
        else:
            checks.append(_check_feed_url("wwr", wwr.FEED_URL_TEMPLATE.format(slug=slug)))
    else:
        checks.append(DoctorCheck(name="source: wwr", ok=True, detail="skipped (disabled)"))

    checks.append(_check_linkedin(config))

    checks.append(
        DoctorCheck(name="source: nofluffjobs", ok=True, detail="skipped (not implemented yet)")
    )

    return checks


def check_db_schema(conn: sqlite3.Connection) -> DoctorCheck:
    table_count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0]
    if table_count == 0:
        return DoctorCheck(
            name="DB schema",
            ok=True,
            detail="not initialized yet -- created automatically on the first `job` run",
        )
    diffs = db.schema_diff(conn)
    if not diffs:
        return DoctorCheck(name="DB schema", ok=True, detail="up to date")
    lines = []
    has_missing = False
    for table, (missing, extra) in sorted(diffs.items()):
        if missing:
            has_missing = True
            lines.append(f"{table}: missing columns {sorted(missing)}")
        if extra:
            lines.append(f"{table}: extra columns {sorted(extra)} (harmless)")
    return DoctorCheck(name="DB schema", ok=not has_missing, detail="; ".join(lines))


def format_doctor_report(checks: list[DoctorCheck]) -> str:
    lines = []
    for check in checks:
        status = "OK" if check.ok else "FAILED"
        lines.append(f"[{status}] {check.name}: {check.detail}")
    passed = sum(1 for check in checks if check.ok)
    lines.append(f"\n{passed}/{len(checks)} checks passed.")
    return "\n".join(lines)
