import httpx
import pytest

from job_agent import release_assets

REPO = "owner/data-repo"
TAG = "jobs-db"


class FakeGitHubReleases:
    """Minimal in-memory stand-in for the subset of the GitHub Releases
    API release_assets.py calls: get/create a release by tag, upload an
    asset, list/download/delete assets."""

    def __init__(self):
        self.release_id = 1
        self.assets: dict[int, dict] = {}
        self._next_asset_id = 100
        self.created = False

    def _release_json(self) -> dict:
        return {
            "id": self.release_id,
            "tag_name": TAG,
            "upload_url": "https://uploads.github.com/repos/owner/data-repo/releases/1/assets{?name,label}",
            "assets": [
                {
                    "id": asset_id,
                    "name": asset["name"],
                    "url": f"https://api.github.com/repos/{REPO}/releases/assets/{asset_id}",
                }
                for asset_id, asset in self.assets.items()
            ],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if method == "GET" and path == f"/repos/{REPO}/releases/tags/{TAG}":
            if not self.created:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=self._release_json())

        if method == "POST" and path == f"/repos/{REPO}/releases":
            self.created = True
            return httpx.Response(201, json=self._release_json())

        if method == "POST" and path == "/repos/owner/data-repo/releases/1/assets":
            name = request.url.params["name"]
            asset_id = self._next_asset_id
            self._next_asset_id += 1
            self.assets[asset_id] = {"name": name, "content": request.content}
            return httpx.Response(201, json={"id": asset_id, "name": name})

        if method == "GET" and path.startswith(f"/repos/{REPO}/releases/assets/"):
            asset_id = int(path.rsplit("/", 1)[-1])
            return httpx.Response(200, content=self.assets[asset_id]["content"])

        if method == "DELETE" and path.startswith(f"/repos/{REPO}/releases/assets/"):
            asset_id = int(path.rsplit("/", 1)[-1])
            del self.assets[asset_id]
            return httpx.Response(204)

        raise AssertionError(f"unexpected request: {method} {path}")


@pytest.fixture
def fake_github():
    return FakeGitHubReleases()


@pytest.fixture
def client(fake_github):
    transport = httpx.MockTransport(fake_github.handler)
    return httpx.Client(base_url=release_assets.API_BASE_URL, transport=transport)


def test_download_latest_returns_false_when_no_release_exists(client, tmp_path):
    dest = tmp_path / "jobs.db"
    found = release_assets.download_latest(client, REPO, TAG, dest)
    assert found is False
    assert not dest.exists()


def test_upload_then_download_roundtrip(client, tmp_path, fake_github):
    src = tmp_path / "jobs.db"
    src.write_bytes(b"sqlite-bytes-v1")

    name = release_assets.upload_and_rotate(client, REPO, TAG, src)
    assert name.startswith("jobs-") and name.endswith(".db")

    dest = tmp_path / "downloaded.db"
    found = release_assets.download_latest(client, REPO, TAG, dest)
    assert found is True
    assert dest.read_bytes() == b"sqlite-bytes-v1"


def test_upload_and_rotate_keeps_only_newest_n(client, tmp_path, fake_github, monkeypatch):
    src = tmp_path / "jobs.db"

    # deterministic, strictly increasing timestamps -- one real-time call
    # could collide (second-level precision) if the loop runs fast enough
    import datetime as datetime_module

    counter = iter(range(8))

    class _FakeDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime_module.datetime(2026, 1, 1, 0, 0, next(counter), tzinfo=tz)

    monkeypatch.setattr(release_assets, "datetime", _FakeDatetime)

    names = []
    for i in range(8):
        src.write_bytes(f"version-{i}".encode())
        names.append(release_assets.upload_and_rotate(client, REPO, TAG, src, keep=7))

    assert len(fake_github.assets) == 7
    remaining_names = {a["name"] for a in fake_github.assets.values()}
    # the 7 newest (i=1..7) survive; the oldest (i=0) was pruned
    assert names[0] not in remaining_names
    assert all(n in remaining_names for n in names[1:])


def test_list_db_assets_ignores_non_matching_names():
    release = {
        "assets": [
            {"name": "jobs-20260101T000000Z.db", "url": "x", "id": 1},
            {"name": "README.md", "url": "y", "id": 2},
            {"name": "jobs-20260102T000000Z.db", "url": "z", "id": 3},
        ]
    }
    assets = release_assets.list_db_assets(release)
    assert [a["name"] for a in assets] == [
        "jobs-20260102T000000Z.db",
        "jobs-20260101T000000Z.db",
    ]
