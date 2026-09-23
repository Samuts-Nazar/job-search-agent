import httpx
import pytest

from job_agent.ats_vendor import detect_ats_vendor, resolve_apply_destination


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://jobs.lever.co/acme/abcd", "lever"),
        ("https://apply.workable.com/acme/j/ABCDEF", "workable"),
        ("https://acme.teamtailor.com/jobs/123", "teamtailor"),
        ("https://acme.bamboohr.com/careers/123", "bamboohr"),
        ("https://careers.smartrecruiters.com/Acme/123", "smartrecruiters"),
        ("https://jobs.ashbyhq.com/acme/abc", "ashby"),
        ("https://acme.recruitee.com/o/role", "recruitee"),
        ("https://acme.myworkdayjobs.com/en-US/Acme/job/123", "workday"),
    ],
)
def test_detect_ats_vendor_matches_known_domains(url, expected):
    assert detect_ats_vendor(url) == expected


def test_detect_ats_vendor_unknown_domain_returns_none():
    assert detect_ats_vendor("https://djinni.co/jobs/123-qa/") is None


def test_detect_ats_vendor_none_or_empty_url():
    assert detect_ats_vendor(None) is None
    assert detect_ats_vendor("") is None


def test_detect_ats_vendor_does_not_false_positive_on_substring():
    # "notgreenhouse.io" contains "greenhouse.io" as a substring but is a
    # different domain entirely -- must not match.
    assert detect_ats_vendor("https://notgreenhouse.io/jobs/1") is None


# --- resolve_apply_destination ---------------------------------------------


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_apply_destination_dou_follows_goto_redirect_to_real_ats():
    posting_page = (
        '<a rel="nofollow" href="https://dou.ua/goto/vacancy/?id=373919">'
        "Відгукнутися</a>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://jobs.dou.ua/companies/acme/vacancies/1/":
            return httpx.Response(200, text=posting_page)
        if url == "https://dou.ua/goto/vacancy/?id=373919":
            return httpx.Response(
                302, headers={"location": "https://job-boards.eu.greenhouse.io/acme/jobs/1"}
            )
        if url == "https://job-boards.eu.greenhouse.io/acme/jobs/1":
            return httpx.Response(200, text="<html>job page</html>")
        raise AssertionError(f"unexpected request: {url}")

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="dou", posting_url="https://jobs.dou.ua/companies/acme/vacancies/1/"
        )

    assert result == "https://job-boards.eu.greenhouse.io/acme/jobs/1"


def test_resolve_apply_destination_dou_internal_form_returns_none():
    posting_page = '<a id="relogin-link" href="#apply">Відгукнутися</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=posting_page)

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="dou", posting_url="https://jobs.dou.ua/companies/acme/vacancies/1/"
        )

    assert result is None


def test_resolve_apply_destination_djinni_anon_apply_returns_none():
    posting_page = '<input type="hidden" name="anon_apply" value="true">'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=posting_page)

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="djinni", posting_url="https://djinni.co/jobs/1-qa/"
        )

    assert result is None


def test_resolve_apply_destination_djinni_without_marker_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no apply marker here</html>")

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="djinni", posting_url="https://djinni.co/jobs/1-qa/"
        )

    assert result is None


def test_resolve_apply_destination_remoteok_follows_redirect_to_real_ats():
    posting_page = '<a href="/l/123456">Apply now</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://remoteok.com/remote-jobs/1":
            return httpx.Response(200, text=posting_page)
        if url == "https://remoteok.com/l/123456":
            return httpx.Response(302, headers={"location": "https://jobs.lever.co/acme/1"})
        if url == "https://jobs.lever.co/acme/1":
            return httpx.Response(200, text="<html>job page</html>")
        raise AssertionError(f"unexpected request: {url}")

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="remoteok", posting_url="https://remoteok.com/remote-jobs/1"
        )

    assert result == "https://jobs.lever.co/acme/1"


def test_resolve_apply_destination_remoteok_loop_back_returns_none():
    posting_page = '<a href="/l/123456">Apply now</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://remoteok.com/remote-jobs/1":
            return httpx.Response(200, text=posting_page)
        if url == "https://remoteok.com/l/123456":
            return httpx.Response(
                302, headers={"location": "https://remoteok.com/remote-jobs/1"}
            )
        raise AssertionError(f"unexpected request: {url}")

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="remoteok", posting_url="https://remoteok.com/remote-jobs/1"
        )

    assert result is None


def test_resolve_apply_destination_unverified_source_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>some wwr page</html>")

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="wwr", posting_url="https://weworkremotely.com/remote-jobs/1"
        )

    assert result is None


def test_resolve_apply_destination_network_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with _client(handler) as client:
        result = resolve_apply_destination(
            client, source="dou", posting_url="https://jobs.dou.ua/companies/acme/vacancies/1/"
        )

    assert result is None
