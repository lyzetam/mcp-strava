"""Read-only Strava API v3 client that keeps its own access token fresh.

The token file is re-read on every call, so running `mcp-strava auth` while the
server is up takes effect without a restart.
"""

import asyncio
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from mcp_strava.tokens import TokenFileError, TokenStore

API_BASE = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
REFRESH_MARGIN_S = 60
RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Usage",
    "X-ReadRateLimit-Limit",
    "X-ReadRateLimit-Usage",
)
REAUTH_HINT = "Run `mcp-strava auth` to authorize Strava again."


# ToolError rather than Exception: the MCP SDK hides the text of any other exception
# from the client, and these messages are exactly what the model needs to see.
class StravaError(ToolError):
    """A Strava request failed in a way worth explaining to the caller."""


class StravaAuthError(StravaError):
    """Strava credentials are missing, revoked, or lack a required scope."""


class StravaRateLimitError(StravaError):
    """Strava answered 429: the 15-minute or daily request budget is spent."""


def _error_body(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def strava_message(resp: httpx.Response) -> str:
    body = _error_body(resp)
    if body is None:
        return resp.text[:300]
    message = str(body.get("message", ""))
    errors = body.get("errors") or []
    details = "; ".join(
        ":".join(str(e[k]) for k in ("resource", "field", "code") if e.get(k))
        for e in errors
        if isinstance(e, dict)
    )
    return f"{message} ({details})" if details else message


def missing_permission(resp: httpx.Response) -> bool:
    """Whether a 401 says the athlete never granted a scope, which no token refresh can fix."""
    errors = (_error_body(resp) or {}).get("errors") or []
    return any(isinstance(e, dict) and str(e.get("field", "")).endswith("_permission") for e in errors)


def raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    where = f"{resp.request.method} {resp.request.url.path}"
    if resp.status_code == 429:
        limits = ", ".join(f"{h}={resp.headers[h]}" for h in RATE_LIMIT_HEADERS if h in resp.headers)
        raise StravaRateLimitError(
            f"Strava rate limit exceeded on {where} ({limits}). Each pair is 15-minute,daily; "
            "the 15-minute window resets at :00, :15, :30 and :45, the daily one at midnight UTC."
        )
    raise StravaError(f"Strava API error {resp.status_code} on {where}: {strava_message(resp)}")


class StravaClient:
    def __init__(
        self,
        store: TokenStore,
        *,
        http: httpx.AsyncClient | None = None,
        env: Mapping[str, str] = os.environ,
        clock: Callable[[], float] = time.time,
    ):
        self._store = store
        self._http = http or httpx.AsyncClient(timeout=30)
        self._env = env
        self._clock = clock
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "StravaClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET an API path such as `/athlete`, refreshing the access token when needed. Returns the JSON."""
        query = {k: v for k, v in (params or {}).items() if v is not None}
        token = await self._access_token()
        resp = await self._get(path, query, token)
        if resp.status_code == 401 and not missing_permission(resp):
            resp = await self._get(path, query, await self._access_token(stale=token))
        if resp.status_code == 401:
            raise StravaAuthError(
                f"Strava refused the request to {path}: {strava_message(resp)}. {REAUTH_HINT}"
            )
        raise_for_status(resp)
        return resp.json()

    async def get_object(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Like `get`, for endpoints documented to return a single JSON object."""
        body = await self.get(path, params)
        if not isinstance(body, dict):
            raise StravaError(f"Strava returned {type(body).__name__} for GET {path}; expected an object.")
        return body

    async def athlete_id(self) -> int:
        """The connected athlete's id, from the token file or, failing that, from Strava (then saved)."""
        stored = self._load().get("athlete_id")
        if stored:
            return int(stored)
        athlete_id = int((await self.get("/athlete"))["id"])
        async with self._lock:
            data = self._load()
            data["athlete_id"] = athlete_id
            self._store.save(data)
        return athlete_id

    async def _get(self, path: str, query: Mapping[str, Any], token: str) -> httpx.Response:
        try:
            return await self._http.get(
                f"{API_BASE}{path}", params=query, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TransportError as exc:
            raise StravaError(f"Could not reach Strava for GET {path}: {exc!r}") from exc

    def _load(self) -> dict[str, Any]:
        try:
            return self._store.load()
        except TokenFileError as exc:
            raise StravaAuthError(f"{exc} {REAUTH_HINT}") from exc

    async def _access_token(self, *, stale: str | None = None) -> str:
        """A token valid for at least REFRESH_MARGIN_S more seconds; `stale` is one Strava just rejected."""
        async with self._lock:
            data = self._load()
            token = data.get("access_token")
            if (
                isinstance(token, str)
                and token != stale
                and data.get("expires_at", 0) - self._clock() > REFRESH_MARGIN_S
            ):
                return token
            return await self._refresh(data)

    async def _refresh(self, data: dict[str, Any]) -> str:
        client_id = data.get("client_id") or self._env.get("STRAVA_CLIENT_ID")
        client_secret = data.get("client_secret") or self._env.get("STRAVA_CLIENT_SECRET")
        refresh_token = data.get("refresh_token") or self._env.get("STRAVA_REFRESH_TOKEN")
        if not (client_id and client_secret and refresh_token):
            raise StravaAuthError(
                f"Strava is not authorized: no client id, client secret and refresh token in "
                f"{self._store.path} or STRAVA_* environment variables. {REAUTH_HINT}"
            )
        try:
            resp = await self._http.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        except httpx.TransportError as exc:
            raise StravaError(f"Could not reach Strava to refresh the access token: {exc!r}") from exc
        if resp.status_code in (400, 401):
            raise StravaAuthError(f"Strava rejected the refresh token: {strava_message(resp)}. {REAUTH_HINT}")
        raise_for_status(resp)
        body = resp.json()
        access_token: str = body["access_token"]
        data.update(
            access_token=access_token, refresh_token=body["refresh_token"], expires_at=body["expires_at"]
        )
        self._store.save(data)
        return access_token
