"""Extended `job stats` aggregates -- CLI-only, additive analysis on top of
the base status/source/spend numbers (still in telegram_bot.format_stats,
shared with the /stats bot command). Every percentage is shown with its
underlying counts so small samples stay visible.
"""

from __future__ import annotations

import sqlite3

from job_agent import db


def format_extended_stats(conn: sqlite3.Connection) -> str:
    lines = ["", "Extended stats:", ""]

    lines.append("Application funnel (from postings.status; cumulative per stage):")
    funnel = db.application_funnel(conn)
    for stage in db.FUNNEL_STAGES:
        lines.append(f"  {stage}: {funnel[stage]}")
    lines.append("  Conversion:")
    for step, (reached, prior) in db.funnel_conversion_rates(funnel).items():
        pct = f"{reached / prior:.0%}" if prior else "0%"
        lines.append(f"    {step}: {pct} ({reached}/{prior})")
    lines.append("  Outcome breakdown (of replied):")
    status_totals = db.status_counts(conn)
    for status in ("replied", "rejected", "interview"):
        lines.append(f"    {status}: {status_totals.get(status, 0)}")
    lines.append("")

    for dimension in ("cv_track", "source"):
        lines.append(f"Funnel reply rate by {dimension} (from postings.status):")
        funnel_rates = db.funnel_reply_rate_by(conn, dimension)
        if not funnel_rates:
            lines.append("  (no submitted postings yet)")
        for group, (replied, total) in sorted(funnel_rates.items()):
            pct = f"{replied / total:.0%}" if total else "0%"
            lines.append(f"  {group}: {pct} ({replied}/{total})")
        lines.append("")

    lines.append("Postings rejected by the category allowlist, per source:")
    category_rejects = db.category_not_allowed_counts_by_source(conn)
    if not category_rejects:
        lines.append("  (none rejected on category yet)")
    for source, count in sorted(category_rejects.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {source}: {count}")
    lines.append("")

    lines.append("Recent pipeline runs (most recent first):")
    runs = db.get_recent_pipeline_runs(conn, limit=10)
    if not runs:
        lines.append("  (no runs recorded yet)")
    for run in runs:
        lines.append(
            f"  [{run['id']}] {run['kind']} {run['started_at']} -> {run['finished_at']}: "
            f"collected={run['collected_count']}, new={run['new_count']}, "
            f"filtered_out={run['filtered_out_count']}, scored={run['scored_count']}, "
            f"score_failed={run['score_failed_count']}, cost=${run['cost_usd']:.4f}"
        )
    lines.append("")

    lines.append("Most frequent missing skills (top 20):")
    skill_counts = db.missing_skills_frequency(conn, limit=20)
    if not skill_counts:
        lines.append("  (no scored postings yet)")
    for skill, count in skill_counts:
        lines.append(f"  {skill}: {count}")
    lines.append("")

    for dimension in ("cv_track", "source", "country"):
        lines.append(f"Reply rate by {dimension}:")
        rates = db.reply_rate_by(conn, dimension)
        if not rates:
            lines.append("  (no submitted applications yet)")
        for group, (replied, total) in sorted(rates.items()):
            pct = f"{replied / total:.0%}" if total else "0%"
            lines.append(f"  {group}: {pct} ({replied}/{total})")
        lines.append("")

    lines.append("Median monthly-equivalent salary by currency (not converted across currencies):")
    salary_by_currency = db.salary_stats_by_currency(conn)
    if not salary_by_currency:
        lines.append("  (no salary data with a known currency+period yet)")
    for currency, stat in sorted(salary_by_currency.items()):
        lines.append(f"  {currency}: {stat['median_monthly']:.0f}/month (n={stat['count']})")
    lines.append("")

    lines.append("Median monthly-equivalent salary by tech tag, per currency:")
    salary_by_tag = db.median_salary_by_tech_tag(conn)
    if not salary_by_tag:
        lines.append("  (no salary data with a known currency+period yet)")
    tag_totals = {
        tag: sum(n for _, n in by_currency.values()) for tag, by_currency in salary_by_tag.items()
    }
    for tag in sorted(salary_by_tag, key=lambda t: -tag_totals[t]):
        parts = ", ".join(
            f"{currency}={median:.0f}/mo (n={count})"
            for currency, (median, count) in sorted(salary_by_tag[tag].items())
        )
        lines.append(f"  {tag}: {parts}")
    lines.append("")

    lines.append("Per CV track (scored postings): count, avg fit_score:")
    track_stats = db.stats_by_cv_track(conn)
    if not track_stats:
        lines.append("  (no scored postings yet)")
    for track, stat in sorted(track_stats.items(), key=lambda kv: -kv[1]["count"]):
        lines.append(f"  {track}: n={stat['count']}, avg_fit_score={stat['avg_fit_score']:.1f}")
    lines.append("")

    lines.append("Required years of experience distribution:")
    years_dist = db.required_years_experience_distribution(conn)
    total_years = sum(years_dist.values())
    if not total_years:
        lines.append("  (no data yet)")
    else:
        for bucket, count in years_dist.items():
            lines.append(f"  {bucket}: {count / total_years:.0%} ({count}/{total_years})")
    lines.append("")

    lines.append("Median time to first reply:")
    reply_time = db.median_days_to_first_reply(conn)
    if reply_time is None:
        lines.append("  (no replies recorded yet)")
    else:
        median_days, count = reply_time
        lines.append(f"  {median_days:.1f} days (n={count})")
    lines.append("")

    lines.append("Tech-tag co-occurrence (top 10 pairs / triples):")
    cooccurrence = db.tech_tag_cooccurrence(conn, top_n=10)
    if not cooccurrence["pairs"] and not cooccurrence["triples"]:
        lines.append("  (no tech_tags data yet)")
    else:
        lines.append("  Pairs:")
        for tags, count in cooccurrence["pairs"]:
            lines.append(f"    {' + '.join(tags)}: {count}")
        lines.append("  Triples:")
        for tags, count in cooccurrence["triples"]:
            lines.append(f"    {' + '.join(tags)}: {count}")
    lines.append("")

    lines.append("Per tech tag (frequency, required share, median salary, 0-1y share):")
    tag_stats = db.tech_tag_stats(conn)
    if not tag_stats:
        lines.append("  (no tech_tags data yet)")
    for tag, stat in sorted(tag_stats.items(), key=lambda kv: -kv[1]["frequency"]):
        salary_text = (
            f"{stat['median_salary']:.0f} (n={stat['salary_count']})"
            if stat["median_salary"] is not None
            else "no salary data"
        )
        req_n, req_d = stat["required_count"], stat["frequency"]
        required_text = f"{stat['required_share']:.0%} ({req_n}/{req_d})"
        jun_n, jun_d = stat["junior_count"], stat["years_count"]
        junior_text = f"{stat['junior_share']:.0%} ({jun_n}/{jun_d})"
        lines.append(
            f"  {tag}: frequency={stat['frequency']}, required={required_text}, "
            f"median_salary={salary_text}, 0-1y_experience={junior_text}"
        )
    lines.append("")

    lines.append("Monthly posting trend for the 5 most frequent tech tags:")
    trend = db.tech_tag_monthly_trend(conn)
    if not trend:
        lines.append("  (no tech_tags data yet)")
    else:
        top_tags = sorted(trend, key=lambda t: -sum(trend[t].values()))[:5]
        for tag in top_tags:
            months = ", ".join(f"{month}: {count}" for month, count in sorted(trend[tag].items()))
            lines.append(f"  {tag}: {months}")
    lines.append("")

    lines.append("Share of scored postings expecting GitHub / portfolio / test task:")
    portfolio_stats = db.portfolio_expectation_stats(conn)
    total_scored = next(iter(portfolio_stats.values()), (0, 0))[1]
    if not total_scored:
        lines.append("  (no scored postings yet)")
    else:
        for key, (count, total) in portfolio_stats.items():
            pct = f"{count / total:.0%}" if total else "0%"
            lines.append(f"  {key}: {pct} ({count}/{total})")

    return "\n".join(lines)
