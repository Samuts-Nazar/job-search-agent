"""CLI entry point (`job`). See PROJECT.md §2, §11."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import typer
from pydantic import ValidationError

from job_agent import (
    ats_vendor,
    data_guard,
    db,
    deadman,
    dedup,
    facts,
    prefilter,
    release_assets,
    scoring,
    stats_report,
    telegram_bot,
)
from job_agent import export as export_module
from job_agent.config import Config, Secrets, SourcesConfig, load_config, load_secrets
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
DEFAULT_OFFSET_PATH = "data/telegram_offset.txt"
DEFAULT_ALERT_FLAG_PATH = "data/telegram_stale_alert.flag"

# Set by the GitHub Actions workflows (never locally): public-repo Actions
# logs are world-readable, so under JOB_CI=1 an exception's own message --
# which could embed a file path, an HTTP response body, or other content
# from the private data repo -- is never printed, only its type name.
CI_MODE = os.environ.get("JOB_CI") == "1"


def _echo_error(exc: Exception, *, prefix: str = "") -> None:
    message = type(exc).__name__ if CI_MODE else str(exc)
    typer.echo(f"{prefix}{message}", err=True)


def collect_all(
    config: Config, conn: sqlite3.Connection | None = None
) -> tuple[list[Posting], list[str]]:
    """Each source is isolated: one failing (e.g. a source blocking a
    datacenter IP a CI runner uses) must not abort collection from the
    other four. Returns (postings, failed_source_names)."""
    postings: list[Posting] = []
    failed: list[str] = []
    sources: list[tuple[str, Callable[[], list[Posting]]]] = [
        ("djinni", lambda: collect_djinni(config.sources.djinni, conn=conn)),
        ("dou", lambda: collect_dou(config.sources.dou)),
        ("remoteok", lambda: collect_remoteok(config.sources.remoteok)),
        ("wwr", lambda: collect_wwr(config.sources.wwr)),
        ("linkedin", lambda: collect_linkedin(config.sources.linkedin)),
    ]
    for name, collect in sources:
        try:
            postings.extend(collect())
        except Exception as exc:  # noqa: BLE001 -- isolate each source deliberately
            typer.echo(f"{name} collection failed: {type(exc).__name__}", err=True)
            failed.append(name)
    return postings, failed


def _count_by_source(postings: list[Posting]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for posting in postings:
        counts[posting.source] = counts.get(posting.source, 0) + 1
    return counts


def _posting_from_row(row: sqlite3.Row) -> Posting:
    """Reconstructs a Posting from a stored postings row -- used by
    `job analyze --rescore` to re-score from the raw text/payload already
    on disk, without re-collecting from the source."""
    return Posting(
        source=row["source"],
        url=row["url"],
        title=row["title"],
        description=row["description"],
        company=row["company"],
        location=row["location"],
        remote_type=row["remote_type"],
        published_at=row["published_at"],
        salary_raw=row["salary_raw"],
        raw_tags=json.loads(row["raw_tags"] or "[]"),
        raw_payload=row["raw_payload"],
        ats_vendor=row["ats_vendor"],
        canonical_url=row["canonical_url"],
        dedup_hash=row["dedup_hash"],
    )


def run_pipeline(
    config: Config,
    secrets: Secrets,
    conn: sqlite3.Connection,
    *,
    facts_path: str = data_guard.FACTS_PATH,
) -> tuple[list[sqlite3.Row], dict[str, int], list[str]]:
    started_at = db.now_iso()
    typer.echo("Collecting postings...")
    raw_postings, failed_sources = collect_all(config, conn)
    source_counts_this_run = _count_by_source(raw_postings)
    typer.echo(f"Collected {len(raw_postings)} postings: {source_counts_this_run}")

    new_postings = dedup.dedup_new_postings(conn, raw_postings)
    typer.echo(f"{len(new_postings)} new after dedup.")

    for posting in new_postings:
        posting.ats_vendor = ats_vendor.detect_ats_vendor(posting.url)

    inserted: list[tuple[int, Posting]] = [
        (db.insert_posting(conn, posting), posting) for posting in new_postings
    ]

    kept, filtered_out = prefilter.prefilter_postings(
        [posting for _, posting in inserted],
        categories=config.categories,
        seniority_strict=config.active_threshold.seniority_strict,
    )
    kept_urls = {posting.canonical_url for posting in kept}
    filtered_reasons = {posting.canonical_url: reason for posting, reason in filtered_out}
    for posting_id, posting in inserted:
        reason = filtered_reasons.get(posting.canonical_url)
        if reason is not None:
            db.update_posting_status(conn, posting_id, "filtered_out", filtered_reason=reason)

    typer.echo(f"{len(kept)} passed prefilter, scoring...")

    facts_data = facts.load_facts(facts_path)
    facts_summary = facts.render_facts_summary(facts_data)

    scored_rows: list[sqlite3.Row] = []
    reasoning_overrides: dict[str, str | None] = {}
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
                reasoning_overrides=reasoning_overrides,
            )
            row = db.get_posting(conn, posting_id)
            if row is not None and row["status"] == "scored":
                scored_rows.append(row)

    typer.echo(f"Scored {len(scored_rows)} postings.")

    db.insert_pipeline_run(
        conn,
        kind="run",
        started_at=started_at,
        finished_at=db.now_iso(),
        collected_count=len(raw_postings),
        new_count=len(new_postings),
        filtered_out_count=len(filtered_out),
        scored_count=len(scored_rows),
        score_failed_count=len(kept) - len(scored_rows),
        source_counts=source_counts_this_run,
        cost_usd=db.total_cost_usd(conn, since_iso=started_at),
    )
    return scored_rows, source_counts_this_run, failed_sources


async def _send_digest(
    secrets: Secrets,
    conn: sqlite3.Connection,
    source_counts: dict[str, int],
    scored_rows: list[sqlite3.Row],
    fit_score_min: int,
    failed_sources: list[str] | None = None,
) -> None:
    bot = telegram_bot.build_bot(secrets.telegram_bot_token)
    try:
        await telegram_bot.send_digest_and_cards(
            bot,
            secrets.telegram_chat_id,
            conn,
            source_counts=source_counts,
            scored_rows=scored_rows,
            fit_score_min=fit_score_min,
            failed_sources=failed_sources,
        )
    finally:
        await bot.session.close()


async def _poll(
    conn: sqlite3.Connection,
    secrets: Secrets,
    min_score: int,
    *,
    facts_data: dict | None = None,
    output_dir: Path = telegram_bot.DEFAULT_PACKAGE_OUTPUT_DIR,
    sources_config: SourcesConfig | None = None,
) -> None:
    bot = telegram_bot.build_bot(secrets.telegram_bot_token)
    dispatcher = telegram_bot.build_dispatcher(
        conn,
        min_score,
        facts_data=facts_data,
        output_dir=output_dir,
        sources_config=sources_config,
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


async def _telegram_cleanup(
    secrets: Secrets, conn: sqlite3.Connection, below_score: int
) -> tuple[int, int]:
    bot = telegram_bot.build_bot(secrets.telegram_bot_token)
    try:
        return await telegram_bot.cleanup_old_cards(
            bot, secrets.telegram_chat_id, conn, below_score=below_score
        )
    finally:
        await bot.session.close()


def _read_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return int(text) if text else None


def _write_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset), encoding="utf-8")


async def _trigger_pipeline_workflow() -> None:
    """Dispatches pipeline.yml's workflow_dispatch event via the GitHub
    API, in response to a /run command -- see telegram_bot.build_router's
    on_run. Only meaningful inside the GitHub Actions deployment: GITHUB_
    TOKEN and GITHUB_REPOSITORY are provided automatically there, so this
    is a silent no-op when running `job` locally."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    ) as client:
        response = await client.post(
            f"/repos/{repo}/actions/workflows/pipeline.yml/dispatches",
            json={"ref": os.environ.get("GITHUB_REF_NAME", "main")},
        )
        response.raise_for_status()


async def _telegram_process(
    conn: sqlite3.Connection,
    secrets: Secrets,
    config: Config,
    *,
    offset_path: Path,
    alert_flag_path: Path,
    stale_threshold_hours: float,
    facts_data: dict | None,
    output_dir: Path,
) -> int:
    """One-shot: process whatever Telegram updates are waiting, persist the
    new offset, then check the secondary stale-pipeline signal. Returns
    the number of updates processed."""
    bot = telegram_bot.build_bot(secrets.telegram_bot_token)
    try:
        dispatcher = telegram_bot.build_dispatcher(
            conn,
            config.telegram.min_score,
            on_run=_trigger_pipeline_workflow,
            facts_data=facts_data,
            output_dir=output_dir,
            sources_config=config.sources,
        )
        offset = _read_offset(offset_path)
        processed, new_offset = await telegram_bot.process_pending_updates(
            bot, dispatcher, offset=offset
        )
        if new_offset is not None:
            _write_offset(offset_path, new_offset)

        alert = deadman.check_pipeline_staleness(
            conn, alert_flag_path=alert_flag_path, threshold_hours=stale_threshold_hours
        )
        if alert:
            await bot.send_message(secrets.telegram_chat_id, alert)
        return processed
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
    no_telegram: bool = typer.Option(
        False, "--no-telegram", help="Collect and score only -- send or poll nothing."
    ),
    no_poll: bool = typer.Option(
        False,
        "--no-poll",
        help="Send the digest but don't poll for Telegram interaction afterward "
        "(for when something else, e.g. a separate scheduled process, handles it).",
    ),
) -> None:
    """Run the full pipeline once, then stay up for Telegram interaction (Ctrl+C to stop)."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        config = load_config(config_path)
        secrets = load_secrets(env_path)
    except FileNotFoundError as exc:
        _echo_error(exc)
        raise typer.Exit(code=1) from exc
    except ValidationError as exc:
        _echo_error(exc, prefix=f"Missing or invalid secrets in {env_path}: ")
        raise typer.Exit(code=1) from exc

    data_results = data_guard.check_data_safety(facts_path, answers_path)
    if not all(result.ok for result in data_results):
        typer.echo("Refusing to run: candidate data is not ready.", err=True)
        typer.echo(data_guard.format_check_report(data_results), err=True)
        raise typer.Exit(code=1)

    conn = db.connect(db_path)
    db.init_db(conn)

    try:
        scored_rows, source_counts_this_run, failed_sources = run_pipeline(
            config, secrets, conn, facts_path=facts_path
        )
        if no_telegram:
            typer.echo("--no-telegram set: pipeline run complete, nothing sent.")
        else:
            typer.echo("Sending Telegram digest...")
            asyncio.run(
                _send_digest(
                    secrets,
                    conn,
                    source_counts_this_run,
                    scored_rows,
                    config.telegram.min_score,
                    failed_sources,
                )
            )
            if no_poll:
                typer.echo("--no-poll set: digest sent, not polling.")
            else:
                typer.echo(
                    "Pipeline run complete. Listening for Telegram interaction "
                    "(Ctrl+C to stop)..."
                )
                facts_data = facts.load_facts(facts_path)
                asyncio.run(
                    _poll(
                        conn,
                        secrets,
                        config.telegram.min_score,
                        facts_data=facts_data,
                        sources_config=config.sources,
                    )
                )
    except KeyboardInterrupt:
        typer.echo("Shutting down.")
    except FileNotFoundError as exc:
        _echo_error(exc)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@app.command()
def stats(db_path: str = typer.Option(DEFAULT_DB_PATH, "--db")) -> None:
    """Print pipeline/spend stats, plus extended aggregates (skills, reply rate, salary, etc.)."""
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        text = telegram_bot.format_stats(
            db.status_counts(conn), db.source_counts(conn), db.total_cost_usd(conn)
        )
        typer.echo(text.replace("<b>", "").replace("</b>", ""))
        typer.echo(stats_report.format_extended_stats(conn))
    finally:
        conn.close()


@app.command()
def export(
    out_dir: str = typer.Option("output/export", "--out-dir"),
    fmt: str = typer.Option("csv", "--format"),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
) -> None:
    """Export postings, scores, and applications for analysis outside the tool."""
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        counts = export_module.export_all(conn, out_dir, fmt=fmt)
    except ValueError as exc:
        _echo_error(exc)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    for name, count in counts.items():
        typer.echo(f"{name}: {count} rows -> {out_dir}/{name}.{fmt}")


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
def render_test(
    output_path: str = typer.Option("output/render-test-cv.pdf", "--output"),
    track: str = typer.Option("qa_automation", "--track"),
) -> None:
    """Render the CV template against data.example content and check the output.

    Runs the content validation (PROJECT.md §8.5) and ATS text-layer
    checks on every render, plus the OpenResume parser (report only)
    since this checks the template itself.
    """
    from job_agent.cv.ats_checks import check_ats
    from job_agent.cv.content import build_cv_content, validate_cv_content
    from job_agent.cv.openresume_check import OpenResumeCheckError, run_openresume_parser
    from job_agent.cv.render import RenderError, render_cv

    facts_data = facts.load_facts("data.example/facts.example.yaml")
    content = build_cv_content(facts_data, track_id=track)

    content_problems = validate_cv_content(content, facts_data)
    if content_problems:
        typer.echo("Content validation FAILED -- refusing to render:", err=True)
        for problem in content_problems:
            typer.echo(f"  - {problem}", err=True)
        raise typer.Exit(code=1)

    try:
        pdf_path = render_cv(content, output_path)
    except RenderError as exc:
        _echo_error(exc)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Rendered {pdf_path}")

    ats_result = check_ats(pdf_path, content)
    typer.echo(f"ATS checks: {'OK' if ats_result.ok else 'FAILED'}")
    for problem in ats_result.problems:
        typer.echo(f"  - {problem}")

    try:
        resume = run_openresume_parser(pdf_path)
        typer.echo("OpenResume parser extracted fields (informational, not a pass/fail gate):")
        typer.echo(json.dumps(resume, indent=2, ensure_ascii=False))
    except OpenResumeCheckError as exc:
        typer.echo(f"OpenResume parser check skipped: {exc}", err=True)

    if not ats_result.ok:
        raise typer.Exit(code=1)


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
        _echo_error(exc)
        raise typer.Exit(code=1) from exc

    facts_summary = _load_facts_summary_for_eval()
    reports = run_eval(config, secrets, facts_summary=facts_summary)
    typer.echo(format_eval_report(reports))


def _readmit_newly_passing(
    conn: sqlite3.Connection,
    client: httpx.Client,
    config: Config,
    facts_summary: str,
    reasoning_overrides: dict[str, str | None],
) -> tuple[int, int, int]:
    """Re-checks every filtered_out posting against the current prefilter
    (e.g. after a prefilter bug fix) and scores any that now pass, as a
    primary result -- unlike --rescore, this promotes the posting's actual
    status instead of only appending scoring_runs history.

    Returns (examined_count, admitted_count, scored_count)."""
    filtered_rows = db.get_postings_by_status(conn, "filtered_out")
    postings = [_posting_from_row(row) for row in filtered_rows]
    kept, _ = prefilter.prefilter_postings(
        postings,
        categories=config.categories,
        seniority_strict=config.active_threshold.seniority_strict,
    )
    kept_urls = {posting.canonical_url for posting in kept}
    admitted_rows = [row for row in filtered_rows if row["canonical_url"] in kept_urls]

    scored = 0
    for row in admitted_rows:
        posting = _posting_from_row(row)
        result = scoring.score_posting(
            conn,
            client,
            posting_id=row["id"],
            posting=posting,
            config=config,
            facts_summary=facts_summary,
            reasoning_overrides=reasoning_overrides,
            record_as_primary=True,
        )
        # score_posting already set status to scored/score_failed; this
        # just clears the now-stale filtered_reason left over from before.
        status = "scored" if result is not None else "score_failed"
        db.update_posting_status(conn, row["id"], status, filtered_reason=None)
        if result is not None:
            scored += 1
    return len(filtered_rows), len(admitted_rows), scored


@app.command()
def analyze(
    rescore: bool = typer.Option(False, "--rescore"),
    readmit: bool = typer.Option(
        False,
        "--readmit",
        help="Re-check filtered-out postings against the current prefilter and "
        "score any that now pass, as primary results (e.g. after a prefilter fix).",
    ),
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    env_path: str = typer.Option(DEFAULT_ENV_PATH, "--env"),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
    facts_path: str = typer.Option(data_guard.FACTS_PATH, "--facts"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    """--rescore: re-run scoring over every stored posting (including
    previously filtered-out ones) with the current model and prompt
    version, appending a new scoring_runs row per posting without touching
    the posting's own primary result -- safe to run repeatedly.

    --readmit: re-check filtered-out postings against the current
    prefilter and score any that now pass, as primary results. The two can
    be combined; --readmit runs first.
    """
    if not rescore and not readmit:
        typer.echo("Nothing to do -- pass --rescore or --readmit.", err=True)
        raise typer.Exit(code=1)

    try:
        config = load_config(config_path)
        secrets = load_secrets(env_path)
    except FileNotFoundError as exc:
        _echo_error(exc)
        raise typer.Exit(code=1) from exc
    except ValidationError as exc:
        _echo_error(exc, prefix=f"Missing or invalid secrets in {env_path}: ")
        raise typer.Exit(code=1) from exc

    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        facts_data = facts.load_facts(facts_path)
        facts_summary = facts.render_facts_summary(facts_data)
        reasoning_overrides: dict[str, str | None] = {}

        if readmit:
            with make_client(secrets.openrouter_api_key) as client:
                examined, admitted, scored = _readmit_newly_passing(
                    conn, client, config, facts_summary, reasoning_overrides
                )
            typer.echo(
                f"{admitted}/{examined} filtered-out postings now pass the prefilter; "
                f"scored {scored}/{admitted} of them."
            )

        if rescore:
            rows = db.get_all_postings(conn, limit=limit)
            typer.echo(f"Re-scoring {len(rows)} stored postings...")

            rescored = 0
            with make_client(secrets.openrouter_api_key) as client:
                for row in rows:
                    posting = _posting_from_row(row)
                    result = scoring.score_posting(
                        conn,
                        client,
                        posting_id=row["id"],
                        posting=posting,
                        config=config,
                        facts_summary=facts_summary,
                        reasoning_overrides=reasoning_overrides,
                        record_as_primary=False,
                    )
                    if result is not None:
                        rescored += 1
            typer.echo(f"Re-scored {rescored}/{len(rows)} postings (new scoring_runs rows).")
    finally:
        conn.close()


@app.command(name="telegram-cleanup")
def telegram_cleanup(
    below: int = typer.Option(..., "--below", help="Delete cards for postings scored under this."),
    env_path: str = typer.Option(DEFAULT_ENV_PATH, "--env"),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
) -> None:
    """Delete the bot's own Telegram cards for postings scored under --below.

    Telegram only allows deleting messages younger than 48 hours -- anything
    older is reported separately rather than retried.
    """
    try:
        secrets = load_secrets(env_path)
    except ValidationError as exc:
        _echo_error(exc, prefix=f"Missing or invalid secrets in {env_path}: ")
        raise typer.Exit(code=1) from exc

    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        deleted, too_old = asyncio.run(_telegram_cleanup(secrets, conn, below))
    finally:
        conn.close()
    typer.echo(f"Deleted {deleted} messages, {too_old} too old to delete (>48h).")


@app.command(name="telegram-process")
def telegram_process(
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    env_path: str = typer.Option(DEFAULT_ENV_PATH, "--env"),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
    facts_path: str = typer.Option(data_guard.FACTS_PATH, "--facts"),
    output_dir: str = typer.Option(
        str(telegram_bot.DEFAULT_PACKAGE_OUTPUT_DIR), "--output-dir"
    ),
    offset_path: str = typer.Option(DEFAULT_OFFSET_PATH, "--offset-file"),
    alert_flag_path: str = typer.Option(DEFAULT_ALERT_FLAG_PATH, "--alert-flag-file"),
    stale_threshold_hours: float = typer.Option(
        deadman.DEFAULT_STALE_THRESHOLD_HOURS, "--stale-threshold-hours"
    ),
) -> None:
    """Process whatever Telegram updates are waiting right now, once, and
    exit -- for a scheduled process (e.g. a GitHub Actions workflow run
    every 15 min) instead of `job`'s local infinite start_polling() loop.
    Same /pending, /stats, /mark, button, and package handlers either way;
    only the update-delivery mechanism differs. Also runs the secondary
    (in-Actions) stale-pipeline check -- see job_agent/deadman.py.
    """
    try:
        config = load_config(config_path)
        secrets = load_secrets(env_path)
    except FileNotFoundError as exc:
        _echo_error(exc)
        raise typer.Exit(code=1) from exc
    except ValidationError as exc:
        _echo_error(exc, prefix=f"Missing or invalid secrets in {env_path}: ")
        raise typer.Exit(code=1) from exc

    try:
        facts_data = facts.load_facts(facts_path)
    except FileNotFoundError:
        facts_data = None  # /pending, /stats, /mark still work; package prep declines

    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        processed = asyncio.run(
            _telegram_process(
                conn,
                secrets,
                config,
                offset_path=Path(offset_path),
                alert_flag_path=Path(alert_flag_path),
                stale_threshold_hours=stale_threshold_hours,
                facts_data=facts_data,
                output_dir=Path(output_dir),
            )
        )
    finally:
        conn.close()
    typer.echo(f"Processed {processed} update(s).")


@app.command(name="db-pull")
def db_pull(
    repo: str = typer.Option(..., "--repo", help="owner/repo of the private data repo."),
    tag: str = typer.Option("jobs-db", "--tag", help="Release tag jobs.db is stored under."),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
) -> None:
    """Download the newest jobs.db release asset from --repo (see the
    GitHub Actions deployment plan, amendment 4). Reads DATA_REPO_TOKEN
    from the environment. Leaves --db untouched if no asset exists yet.
    """
    token = os.environ.get("DATA_REPO_TOKEN")
    if not token:
        typer.echo("DATA_REPO_TOKEN is not set.", err=True)
        raise typer.Exit(code=1)

    client = release_assets.make_client(token)
    try:
        found = release_assets.download_latest(client, repo, tag, Path(db_path))
    finally:
        client.close()
    typer.echo(f"jobs.db {'downloaded' if found else 'not found yet -- starting fresh'}.")


@app.command(name="db-push")
def db_push(
    repo: str = typer.Option(..., "--repo", help="owner/repo of the private data repo."),
    tag: str = typer.Option("jobs-db", "--tag", help="Release tag jobs.db is stored under."),
    db_path: str = typer.Option(DEFAULT_DB_PATH, "--db"),
    keep: int = typer.Option(release_assets.KEEP_VERSIONS, "--keep"),
) -> None:
    """Upload --db as a new dated release asset on --repo, pruning to the
    newest --keep versions. Reads DATA_REPO_TOKEN from the environment.
    """
    token = os.environ.get("DATA_REPO_TOKEN")
    if not token:
        typer.echo("DATA_REPO_TOKEN is not set.", err=True)
        raise typer.Exit(code=1)

    client = release_assets.make_client(token)
    try:
        name = release_assets.upload_and_rotate(client, repo, tag, Path(db_path), keep=keep)
    finally:
        client.close()
    typer.echo(f"Uploaded {name} (keeping newest {keep}).")


if __name__ == "__main__":
    app()
