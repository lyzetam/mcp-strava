import contextlib
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mcp_strava.auth import (
    SCOPES,
    AuthError,
    CallbackServer,
    ask_terminal,
    authorize_url,
    parse_callback,
    run_auth,
)
from mcp_strava.tokens import TokenStore

from .conftest import FakeStrava


def test_authorize_url_requests_read_scopes_and_local_redirect():
    url = urlparse(authorize_url("123", "http://localhost:8723/callback", "st4te"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}

    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://www.strava.com/oauth/authorize"
    assert query == {
        "client_id": "123",
        "response_type": "code",
        "redirect_uri": "http://localhost:8723/callback",
        "approval_prompt": "auto",
        "scope": "read,read_all,profile:read_all,activity:read_all",
        "state": "st4te",
    }
    assert SCOPES == "read,read_all,profile:read_all,activity:read_all"


def test_parse_callback_returns_code_and_granted_scope():
    assert parse_callback("code=abc&scope=read,activity:read_all&state=s", "s") == (
        "abc",
        "read,activity:read_all",
    )


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("code=abc&state=forged", "state"),
        ("error=access_denied&state=s", "access_denied"),
        ("state=s", "code"),
    ],
)
def test_parse_callback_rejects_bad_callbacks(query: str, reason: str):
    with pytest.raises(AuthError, match=reason):
        parse_callback(query, "s")


def _hit(url: str) -> None:
    """Request `url` in the background, like a browser: a request the listener never answers is ignored."""

    def get() -> None:
        with contextlib.suppress(httpx.HTTPError):
            httpx.get(url, timeout=5, trust_env=False)

    threading.Thread(target=get, daemon=True).start()


def _get_in_order(*urls: str) -> list[int]:
    """GET `urls` one after another on a background thread; the list fills with their status codes."""
    statuses: list[int] = []

    def run() -> None:
        for url in urls:
            statuses.append(httpx.get(url, timeout=5, trust_env=False).status_code)

    threading.Thread(target=run, daemon=True).start()
    return statuses


def test_callback_server_receives_the_redirect_over_real_http():
    with CallbackServer(port=0) as server:
        base = f"http://127.0.0.1:{server.port}"
        statuses = _get_in_order(f"{base}/favicon.ico", f"{base}/callback?code=xyz&scope=read&state=s1")
        assert server.wait("s1", timeout=5) == ("xyz", "read")
    assert statuses[0] == 404


def test_callback_server_ignores_a_stale_redirect_and_keeps_waiting():
    # A reload of an earlier attempt's redirect carries that attempt's state; it must not end this login.
    with CallbackServer(port=0) as server:
        base = f"http://127.0.0.1:{server.port}/callback"
        statuses = _get_in_order(
            f"{base}?code=old&scope=read&state=previous-attempt", f"{base}?code=xyz&scope=read&state=s1"
        )
        assert server.wait("s1", timeout=5) == ("xyz", "read")
    assert statuses[0] == 400


def test_callback_server_timeout_holds_when_a_connection_sends_nothing():
    # A browser preconnect or any local process can open a socket and stay silent.
    with CallbackServer(port=0) as server, socket.create_connection(("127.0.0.1", server.port)):
        started = time.monotonic()
        with pytest.raises(AuthError, match="Timed out"):
            server.wait("s1", timeout=0.5)
        assert time.monotonic() - started < 3


def test_callback_server_times_out_without_a_redirect():
    with CallbackServer(port=0) as server, pytest.raises(AuthError, match="Timed out"):
        server.wait("s1", timeout=0.2)


TOKEN_BODY = {
    "token_type": "Bearer",
    "access_token": "fresh-access",
    "refresh_token": "fresh-refresh",
    "expires_at": 1_800_021_600,
    "athlete": {"id": 42, "firstname": "Landry", "lastname": "Zetam"},
}


def _browser_that_approves(scope: str):
    """Stands in for the user approving the app: follows the redirect back to the local listener."""

    def open_browser(url: str) -> bool:
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        _hit(f"{query['redirect_uri']}?code=the-code&scope={scope}&state={query['state']}")
        return True

    return open_browser


def test_run_auth_exchanges_the_code_and_saves_tokens(store: TokenStore, fake: FakeStrava, capsys):
    fake.json("POST", "/oauth/token", TOKEN_BODY)
    env = {"STRAVA_CLIENT_ID": "123", "STRAVA_CLIENT_SECRET": "client-secret-value"}

    code = run_auth(
        ["--port", "0", "--timeout", "5"],
        env=env,
        store=store,
        open_browser=_browser_that_approves(SCOPES),
        transport=httpx.MockTransport(fake.handler),
    )

    assert code == 0
    form = dict(httpx.QueryParams(fake.calls("POST", "/oauth/token")[0].content.decode()))
    assert form == {
        "client_id": "123",
        "client_secret": "client-secret-value",
        "code": "the-code",
        "grant_type": "authorization_code",
    }
    assert store.load() == {
        "client_id": "123",
        "client_secret": "client-secret-value",
        "access_token": "fresh-access",
        "refresh_token": "fresh-refresh",
        "expires_at": 1_800_021_600,
        "athlete_id": 42,
        "scope": SCOPES,
    }
    out = capsys.readouterr().out
    assert "Landry Zetam" in out
    for secret in ("client-secret-value", "fresh-access", "fresh-refresh", "the-code"):
        assert secret not in out


def test_run_auth_reuses_client_credentials_from_the_token_file(store: TokenStore, fake: FakeStrava):
    store.save({"client_id": "77", "client_secret": "stored-secret", "refresh_token": "old"})
    fake.json("POST", "/oauth/token", TOKEN_BODY)

    code = run_auth(
        ["--port", "0", "--timeout", "5"],
        env={},
        store=store,
        open_browser=_browser_that_approves(SCOPES),
        transport=httpx.MockTransport(fake.handler),
    )

    assert code == 0
    assert store.load()["client_id"] == "77"
    assert store.load()["refresh_token"] == "fresh-refresh"


def test_run_auth_warns_when_scopes_were_unticked(store: TokenStore, fake: FakeStrava, capsys):
    fake.json("POST", "/oauth/token", TOKEN_BODY)

    run_auth(
        ["--port", "0", "--timeout", "5"],
        env={"STRAVA_CLIENT_ID": "1", "STRAVA_CLIENT_SECRET": "s"},
        store=store,
        open_browser=_browser_that_approves("read,activity:read"),
        transport=httpx.MockTransport(fake.handler),
    )

    out = capsys.readouterr().out
    assert "activity:read_all" in out
    assert "profile:read_all" in out


def test_run_auth_without_client_credentials_fails_clearly(store: TokenStore, capsys):
    assert run_auth([], env={}, store=store, open_browser=None, ask=lambda label, secret: "") == 1
    assert "STRAVA_CLIENT_ID" in capsys.readouterr().err


def test_run_auth_reports_a_failed_exchange(store: TokenStore, fake: FakeStrava, capsys):
    fake.json("POST", "/oauth/token", {"message": "Bad Request", "errors": [{"code": "invalid"}]}, status=400)

    code = run_auth(
        ["--port", "0", "--timeout", "5"],
        env={"STRAVA_CLIENT_ID": "1", "STRAVA_CLIENT_SECRET": "s"},
        store=store,
        open_browser=_browser_that_approves(SCOPES),
        transport=httpx.MockTransport(fake.handler),
    )

    assert code == 1
    assert "Bad Request" in capsys.readouterr().err
    assert store.load() == {}


def test_run_auth_reports_a_network_failure_during_the_exchange(store: TokenStore, fake: FakeStrava, capsys):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    fake.add("POST", "/oauth/token", unreachable)

    code = run_auth(
        ["--port", "0", "--timeout", "5"],
        env={"STRAVA_CLIENT_ID": "1", "STRAVA_CLIENT_SECRET": "s"},
        store=store,
        open_browser=_browser_that_approves(SCOPES),
        transport=httpx.MockTransport(fake.handler),
    )

    assert code == 1
    assert "connection refused" in capsys.readouterr().err
    assert not store.path.exists()


def test_run_auth_replaces_an_unreadable_token_file(store: TokenStore, fake: FakeStrava, capsys):
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{truncated")
    fake.json("POST", "/oauth/token", TOKEN_BODY)

    code = run_auth(
        ["--port", "0", "--timeout", "5"],
        env={"STRAVA_CLIENT_ID": "123", "STRAVA_CLIENT_SECRET": "client-secret-value"},
        store=store,
        open_browser=_browser_that_approves(SCOPES),
        transport=httpx.MockTransport(fake.handler),
    )

    assert code == 0
    assert store.load()["refresh_token"] == "fresh-refresh"
    assert str(store.path) in capsys.readouterr().err


def test_run_auth_asks_for_missing_credentials(store: TokenStore, fake: FakeStrava):
    fake.json("POST", "/oauth/token", TOKEN_BODY)
    asked: list[tuple[str, bool]] = []

    def ask(label: str, secret: bool) -> str:
        asked.append((label, secret))
        return "typed-secret" if secret else "555"

    code = run_auth(
        ["--port", "0", "--timeout", "5"],
        env={},
        store=store,
        open_browser=_browser_that_approves(SCOPES),
        transport=httpx.MockTransport(fake.handler),
        ask=ask,
    )

    assert code == 0
    assert [secret for _, secret in asked] == [False, True]
    assert store.load()["client_id"] == "555"
    assert store.load()["client_secret"] == "typed-secret"


def test_ask_terminal_treats_end_of_input_as_no_answer(monkeypatch):
    def abandon(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", abandon)
    monkeypatch.setattr("getpass.getpass", abandon)

    assert ask_terminal("Strava Client ID", secret=False) == ""
    assert ask_terminal("Strava Client Secret", secret=True) == ""


def test_callback_server_starts_without_a_reverse_dns_lookup(monkeypatch):
    # http.server looks up a hostname for the bind address, which can stall for 30 s or more on some
    # networks (seen on GitHub's macOS runners). The listener never uses that name.
    def no_dns(name: str = "") -> str:
        raise AssertionError(f"reverse DNS lookup of {name!r}")

    monkeypatch.setattr(socket, "getfqdn", no_dns)

    with CallbackServer(port=0) as server:
        assert server.port > 0
