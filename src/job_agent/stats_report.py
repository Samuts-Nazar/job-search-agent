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

    lines.append("Median salary by tech tag:")
    salary_by_tag = db.median_salary_by_tech_tag(conn)
    if not salary_by_tag:
        lines.append("  (no salary data yet)")
    for tag, (median, count) in sorted(salary_by_tag.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"  {tag}: {median:.0f} (n={count})")
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

    return "\n".join(lines)
