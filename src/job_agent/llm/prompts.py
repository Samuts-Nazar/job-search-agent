"""Loads versioned prompt templates from the repo-root prompts/ directory.

See PROJECT.md §6: "Prompts live in versioned files, not inline strings."
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from job_agent.db import Posting

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

# The version currently used for live scoring -- stored alongside every
# scoring_runs row (db.py) so results stay attributable to the prompt that
# produced them even after this constant moves on.
SCORING_PROMPT_VERSION = "scoring_v3"


def load_prompt_template(name: str) -> Template:
    path = PROMPTS_DIR / f"{name}.md"
    return Template(path.read_text(encoding="utf-8"))


def render_scoring_prompt(*, facts_summary: str, posting: Posting) -> str:
    template = load_prompt_template(SCORING_PROMPT_VERSION)
    return template.substitute(
        facts_summary=facts_summary,
        source=posting.source,
        title=posting.title,
        company=posting.company or "unknown",
        location=posting.location or "unknown",
        remote_type=posting.remote_type or "unknown",
        salary_raw=posting.salary_raw or "not stated",
        description=posting.description,
    )
