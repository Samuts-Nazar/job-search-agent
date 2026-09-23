"""Detects the ATS vendor behind a posting's apply/company URL, from the URL
alone -- no network request. See PROJECT.md §5.7 for the "known ATS" list
this is meant to eventually route form-filling to (Workable, Greenhouse,
Lever, Teamtailor first); for now this only labels postings for later
analysis (`job stats`, `job export`), nothing acts on the label yet.

`resolve_apply_destination` below is a second, network-using step: for
Djinni and DOU, `posting.url` is the source's own domain, never the real
employer/ATS URL, so `detect_ats_vendor(posting.url)` alone can never
detect anything for them -- confirmed live 2026-09-23 against every
currently above-threshold posting (all 598 stored postings have
ats_vendor=None). It fetches the posting's own page and follows the
source-specific "real apply link" pattern verified live that same day.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

# Ordered roughly by how common each is in practice. Domain suffixes are
# matched against the URL's hostname; startswith the vendor's subdomain
# convention also matches (e.g. "acme.recruitee.com").
_VENDOR_DOMAINS: list[tuple[str, tuple[str, ...]]] = [
    ("greenhouse", ("greenhouse.io",)),
    ("lever", ("lever.co",)),
    ("workable", ("workable.com",)),
    ("teamtailor", ("teamtailor.com",)),
    ("bamboohr", ("bamboohr.com",)),
    ("smartrecruiters", ("smartrecruiters.com",)),
    ("ashby", ("ashbyhq.com",)),
    ("recruitee", ("recruitee.com",)),
    ("personio", ("personio.de", "personio.com")),
    ("jazzhr", ("applytojob.com", "jazz.co")),
    ("icims", ("icims.com",)),
    ("workday", ("myworkdayjobs.com",)),
]


def detect_ats_vendor(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url).netloc.lower()
    if not host:
        return None
    for vendor, domains in _VENDOR_DOMAINS:
        for domain in domains:
            if host == domain or host.endswith(f".{domain}"):
                return vendor
    return None


# DOU's own outbound-apply redirect wrapper (verified live 2026-09-23
# against real above-threshold postings: rel="nofollow" href, one 302/301
# hop to the real ATS -- e.g. job-boards.eu.greenhouse.io). Present only
# on postings that link out; postings using DOU's own internal apply form
# instead carry id="relogin-link" and no goto link at all.
_DOU_GOTO_RE = re.compile(r'href="(https://dou\.ua/goto/vacancy/\?id=\d+)"')

# Djinni's anonymous-apply form marker (verified live 2026-09-23 against
# 15 real above-threshold postings, 11/15 present): its presence means
# the application happens on Djinni itself, never via an external ATS.
_DJINNI_ANON_APPLY_MARKER = "anon_apply"

# RemoteOK's own outbound-apply redirect wrapper.
_REMOTEOK_REDIRECT_RE = re.compile(r'href="(/l/\d+[^"]*)"')


def resolve_apply_destination(client: httpx.Client, *, source: str, posting_url: str) -> str | None:
    """Best-effort: fetches `posting_url` and returns the real external
    apply URL if one can be found, else None (which covers both "this
    posting uses the source's own internal apply form" and "no pattern
    matched" -- both are the honest "no external ATS detected" answer,
    not distinguished further). Only djinni/dou/remoteok have a pattern
    verified against real data (see the constants above); other sources
    fall through to None. Never raises on a network/parse failure -- a
    stats-only enrichment step shouldn't be able to break anything it's
    called from.
    """
    try:
        response = client.get(posting_url, follow_redirects=True, timeout=15.0)
        response.raise_for_status()
        html = response.text
    except httpx.HTTPError:
        return None

    if source == "dou":
        match = _DOU_GOTO_RE.search(html)
        if not match:
            return None  # either DOU's own internal form, or no pattern matched
        try:
            redirected = client.get(match.group(1), follow_redirects=True, timeout=15.0)
            redirected.raise_for_status()
        except httpx.HTTPError:
            return None
        return str(redirected.url)

    if source == "djinni":
        if _DJINNI_ANON_APPLY_MARKER in html:
            return None  # Djinni's own internal form, not an external ATS
        return None  # no external-link pattern verified yet for the remainder

    if source == "remoteok":
        match = _REMOTEOK_REDIRECT_RE.search(html)
        if not match:
            return None
        try:
            redirect_url = httpx.URL(posting_url).join(match.group(1))
            redirected = client.get(str(redirect_url), follow_redirects=True, timeout=15.0)
            redirected.raise_for_status()
        except httpx.HTTPError:
            return None
        final_url = str(redirected.url)
        # RemoteOK's redirect loops back to its own listing page for
        # requests it doesn't consider a real browser (verified live
        # 2026-09-23) -- that's not a resolved destination, just a bounce.
        if urlsplit(final_url).netloc.lower().endswith("remoteok.com"):
            return None
        return final_url

    return None  # wwr/linkedin/other: no pattern verified against real data yet
