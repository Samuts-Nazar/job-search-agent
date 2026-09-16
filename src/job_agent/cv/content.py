"""Builds the CV template's input data straight from facts.yaml -- no LLM
tailoring. Real CV tailoring (selecting/editing content per posting, per
PROJECT.md §5.6 step 1) is Phase 2 work; this exists only so the Phase 1
render/ATS-check pipeline has real content to render and test against
(§8's "content and layout are separate" split).
"""

from __future__ import annotations

from typing import Any


def build_cv_content(facts: dict[str, Any], *, track_id: str | None = None) -> dict[str, Any]:
    candidate = facts["candidate"]
    links = candidate.get("links", {})

    def in_track(item: dict[str, Any]) -> bool:
        return track_id is None or track_id in item.get("tracks", [])

    tracks = {t["id"]: t["title"] for t in facts.get("cv_tracks", [])}
    track_title = tracks.get(track_id, "Candidate") if track_id else "Candidate"

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
        "summary": f"{track_title} with hands-on experience across the projects "
        "and roles below.",
        "skills": [s["name"] for s in facts.get("skills", []) if in_track(s)],
        "projects": [
            {
                "name": p["name"],
                "dates": p.get("dates", ""),
                "summary": p.get("summary", ""),
                "bullets": p.get("metrics", []),
            }
            for p in facts.get("projects", [])
            if in_track(p)
        ],
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
        "languages": [
            {"name": lang["name"], "cefr": lang["cefr"]} for lang in facts.get("languages", [])
        ],
    }
