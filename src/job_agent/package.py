"""Package assembly: CV render + ATS check + LinkedIn outreach draft, sent
to Telegram for approval (PROJECT.md §5.6). Cover-letter generation
(§5.6 step 4) is intentionally NOT included -- on hold pending a model
choice. The outreach draft message is a short, deterministic template
built from facts.yaml + the posting, not an LLM call, so it doesn't
carry the same "which model" question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from job_agent.cv.ats_checks import AtsCheckResult, check_ats, validate_pdf_tokens
from job_agent.cv.content import build_cv_content, validate_cv_content
from job_agent.cv.render import RenderError, render_cv
from job_agent.outreach import build_people_search_url

AUTOMATION_KEYWORDS = [
    "automation",
    "automate",
    "automated",
    "автоматизац",
    "автоматизува",
]

MAX_OUTREACH_MESSAGE_CHARS = 300


class PackageError(Exception):
    pass


def mentions_automation(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in AUTOMATION_KEYWORDS)


def _is_ukrainian(language: str) -> bool:
    return language.strip().lower().startswith("ukrain")


def build_outreach_message(
    facts: dict[str, Any], *, posting_title: str, company: str, language: str = "English"
) -> str:
    """Short, deterministic template (<=300 chars) following the same
    writing rules as prompts/outreach_v1.md (facts.yaml only, no dashes,
    no banned phrases, language-matched, formal greeting, no Ukrainian
    sign-off) -- not LLM-generated, so this doesn't need a model choice
    the way the (on-hold) cover letter does. `language` should be the
    posting's own language (e.g. from its required_languages); defaults
    to English. Every value comes from facts.yaml or the posting."""
    candidate_name = facts["candidate"]["name"]

    if _is_ukrainian(language):
        role_part = (
            f"позицію {posting_title} у {company}" if company else f"позицію {posting_title}"
        )
        message = (
            f"Доброго дня\n\n"
            f"Подаюся на {role_part}. Готовий поділитися деталями про свій досвід. "
            f"{candidate_name}"
        )
    else:
        role_part = f"{posting_title} at {company}" if company else posting_title
        message = (
            f"Dear Hiring Team,\n\n"
            f"I'm applying for {role_part}. Happy to share more about my background. "
            f"Best, {candidate_name}"
        )

    if len(message) > MAX_OUTREACH_MESSAGE_CHARS:
        message = message[: MAX_OUTREACH_MESSAGE_CHARS - 1].rstrip() + "…"
    return message


@dataclass
class Package:
    pdf_path: Path
    outreach_url: str
    outreach_message: str
    ats_result: AtsCheckResult
    content: dict[str, Any] = field(default_factory=dict)


def build_package(
    facts: dict[str, Any],
    *,
    track_id: str,
    posting_title: str,
    company: str,
    description: str,
    posting_tech_tags: list[dict[str, str]] | None,
    output_path: Path | str,
    language: str = "English",
) -> Package:
    """Raises PackageError if content validation (§8.5) fails, the
    rendered PDF fails its ATS text-layer checks, or the PDF text
    contains a token not traceable to facts.yaml/the posting title
    (§5.6 step 3's "assert... no missing text", checked end to end, not
    just at the content-dict level). Raises RenderError if the Typst
    compile itself fails."""
    content = build_cv_content(
        facts,
        track_id=track_id,
        posting_title=posting_title,
        posting_tech_tags=posting_tech_tags,
        mentions_automation=mentions_automation(f"{posting_title}\n{description}"),
    )
    content_problems = validate_cv_content(content, facts)
    if content_problems:
        raise PackageError("Content validation failed: " + "; ".join(content_problems))

    pdf_path = render_cv(content, output_path)
    ats_result = check_ats(pdf_path, content)
    token_problems = validate_pdf_tokens(
        ats_result.extracted_text, facts, posting_title=posting_title
    )
    if not ats_result.ok or token_problems:
        all_problems = ats_result.problems + token_problems
        raise PackageError("ATS check failed: " + "; ".join(all_problems))

    outreach_url = build_people_search_url(company) if company else ""
    outreach_message = build_outreach_message(
        facts, posting_title=posting_title, company=company, language=language
    )

    return Package(
        pdf_path=pdf_path,
        outreach_url=outreach_url,
        outreach_message=outreach_message,
        ats_result=ats_result,
        content=content,
    )


__all__ = [
    "Package",
    "PackageError",
    "RenderError",
    "build_outreach_message",
    "build_package",
    "mentions_automation",
]
