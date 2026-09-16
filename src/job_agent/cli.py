"""CLI entry point (`job`). See PROJECT.md §2, §11."""

from __future__ import annotations

import asyncio
import sqlite3

import typer
from pydantic import ValidationError

from job_agent import data_guard, db, dedup, facts, prefilter, scoring, telegram_bot
from job_agent.config import Config, Secrets, load_config, load_secrets
from job_agent.db import Posting
from job_agent.llm.client import make_client
from job_agent.sources.djinni import collect_djinni
from job_agent.sources.dou import collect_dou
from job_agent.sources.linkedin import collect_linkedin
from job_agent.sources.remoteok import collect_remoteok
from job_agent.sources.wwr import collect_wwr

app = typer.Typer(add_completion=False)

DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_DB_PATH = "data/jobs.db"
DEFAULT_ENV_PATH = ".env"


def collect_all(config: Config) -> list[Posting]:
    postings: list[Posting] = []
    postings.extend(collect_djinni(config.sources.djinni))
    postings.extend(collect_dou(config.sources.dou))
    postings.extend(collect_remoteok(config.sources.remoteok))
    postings.extend(collect_wwr(config.sources.wwr))
    postings.extend(collect_linkedin(config.sources.linkedin))
    return postings


def _count_by_source(postings: list[Posting]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for posting in postings:
        counts[posting.source] = counts.get(posting.source, 0) + 1
    return counts


def run_pipeline(
    config: Config,
    secrets: Secrets,
    conn: sqlite3.Connection,
    *,
    facts_path: str = data_guard.FACTS_PATH,
) -> tuple[list[sqlite3.Row], dict[str, int]]:
    typer.echo("Collecting postings...")
    raw_postings = collect_all(config)
    source_counts_this_run = _count_by_source(raw_postings)
    typer.echo(f"Collected {len(raw_postings)} postings: {source_counts_this_run}")

    new_postings = dedup.dedup_new_postings(conn, raw_postings)
    typer.echo(f"{len(new_postings)} new after dedup.")

    inserted: list[tuple[int, Posting]] = [
        (db.insert_posting(conn, posting), posting) for posting in new_postings
    ]

    kept, _filtered_out = prefilter.prefilter_postings(
        [posting for _, posting in inserted],
        categories=config.categories,
        seniority_strict=config.active_threshold.seniority_strict,
    )
    kept_urls = {posting.canonical_url for posting in kept}
    for posting_id, posting in inserted:
        if posting.canonical_url not in kept_urls:
            db.update_posting_status(conn, posting_id, "filtered_out")

    typer.echo(f"{len(kept)} passed prefilter, scoring...")

    facts_data = facts.load_facts(facts_path)
    facts_summary = facts.render_facts_summary(facts_data)

    scored_rows: list[sqlite3.Row] = []
    with make_client(secrets.openrouter_api_key) as client:
        for posting_id, posting in inserted:
            if posting.canonical_url not in kept_urls:
                continue
            scoring.score_posting(
                conn,
                client,
                posting_id=posting_id,
                posting=posting,
                config=config,
                facts_summary=facts_summary,
            )
            row = db.get_posting(conn, posting_id)
            if row is not None and row["status"] == "scored":
                scored_rows.append(row)

    typer.echo(f"Scored {len(scored_rows)} postings.")
    return scored_rows, source_counts_this_run


async def _send_digest(
    secrets: Secrets,
    source_counts: dict[str, int],
    scored_rows: list[sqlite3.Row],
    fit_score_min: int,
) -> None:
    bot = telegram_bot.build_bot(secrets.telegram_bot_token)
    try:
        await telegram_bot.send_digest_and_cards(
            bot,
            secrets.telegram_chat_id,
            source_counts=source_counts,
            scored_rows=scored_rows,
            fit_score_min=fit_score_min,
        )
    finally:
        await bot.session.close()


async def _poll(conn: sqlite3.Connection, secrets: Secrets) -> None:
    bot = telegram_bot.build_bot(secrets.telegram_bot_token)
    dispatcher = telegram_bot.build_dispatcher(conn)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
    env_path: str = typer.Option(DEFAULT_ENV_PATH, "--env"),
    facts_path: str = typer.Option(data_guard.FACTS_PATH, "--facts"),
    answers_path: str = typer.Option(data_guard.ANSWERS_PATH, "--answers"),
) -> None:
    """Run the full pipeline once, then stay up for Telegram interaction (Ctrl+C to stop)."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        config = load_config(config_path)
        secrets = load_secrets(env_path)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValidationError as exc:
        typer.echo(f"Missing or invalid secrets in {env_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    data_results = data_guard.check_data_safety(facts_path, answers_path)
    if not all(result.ok for result in data_results):
        typer.echo("Refusing to run: candidate data is not ready.", err=True)
        typer.echo(data_guard.format_check_report(data_results), err=True)
        raise typer.Exit(code=1)

    conn = db.connect(db_path)
    db.init_db(conn)

    try:
        scored_rows, source_counts_this_run = run_pipeline(
            config, secrets, conn, facts_path=facts_path
        )
        typer.echo("Sending Telegram digest...")
        asyncio.run(
            _send_digest(
                secrets, source_counts_this_run, scored_rows, config.active_threshold.fit_score_min
            )
        )
        typer.echo("Pipeline run complete. Listening for Telegram interaction (Ctrl+C to stop)...")
        asyncio.run(_poll(conn, secrets))
    except KeyboardInterrupt:
        typer.echo("Shutting down.")
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@app.command()
def stats(db_path: str = typer.Option(DEFAULT_DB_PATH, "--db")) -> None:
    """Print pipeline/spend stats."""
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        text = telegram_bot.format_stats(
            db.status_counts(conn), db.source_counts(conn), db.total_cost_usd(conn)
        )
        typer.echo(text.replace("<b>", "").replace("</b>", ""))
    finally:
        conn.close()


@app.command(name="check-data")
def check_data(
    facts_path: str = typer.Option(data_guard.FACTS_PATH, "--facts"),
    answers_path: str = typer.Option(data_guard.ANSWERS_PATH, "--answers"),
) -> None:
    """Validate data/facts.yaml and data/answers.yaml (used by `job` before every run)."""
    results = data_guard.check_data_safety(facts_path, answers_path)
    typer.echo(data_guard.format_check_report(results))
    if not all(result.ok for result in results):
        raise typer.Exit(code=1)


@app.command(name="render-test")
def render_test() -> None:
    """CV template check -- not implemented yet (Phase 2: Typst template)."""
    typer.echo("job render-test: not implemented yet -- the Typst CV template is Phase 2 work.")


def _load_facts_summary_for_eval() -> str:
    try:
        facts_data = facts.load_facts(data_guard.FACTS_PATH)
    except FileNotFoundError:
        facts_data = facts.load_facts("data.example/facts.example.yaml")
    return facts.render_facts_summary(facts_data)


@app.command(name="eval")
def eval_command(
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    env_path: str = typer.Option(DEFAULT_ENV_PATH, "--env"),
) -> None:
    """Compare models in config.yaml -> models.eval_candidates."""
    from job_agent.eval.runner import format_eval_report, run_eval

    try:
        config = load_config(config_path)
        secrets = load_secrets(env_path)
    except (FileNotFoundError, ValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    facts_summary = _load_facts_summary_for_eval()
    reports = run_eval(config, secrets, facts_summary=facts_summary)
    typer.echo(format_eval_report(reports))


if __name__ == "__main__":
    app()
