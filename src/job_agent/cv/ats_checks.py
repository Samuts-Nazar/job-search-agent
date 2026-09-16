"""ATS text-layer checks, run after every CV render. See PROJECT.md §5.6 step 3:
"extract the PDF text layer (pypdf or pdftotext) and assert name, email, phone
and links are present; sections appear in the expected order; key terms from
the posting appear verbatim (no ligature breakage); no missing text."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

EXPECTED_SECTIONS = [
    "Summary",
    "Technical Skills",
    "Projects",
    "Experience",
    "Education",
    "Languages",
]

# U+FFFD is pypdf's/PDF's standard stand-in for a codepoint that had no
# mapping back to a glyph; the Private Use Area is where broken ligature
# substitutions typically land when a font's `liga`/`clig` glyph has no
# proper text-extraction mapping.
_REPLACEMENT_CHAR = "�"
_PUA_RANGE = range(0xE000, 0xF8FF + 1)


@dataclass
class AtsCheckResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    extracted_text: str = ""


def extract_text(pdf_path: Path | str) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_link_uris(pdf_path: Path | str) -> list[str]:
    reader = PdfReader(str(pdf_path))
    uris = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            action = obj.get("/A")
            if action and action.get("/S") == "/URI":
                uris.append(str(action["/URI"]))
    return uris


def _check_identity_fields(text: str, content: dict[str, Any]) -> list[str]:
    problems = []
    if content["name"] not in text:
        problems.append(f"name {content['name']!r} not found in PDF text")
    if content["contact"]["email"] not in text:
        problems.append(f"email {content['contact']['email']!r} not found in PDF text")
    if content["contact"]["phone"] not in text:
        problems.append(f"phone {content['contact']['phone']!r} not found in PDF text")
    return problems


def _check_links(uris: list[str], content: dict[str, Any]) -> list[str]:
    problems = []
    email = content["contact"]["email"]
    if f"mailto:{email}" not in uris:
        problems.append(f"mailto link for {email!r} not found among PDF link annotations")
    for key in ("linkedin", "github"):
        url = content["contact"].get(key)
        if url and url not in uris:
            problems.append(f"{key} link {url!r} not found among PDF link annotations")
    return problems


def _check_section_order(text: str) -> list[str]:
    problems = []
    positions = []
    for section in EXPECTED_SECTIONS:
        idx = text.find(section)
        if idx == -1:
            problems.append(f"section {section!r} not found in PDF text")
        else:
            positions.append((section, idx))

    found_in_order = [section for section, _ in sorted(positions, key=lambda pair: pair[1])]
    expected_of_found = [s for s in EXPECTED_SECTIONS if s in found_in_order]
    if found_in_order != expected_of_found:
        problems.append(
            f"sections out of order: found {found_in_order}, expected {expected_of_found}"
        )
    return problems


def _check_no_garbled_text(text: str) -> list[str]:
    if _REPLACEMENT_CHAR in text:
        return ["PDF text contains U+FFFD (replacement character) -- likely a missing glyph"]
    for ch in text:
        if ord(ch) in _PUA_RANGE:
            return [
                f"PDF text contains a private-use-area codepoint (U+{ord(ch):04X}) "
                "-- possible ligature/font substitution issue"
            ]
    return []


def _check_required_terms(text: str, required_terms: list[str]) -> list[str]:
    return [
        f"required term {term!r} not found verbatim in PDF text"
        for term in required_terms
        if term not in text
    ]


def check_ats(
    pdf_path: Path | str, content: dict[str, Any], *, required_terms: list[str] | None = None
) -> AtsCheckResult:
    text = extract_text(pdf_path)
    uris = extract_link_uris(pdf_path)

    problems: list[str] = []
    problems += _check_identity_fields(text, content)
    problems += _check_links(uris, content)
    problems += _check_section_order(text)
    problems += _check_no_garbled_text(text)
    if required_terms:
        problems += _check_required_terms(text, required_terms)

    return AtsCheckResult(ok=not problems, problems=problems, extracted_text=text)
