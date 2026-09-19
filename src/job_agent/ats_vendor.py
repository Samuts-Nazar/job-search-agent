"""Detects the ATS vendor behind a posting's apply/company URL, from the URL
alone -- no network request. See PROJECT.md §5.7 for the "known ATS" list
this is meant to eventually route form-filling to (Workable, Greenhouse,
Lever, Teamtailor first); for now this only labels postings for later
analysis (`job stats`, `job export`), nothing acts on the label yet.
"""

from __future__ import annotations

from urllib.parse import urlsplit

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
