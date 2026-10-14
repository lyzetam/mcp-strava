from collections.abc import Callable
from typing import Any

import httpx
import pytest

from mcp_strava.client import StravaClient
from mcp_strava.tokens import TokenStore

NOW = 1_800_000_000.0

Route = httpx.Response | list[httpx.Response] | Callable[[httpx.Request], httpx.Response]


class FakeStrava:
    """The Strava HTTP API as seen through httpx.MockTransport: canned responses per (method, path)."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Route] = {}
        self.requests: list[httpx.Request] = []

    def add(self, method: str, path: str, route: Route) -> None:
        self.routes[(method, path)] = route

    def json(self, method: str, path: str, body: Any, status: int = 200, **kwargs: Any) -> None:
        self.add(method, path, httpx.Response(status, json=body, **kwargs))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"message": "Record Not Found", "errors": []})
        if isinstance(route, list):
            return route.pop(0)
        if callable(route):
            return route(request)
        return route

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]


@pytest.fixture
def fake() -> FakeStrava:
    return FakeStrava()


@pytest.fixture
def store(tmp_path) -> TokenStore:
    return TokenStore(tmp_path / "config" / "tokens.json")


@pytest.fixture
def authed_store(store: TokenStore) -> TokenStore:
    store.save(
        {
            "client_id": "123",
            "client_secret": "client-secret-value",
            "access_token": "good-access",
            "refresh_token": "refresh-1",
            "expires_at": NOW + 3600,
            "athlete_id": 42,
        }
    )
    return store


def make_client(store: TokenStore, fake: FakeStrava, env: dict[str, str] | None = None) -> StravaClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return StravaClient(store, http=http, env=env or {}, clock=lambda: NOW)


@pytest.fixture
async def client(authed_store: TokenStore, fake: FakeStrava):
    strava = make_client(authed_store, fake)
    yield strava
    await strava.aclose()
