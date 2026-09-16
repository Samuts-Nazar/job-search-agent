"""LinkedIn people-search URL generator. See PROJECT.md §5.6 step 5 and §12:
no scraping, no automated connecting/messaging, no login -- this only builds
a URL for the human to open in their own already-logged-in browser tab to
search LinkedIn's public people-search for recruiters/hiring roles at a
company.

URL pattern verified live 2026-09-16: an unauthenticated request to
https://www.linkedin.com/search/results/people/?keywords=... 307-redirects
to /uas/login?session_redirect=<that exact search URL>, confirming the path
and query param are current and that a logged-in browser will land on the
right results page.
"""

from __future__ import annotations

from urllib.parse import urlencode

SEARCH_BASE_URL = "https://www.linkedin.com/search/results/people/"
DEFAULT_ROLE_KEYWORDS = ["recruiter", "talent acquisition", "hiring manager"]


def build_people_search_url(company: str, *, role_keywords: list[str] | None = None) -> str:
    """LinkedIn people-search URL for recruiters/hiring roles at `company`.

    `role_keywords` defaults to DEFAULT_ROLE_KEYWORDS; pass an empty list to
    search the company name alone.
    """
    if not company or not company.strip():
        raise ValueError("company must be a non-empty name")

    roles = DEFAULT_ROLE_KEYWORDS if role_keywords is None else role_keywords
    keywords = company.strip()
    if roles:
        keywords += " " + " OR ".join(roles)

    return f"{SEARCH_BASE_URL}?{urlencode({'keywords': keywords})}"
