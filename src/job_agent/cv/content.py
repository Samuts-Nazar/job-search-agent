"""Builds the CV template's input data from facts.yaml, tailored to a
posting per PROJECT.md §8. Every value comes from facts.yaml verbatim --
nothing here generates free text, so there's nothing to invent; the two
posting-driven choices (headline title, tech-tag matching) either mirror
the posting's own text or select among facts.yaml's own records. See
validate_cv_content() (§8.5) for the after-the-fact safety check.
"""

from __future__ import annotations

from typing import Any

MIN_SKILLS = 16
MAX_SKILLS = 22


def _track_skills(facts: dict[str, Any], track_id: str) -> list[dict[str, Any]]:
    return [s for s in facts.get("skills", []) if track_id in s.get("tracks", [])]


def _tag_names(posting_tech_tags: list[dict[str, str]] | None) -> list[str]:
    """Required tags first, then nice-to-have -- both lowercased for
    substring matching against facts.yaml skill names."""
    tags = sorted(posting_tech_tags or [], key=lambda t: t.get("priority") != "required")
    return [t["tag"].lower() for t in tags]


def _build_headline(
    facts: dict[str, Any],
    *,
    track_id: str,
    posting_title: str | None,
    tag_names: list[str],
) -> str:
    tracks = {t["id"]: t["title"] for t in facts.get("cv_tracks", [])}
    title = posting_title or tracks.get(track_id, "Candidate")

    track_skill_names = [s["name"] for s in _track_skills(facts, track_id)]
    matched: list[str] = []
    for tag in tag_names:
        for name in track_skill_names:
            if tag in name.lower() and name not in matched:
                matched.append(name)
                break
        if len(matched) == 3:
            break
    for name in track_skill_names:  # pad with track skills if the posting matched < 3
        if len(matched) == 3:
            break
        if name not in matched:
            matched.append(name)

    english_cefr = next(
        (lang["cefr"] for lang in facts.get("languages", []) if lang["name"] == "English"), None
    )
    english_part = f"English {english_cefr}" if english_cefr else "English"

    parts = [title]
    if matched:
        parts.append(", ".join(matched))
    parts.append(english_part)
    return " | ".join(parts)


def _build_skills(
    facts: dict[str, Any], *, track_id: str, tag_names: list[str]
) -> list[dict[str, Any]]:
    """Groups by category (PROJECT.md §8.2) as [{category, items}, ...],
    category and item order following how many of the posting's tech_tags
    each matches -- most relevant first. Total items capped at
    MAX_SKILLS, trimmed from the lowest-priority categories/items first;
    MIN_SKILLS isn't force-padded since that would mean inventing skills
    that aren't in facts.yaml."""
    by_category: dict[str, list[str]] = {}
    for skill in _track_skills(facts, track_id):
        by_category.setdefault(skill["category"], []).append(skill["name"])

    def match_count(name: str) -> int:
        lowered = name.lower()
        return sum(1 for tag in tag_names if tag in lowered)

    for names in by_category.values():
        names.sort(key=lambda name: -match_count(name))

    ordered_categories = sorted(
        by_category.items(), key=lambda kv: -sum(match_count(n) for n in kv[1])
    )

    result: list[dict[str, Any]] = []
    remaining = MAX_SKILLS
    for category, names in ordered_categories:
        if remaining <= 0:
            break
        taken = names[:remaining]
        result.append({"category": category, "items": taken})
        remaining -= len(taken)
    return result


def _build_projects(
    facts: dict[str, Any], *, track_id: str, mentions_automation: bool
) -> list[dict[str, Any]]:
    track = next((t for t in facts.get("cv_tracks", []) if t["id"] == track_id), {})
    order = list(track.get("project_order", []))
    if mentions_automation:
        for pid in track.get("growth_projects", []):
            if pid not in order:
                order.append(pid)

    projects_by_id = {p["id"]: p for p in facts.get("projects", [])}
    return [
        {
            "name": projects_by_id[pid]["name"],
            "dates": projects_by_id[pid].get("dates", ""),
            "summary": projects_by_id[pid].get("summary", ""),
            "bullets": projects_by_id[pid].get("metrics", []),
        }
        for pid in order
        if pid in projects_by_id
    ]


def build_cv_content(
    facts: dict[str, Any],
    *,
    track_id: str,
    posting_title: str | None = None,
    posting_tech_tags: list[dict[str, str]] | None = None,
    mentions_automation: bool = False,
) -> dict[str, Any]:
    """`posting_title`/`posting_tech_tags`/`mentions_automation` are the
    per-posting inputs (§8.4); everything else comes from facts.yaml
    filtered to `track_id`. Experience entries and their bullets are
    included as stored for the track (not yet re-selected per posting --
    a further per-posting bullet-relevance pass is a possible later
    refinement, not implemented here)."""
    candidate = facts["candidate"]
    links = candidate.get("links", {})
    tag_names = _tag_names(posting_tech_tags)

    def in_track(item: dict[str, Any]) -> bool:
        return track_id in item.get("tracks", [])

    return {
        "name": candidate["name"],
        "contact": {
            "city": candidate["city"],
            "country": candidate["country"],
            "timezone": candidate["timezone"],
            "email": candidate["email"],
            "phone": candidate["phone"],
            "linkedin": links.get("linkedin") or "",
            "github": links.get("github") or "",
        },
        "headline": _build_headline(
            facts, track_id=track_id, posting_title=posting_title, tag_names=tag_names
        ),
        "skills": _build_skills(facts, track_id=track_id, tag_names=tag_names),
        "projects": _build_projects(
            facts, track_id=track_id, mentions_automation=mentions_automation
        ),
        "experience": [
            {
                "title": e["title"],
                "employer": e["employer"],
                "dates": e.get("dates", ""),
                "bullets": e.get("bullets", []),
            }
            for e in facts.get("experience", [])
            if in_track(e)
        ],
        "education": [
            {
                "institution": ed["institution"],
                "degree": ed["degree"],
                "dates": ed.get("dates", ""),
            }
            for ed in facts.get("education", [])
        ],
    }


def validate_cv_content(content: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """PROJECT.md §8.5: rejects if any listed skill isn't in facts.yaml,
    any experience entry's title/employer/dates doesn't match a real
    facts.yaml entry (never renamed), any project isn't a real facts.yaml
    project, or education isn't rendered exactly as stored. Since
    build_cv_content only ever selects facts.yaml records verbatim (never
    generates free text), a failure here means a bug in that selection
    logic, not an invented fact -- this is the regression safety net
    §8.5 asks for."""
    problems: list[str] = []

    known_skill_names = {s["name"] for s in facts.get("skills", [])}
    for group in content["skills"]:
        for name in group["items"]:
            if name not in known_skill_names:
                problems.append(f"skill {name!r} not in facts.yaml")

    known_experience = {
        (e["title"], e["employer"], e.get("dates", "")) for e in facts.get("experience", [])
    }
    for entry in content["experience"]:
        key = (entry["title"], entry["employer"], entry["dates"])
        if key not in known_experience:
            problems.append(
                f"experience entry {entry['title']!r} at {entry['employer']!r} "
                "does not match any facts.yaml entry"
            )

    known_project_names = {p["name"] for p in facts.get("projects", [])}
    for project in content["projects"]:
        if project["name"] not in known_project_names:
            problems.append(f"project {project['name']!r} not in facts.yaml")

    known_education = {
        (ed["institution"], ed["degree"], ed.get("dates", "")) for ed in facts.get("education", [])
    }
    for entry in content["education"]:
        key = (entry["institution"], entry["degree"], entry["dates"])
        if key not in known_education:
            problems.append(
                f"education entry {entry['institution']!r} does not match facts.yaml exactly"
            )

    return problems
