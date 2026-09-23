from dataclasses import dataclass

import httpx
import pandas as pd
from aiogram.exceptions import TelegramUnauthorizedError

from job_agent import data_guard, doctor, telegram_bot
from job_agent.config import (
    DjinniSourceConfig,
    DouSourceConfig,
    LinkedinSourceConfig,
    RemoteOkSourceConfig,
    SourcesConfig,
    WwrSourceConfig,
)

# --- check_data_files ----------------------------------------------------


def test_check_data_files_maps_ok_and_failed_results(monkeypatch):
    def fake_check_data_safety(facts_path, answers_path):
        return [
            data_guard.DataCheckResult(path="facts.yaml", ok=True),
            data_guard.DataCheckResult(path="answers.yaml", ok=False, problems=["bad field"]),
        ]

    monkeypatch.setattr(doctor.data_guard, "check_data_safety", fake_check_data_safety)

    checks = doctor.check_data_files("facts.yaml", "answers.yaml")

    assert [c.ok for c in checks] == [True, False]
    assert checks[1].detail == "bad field"


# --- check_binaries --------------------------------------------------------


def test_check_binaries_reports_found_and_missing(monkeypatch):
    monkeypatch.setattr(doctor, "which", lambda name: "/usr/bin/typst" if name == "typst" else None)

    checks = doctor.check_binaries()

    by_name = {c.name: c for c in checks}
    assert by_name["binary: typst"].ok is True
    assert by_name["binary: node"].ok is False
    assert "not found" in by_name["binary: node"].detail


# --- check_openrouter --------------------------------------------------------


def test_check_openrouter_reports_usage_and_limit(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/key"
        return httpx.Response(
            200, json={"data": {"usage_monthly": 1.5, "limit": 10.0, "limit_remaining": 8.5}}
        )

    def fake_make_llm_client(api_key):
        return httpx.Client(
            base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(doctor, "make_llm_client", fake_make_llm_client)

    check = doctor.check_openrouter("sk-fake")

    assert check.ok is True
    assert "1.50" in check.detail
    assert "10.00" in check.detail


def test_check_openrouter_reports_no_limit(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"usage_monthly": 0.2, "limit": None}})

    monkeypatch.setattr(
        doctor,
        "make_llm_client",
        lambda api_key: httpx.Client(
            base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(handler)
        ),
    )

    check = doctor.check_openrouter("sk-fake")

    assert check.ok is True
    assert "no limit" in check.detail


def test_check_openrouter_reports_failure_on_bad_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    monkeypatch.setattr(
        doctor,
        "make_llm_client",
        lambda api_key: httpx.Client(
            base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(handler)
        ),
    )

    check = doctor.check_openrouter("sk-bad")

    assert check.ok is False


# --- check_telegram --------------------------------------------------------


@dataclass
class _FakeUser:
    username: str


@dataclass
class _FakeChat:
    id: int
    title: str | None = None
    first_name: str | None = None


class _FakeSession:
    async def close(self) -> None:
        pass


class _FakeBot:
    def __init__(self, *, me=None, chat=None, me_error=None, chat_error=None):
        self._me = me
        self._chat = chat
        self._me_error = me_error
        self._chat_error = chat_error
        self.session = _FakeSession()

    async def get_me(self):
        if self._me_error:
            raise self._me_error
        return self._me

    async def get_chat(self, chat_id):
        if self._chat_error:
            raise self._chat_error
        return self._chat


async def test_check_telegram_ok(monkeypatch):
    fake_bot = _FakeBot(me=_FakeUser(username="my_bot"), chat=_FakeChat(id=1, title="My Chat"))
    monkeypatch.setattr(telegram_bot, "build_bot", lambda token: fake_bot)

    check = await doctor.check_telegram("token", "1")

    assert check.ok is True
    assert "my_bot" in check.detail
    assert "My Chat" in check.detail


async def test_check_telegram_bad_token(monkeypatch):
    fake_bot = _FakeBot(me_error=TelegramUnauthorizedError(method=None, message="Unauthorized"))
    monkeypatch.setattr(telegram_bot, "build_bot", lambda token: fake_bot)

    check = await doctor.check_telegram("bad-token", "1")

    assert check.ok is False
    assert "token invalid" in check.detail


async def test_check_telegram_bad_chat_id(monkeypatch):
    fake_bot = _FakeBot(
        me=_FakeUser(username="my_bot"),
        chat_error=TelegramUnauthorizedError(method=None, message="chat not found"),
    )
    monkeypatch.setattr(telegram_bot, "build_bot", lambda token: fake_bot)

    check = await doctor.check_telegram("token", "999")

    assert check.ok is False
    assert "my_bot" in check.detail
    assert "999" in check.detail


# --- check_sources --------------------------------------------------------


def _sources_config(**overrides) -> SourcesConfig:
    defaults = dict(
        djinni=DjinniSourceConfig(enabled=True),
        dou=DouSourceConfig(enabled=True),
        remoteok=RemoteOkSourceConfig(enabled=True),
        wwr=WwrSourceConfig(enabled=True, categories=["remote-devops-sysadmin-jobs"]),
        linkedin=LinkedinSourceConfig(enabled=True, keywords=["QA"]),
    )
    defaults.update(overrides)
    return SourcesConfig(**defaults)


def test_check_sources_all_reachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(
        doctor, "make_source_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(doctor, "scrape_jobs", lambda **kwargs: pd.DataFrame([{"title": "QA"}]))

    checks = doctor.check_sources(_sources_config())

    by_name = {c.name: c for c in checks}
    assert by_name["source: djinni"].ok is True
    assert by_name["source: dou"].ok is True
    assert by_name["source: remoteok"].ok is True
    assert by_name["source: wwr"].ok is True
    assert by_name["source: linkedin"].ok is True
    assert by_name["source: nofluffjobs"].ok is True
    assert "not implemented" in by_name["source: nofluffjobs"].detail


def test_check_sources_reports_unreachable_source(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(
        doctor, "make_source_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(doctor, "scrape_jobs", lambda **kwargs: pd.DataFrame([{"title": "QA"}]))

    checks = doctor.check_sources(_sources_config())

    by_name = {c.name: c for c in checks}
    assert by_name["source: djinni"].ok is False


def test_check_sources_skips_disabled(monkeypatch):
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(
        doctor, "make_source_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(doctor, "scrape_jobs", lambda **kwargs: pd.DataFrame([{"title": "QA"}]))

    config = _sources_config(djinni=DjinniSourceConfig(enabled=False))
    checks = doctor.check_sources(config)

    by_name = {c.name: c for c in checks}
    assert by_name["source: djinni"].ok is True
    assert "disabled" in by_name["source: djinni"].detail
    assert not any("djinni" in url for url in requested_urls)


def test_check_sources_linkedin_empty_result_is_a_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(
        doctor, "make_source_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(doctor, "scrape_jobs", lambda **kwargs: pd.DataFrame([]))

    checks = doctor.check_sources(_sources_config())

    by_name = {c.name: c for c in checks}
    assert by_name["source: linkedin"].ok is False


# --- check_db_schema --------------------------------------------------------


def test_check_db_schema_not_initialized(tmp_path):
    from job_agent import db

    conn = db.connect(tmp_path / "empty.db")
    check = doctor.check_db_schema(conn)
    conn.close()

    assert check.ok is True
    assert "not initialized" in check.detail


def test_check_db_schema_up_to_date(tmp_path):
    from job_agent import db

    conn = db.connect(tmp_path / "current.db")
    db.init_db(conn)
    check = doctor.check_db_schema(conn)
    conn.close()

    assert check.ok is True
    assert check.detail == "up to date"


def test_check_db_schema_fails_on_missing_columns(tmp_path):
    from job_agent import db

    conn = db.connect(tmp_path / "stale.db")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS postings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL UNIQUE,
            dedup_hash TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()

    check = doctor.check_db_schema(conn)
    conn.close()

    assert check.ok is False
    assert "postings" in check.detail


# --- format_doctor_report --------------------------------------------------------


def test_format_doctor_report_shows_status_and_totals():
    checks = [
        doctor.DoctorCheck(name="a", ok=True, detail="fine"),
        doctor.DoctorCheck(name="b", ok=False, detail="broken"),
    ]

    report = doctor.format_doctor_report(checks)

    assert "[OK] a: fine" in report
    assert "[FAILED] b: broken" in report
    assert "1/2 checks passed." in report
