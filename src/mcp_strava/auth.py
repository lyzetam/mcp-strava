"""`mcp-strava auth`: the one-time browser login that writes the token file.

Strava only redirects to the "Authorization Callback Domain" registered on the
app, which must be `localhost` for this flow. A one-shot listener on 127.0.0.1
catches the redirect, checks `state`, and trades the code for tokens.
"""

import argparse
import getpass
import os
import secrets
import socketserver
import sys
import time
import webbrowser
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from mcp_strava.client import TOKEN_URL, StravaError, raise_for_status
from mcp_strava.tokens import TokenFileError, TokenStore, token_path

SCOPES = "read,read_all,profile:read_all,activity:read_all"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
DEFAULT_PORT = 8723


class AuthError(Exception):
    """The browser authorization did not produce a usable code."""


class StateMismatch(AuthError):
    """The redirect belongs to a different (usually earlier) login attempt."""


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "auto",
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query, safe=',:/')}"


def parse_callback(query: str, expected_state: str) -> tuple[str, str]:
    """(code, granted scope) from the redirect's query string."""
    params = {k: v[0] for k, v in parse_qs(query).items()}
    if params.get("state") != expected_state:
        raise StateMismatch("The callback's state did not match this login attempt, so it was ignored.")
    if "error" in params:
        raise AuthError(f"Strava returned error={params['error']}.")
    if "code" not in params:
        raise AuthError("The callback carried no authorization code.")
    return params["code"], params.get("scope", "")


class _CallbackHTTPServer(HTTPServer):
    expected_state = ""
    result: tuple[str, str] | AuthError | None = None

    def server_bind(self) -> None:
        # HTTPServer.server_bind reverse-resolves the bind address only to fill in server_name, and that
        # lookup can stall for 30 s or more on some networks. Nothing here uses the name.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.socket.getsockname()[1]


class _CallbackHandler(BaseHTTPRequestHandler):
    @property
    def _server(self) -> _CallbackHTTPServer:
        return cast(_CallbackHTTPServer, self.server)

    def setup(self) -> None:
        # Bound every read by the time left, so a connection that opens and stays silent (a browser
        # preconnect, any local process) cannot hold the listener past --timeout.
        self.request.settimeout(self._server.timeout)
        super().setup()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path != "/callback":
            self.send_error(404)
            return
        try:
            self._server.result = parse_callback(url.query, self._server.expected_state)
            status, text = 200, "Strava is connected. You can close this tab and return to the terminal."
        except StateMismatch:
            # Leave result unset so the listener keeps waiting for this attempt's own redirect.
            status, text = 400, "This link is from an earlier login attempt. Open the newest authorize URL."
        except AuthError as exc:
            self._server.result = exc
            status, text = 400, f"Strava authorization failed: {exc}"
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class CallbackServer:
    def __init__(self, port: int = DEFAULT_PORT):
        self._httpd = _CallbackHTTPServer(("127.0.0.1", port), _CallbackHandler)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def __enter__(self) -> "CallbackServer":
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.server_close()

    def wait(self, state: str, timeout: float) -> tuple[str, str]:
        self._httpd.expected_state = state
        deadline = time.monotonic() + timeout
        while self._httpd.result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthError(f"Timed out after {timeout:g}s waiting for Strava to redirect back.")
            self._httpd.timeout = remaining
            self._httpd.handle_request()
        if isinstance(self._httpd.result, AuthError):
            raise self._httpd.result
        return self._httpd.result


def exchange_code(
    client_id: str, client_secret: str, code: str, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    with httpx.Client(timeout=30, transport=transport) as http:
        resp = http.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    raise_for_status(resp)
    tokens: dict[str, Any] = resp.json()
    return tokens


def ask_terminal(label: str, secret: bool) -> str:
    """Prompt on the terminal, hiding the answer when `secret`; "" when there is no terminal to ask."""
    if not sys.stdin.isatty():
        return ""
    try:
        return (getpass.getpass if secret else input)(f"{label}: ").strip()
    except EOFError:  # Ctrl-D
        print(file=sys.stderr)
        return ""


def run_auth(
    argv: list[str],
    *,
    env: Mapping[str, str] = os.environ,
    store: TokenStore | None = None,
    open_browser: Callable[[str], bool] | None = webbrowser.open,
    transport: httpx.BaseTransport | None = None,
    ask: Callable[[str, bool], str] = ask_terminal,
) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-strava auth",
        description="Authorize mcp-strava to read your Strava data. Asks for your Strava API application's "
        "Client ID and Client Secret unless STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET are set.",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="local callback port (default %(default)s)"
    )
    parser.add_argument("--timeout", type=float, default=300, help="seconds to wait for approval")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    args = parser.parse_args(argv)

    store = store or TokenStore(token_path(env))
    try:
        existing = store.load()
    except TokenFileError as exc:
        print(f"Ignoring the unreadable token file: {exc} It will be replaced.", file=sys.stderr)
        existing = {}
    client_id = env.get("STRAVA_CLIENT_ID") or existing.get("client_id") or ask("Strava Client ID", False)
    client_secret = (
        env.get("STRAVA_CLIENT_SECRET")
        or existing.get("client_secret")
        or ask("Strava Client Secret (input hidden)", True)
    )
    if not (client_id and client_secret):
        print(
            "No Strava app credentials. Run `mcp-strava auth` in a terminal to be asked for them, or set "
            "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET (both shown at https://www.strava.com/settings/api).",
            file=sys.stderr,
        )
        return 1

    state = secrets.token_urlsafe(16)
    try:
        with CallbackServer(args.port) as server:
            url = authorize_url(str(client_id), f"http://localhost:{server.port}/callback", state)
            print(f"Approve read access to Strava at:\n\n  {url}\n", flush=True)
            if open_browser and not args.no_browser:
                open_browser(url)
            code, scope = server.wait(state, args.timeout)
        tokens = exchange_code(str(client_id), client_secret, code, transport=transport)
    except (AuthError, StravaError, httpx.HTTPError, OSError) as exc:
        print(f"Authorization failed: {exc}", file=sys.stderr)
        return 1

    athlete = tokens.get("athlete") or {}
    store.save(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": tokens["expires_at"],
            "athlete_id": athlete.get("id"),
            "scope": scope,
        }
    )
    name = " ".join(filter(None, (athlete.get("firstname"), athlete.get("lastname")))) or "unknown athlete"
    print(f"Authorized as {name} (athlete {athlete.get('id')}) with scope: {scope}")
    print(f"Tokens saved to {store.path}")
    missing = [s for s in SCOPES.split(",") if s not in scope.split(",")]
    if missing:
        print(
            f"Warning: Strava did not grant {', '.join(missing)}, so some tools will fail. "
            "Run `mcp-strava auth` again and leave every box ticked."
        )
    return 0
