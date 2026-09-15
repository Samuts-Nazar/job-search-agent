"""Minimal facts-registry reader for LLM scoring context (PROJECT.md §5.4).

This intentionally only renders data/facts.yaml into a plain-text summary
for the scoring prompt. The full fact-checked registry that constrains
CV/cover-letter generation (per-bullet metric enforcement, etc.) is Phase 2
work (PROJECT.md §11.2).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_facts(path: Path | str = "data/facts.yaml") -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy data.example/facts.example.yaml to {path} and edit it."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def render_facts_summary(facts: dict) -> str:
    lines: list[str] = []

    tracks = {t["id"]: t["title"] for t in facts.get("cv_tracks", [])}
    lines.append("CV tracks: " + ", ".join(f"{tid} ({title})" for tid, title in tracks.items()))

    lines.append("\nSkills:")
    for skill in facts.get("skills", []):
        track_ids = ", ".join(skill.get("tracks", []))
        lines.append(f"- {skill['name']} ({track_ids})")

    lines.append("\nProjects:")
    for project in facts.get("projects", []):
        track_ids = ", ".join(project.get("tracks", []))
        metrics = "; ".join(project.get("metrics", []))
        summary = project.get("summary", "")
        lines.append(f"- {project['name']} ({track_ids}): {summary} {metrics}".strip())

    lines.append("\nExperience:")
    for job in facts.get("experience", []):
        track_ids = ", ".join(job.get("tracks", []))
        lines.append(f"- {job['title']} at {job['employer']} ({job.get('dates', '')}, {track_ids})")
        for bullet in job.get("bullets", []):
            lines.append(f"  - {bullet}")

    lines.append("\nLanguages:")
    for lang in facts.get("languages", []):
        lines.append(f"- {lang['name']}: {lang['cefr']}")

    return "\n".join(lines)
