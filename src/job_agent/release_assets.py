"""GitHub Release asset storage for jobs.db.

See the GitHub Actions deployment plan, amendment 4: committing a
constantly-changing SQLite file to git history grows the private data
repo without bound (SQLite binaries compress/delta poorly). Assets don't
have this problem -- each upload is just stored, no history to
accumulate -- so jobs.db lives there instead, versioned by upload
timestamp in the asset filename (jobs-<timestamp>.db) so multiple can
coexist on one release; only the newest KEEP_VERSIONS are kept as
rotating backups.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

API_BASE_URL = "https://api.github.com"
KEEP_VERSIONS = 7
ASSET_NAME_RE = re.compile(r"^jobs-\d{8}T\d{6}Z\.db$")


def make_client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=60.0,
    )


def get_or_create_release(client: httpx.Client, repo: str, tag: str) -> dict:
    response = client.get(f"/repos/{repo}/releases/tags/{tag}")
    if response.status_code == 200:
        return response.json()
    response = client.post(
        f"/repos/{repo}/releases",
        json={"tag_name": tag, "name": tag, "draft": False, "prerelease": True},
    )
    response.raise_for_status()
    return response.json()


def list_db_assets(release: dict) -> list[dict]:
    """jobs-<timestamp>.db assets on `release`, newest first (the
    timestamp format sorts lexicographically = chronologically)."""
    assets = [a for a in release.get("assets", []) if ASSET_NAME_RE.match(a["name"])]
    return sorted(assets, key=lambda a: a["name"], reverse=True)


def download_latest(client: httpx.Client, repo: str, tag: str, dest: Path) -> bool:
    """Downloads the newest jobs.db asset to `dest`. Returns False (dest
    left untouched) if no asset exists yet -- e.g. the very first run."""
    release = get_or_create_release(client, repo, tag)
    assets = list_db_assets(release)
    if not assets:
        return False
    response = client.get(
        assets[0]["url"], headers={"Accept": "application/octet-stream"}, follow_redirects=True
    )
    response.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)
    return True


def upload_and_rotate(
    client: httpx.Client, repo: str, tag: str, src: Path, *, keep: int = KEEP_VERSIONS
) -> str:
    """Uploads `src` as a new dated asset, then deletes all but the newest
    `keep` jobs-*.db assets. Returns the new asset's name."""
    release = get_or_create_release(client, repo, tag)
    name = f"jobs-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
    upload_url = release["upload_url"].split("{")[0]  # strip the templated {?name,label} suffix
    response = client.post(
        upload_url,
        params={"name": name},
        headers={"Content-Type": "application/octet-stream"},
        content=src.read_bytes(),
    )
    response.raise_for_status()

    # re-fetch: the release object above predates this upload
    release = get_or_create_release(client, repo, tag)
    for stale in list_db_assets(release)[keep:]:
        del_response = client.delete(f"/repos/{repo}/releases/assets/{stale['id']}")
        del_response.raise_for_status()
    return name
