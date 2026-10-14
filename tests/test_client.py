import httpx
import pytest

from mcp_strava.client import StravaAuthError, StravaError, StravaRateLimitError
from mcp_strava.tokens import TokenStore

from .conftest import NOW, FakeStrava, make_client

ATHLETE = {"id": 42, "firstname": "Landry", "lastname": "Zetam"}


def refreshed(access: str = "new-access", refresh: str = "rotated-refresh") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": int(NOW) + 21600,
            "expires_in": 21600,
        },
    )


async def test_valid_access_token_is_used_without_refreshing(client, fake: FakeStrava):
    fake.json("GET", "/api/v3/athlete", ATHLETE)

    assert await client.get("/athlete") == ATHLETE
    assert fake.calls("POST", "/oauth/token") == []
    assert fake.requests[0].headers["Authorization"] == "Bearer good-access"


async def test_expiring_token_is_refreshed_and_rotated_refresh_token_persisted(
    authed_store: TokenStore, fake: FakeStrava
):
    authed_store.save({**authed_store.load(), "expires_at": NOW + 30})
    fake.add("POST", "/oauth/token", refreshed())
    fake.json("GET", "/api/v3/athlete", ATHLETE)

    async with make_client(authed_store, fake) as strava:
        await strava.get("/athlete")

    form = dict(httpx.QueryParams(fake.calls("POST", "/oauth/token")[0].content.decode()))
    assert form == {
        "client_id": "123",
        "client_secret": "client-secret-value",
        "grant_type": "refresh_token",
        "refresh_token": "refresh-1",
    }
    assert fake.calls("GET", "/api/v3/athlete")[0].headers["Authorization"] == "Bearer new-access"
    saved = authed_store.load()
    assert saved["refresh_token"] == "rotated-refresh"
    assert saved["access_token"] == "new-access"
    assert saved["expires_at"] == int(NOW) + 21600
    assert saved["athlete_id"] == 42


async def test_env_supplies_credentials_missing_from_the_token_file(store: TokenStore, fake: FakeStrava):
    fake.add("POST", "/oauth/token", refreshed())
    fake.json("GET", "/api/v3/athlete", ATHLETE)
    env = {
        "STRAVA_CLIENT_ID": "9",
        "STRAVA_CLIENT_SECRET": "env-secret",
        "STRAVA_REFRESH_TOKEN": "env-refresh",
    }

    async with make_client(store, fake, env=env) as strava:
        await strava.get("/athlete")

    form = dict(httpx.QueryParams(fake.calls("POST", "/oauth/token")[0].content.decode()))
    assert form["client_id"] == "9"
    assert form["refresh_token"] == "env-refresh"
    assert store.load()["refresh_token"] == "rotated-refresh"


async def test_missing_credentials_tell_the_user_to_run_auth(store: TokenStore, fake: FakeStrava):
    async with make_client(store, fake) as strava:
        with pytest.raises(StravaAuthError, match="mcp-strava auth"):
            await strava.get("/athlete")
    assert fake.requests == []


async def test_rejected_refresh_token_tells_the_user_to_run_auth(authed_store: TokenStore, fake: FakeStrava):
    authed_store.save({**authed_store.load(), "expires_at": 0})
    fake.json("POST", "/oauth/token", {"message": "Bad Request", "errors": [{"code": "invalid"}]}, status=400)

    async with make_client(authed_store, fake) as strava:
        with pytest.raises(StravaAuthError, match="mcp-strava auth"):
            await strava.get("/athlete")


async def test_401_forces_one_refresh_and_retries(client, fake: FakeStrava, authed_store: TokenStore):
    fake.add(
        "GET",
        "/api/v3/athlete",
        [httpx.Response(401, json={"message": "Authorization Error"}), httpx.Response(200, json=ATHLETE)],
    )
    fake.add("POST", "/oauth/token", refreshed())

    assert await client.get("/athlete") == ATHLETE
    auth_headers = [r.headers["Authorization"] for r in fake.calls("GET", "/api/v3/athlete")]
    assert auth_headers == ["Bearer good-access", "Bearer new-access"]
    assert authed_store.load()["refresh_token"] == "rotated-refresh"


async def test_second_401_raises_auth_error_with_stravas_message(client, fake: FakeStrava):
    unauthorized = {
        "message": "Authorization Error",
        "errors": [{"resource": "Athlete", "field": "access_token", "code": "invalid"}],
    }
    fake.add(
        "GET",
        "/api/v3/athlete/activities",
        [httpx.Response(401, json=unauthorized), httpx.Response(401, json=unauthorized)],
    )
    fake.add("POST", "/oauth/token", refreshed())

    with pytest.raises(StravaAuthError) as info:
        await client.get("/athlete/activities")
    assert "access_token:invalid" in str(info.value)
    assert "mcp-strava auth" in str(info.value)


async def test_401_for_a_missing_permission_fails_without_refreshing(client, fake: FakeStrava):
    # A new token cannot add a scope the athlete never granted, so refreshing only burns rate limit.
    unauthorized = {
        "message": "Authorization Error",
        "errors": [{"resource": "AccessToken", "field": "activity:read_permission", "code": "missing"}],
    }
    fake.json("GET", "/api/v3/athlete/activities", unauthorized, status=401)

    with pytest.raises(StravaAuthError) as info:
        await client.get("/athlete/activities")

    assert "activity:read_permission" in str(info.value)
    assert "mcp-strava auth" in str(info.value)
    assert len(fake.calls("GET", "/api/v3/athlete/activities")) == 1
    assert fake.calls("POST", "/oauth/token") == []


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 8] nodename nor servname provided", request=request)


async def test_network_failure_reaches_the_caller_with_its_reason(client, fake: FakeStrava):
    fake.add("GET", "/api/v3/athlete", _unreachable)

    with pytest.raises(StravaError, match="Could not reach Strava.*nodename"):
        await client.get("/athlete")


async def test_network_failure_during_refresh_reaches_the_caller_with_its_reason(
    authed_store: TokenStore, fake: FakeStrava
):
    data = authed_store.load()
    data["expires_at"] = NOW
    authed_store.save(data)
    fake.add("POST", "/oauth/token", _unreachable)

    async with make_client(authed_store, fake) as strava:
        with pytest.raises(StravaError, match="Could not reach Strava"):
            await strava.get("/athlete")


async def test_unreadable_token_file_tells_the_user_to_run_auth(store: TokenStore, fake: FakeStrava):
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{not json")

    async with make_client(store, fake) as strava:
        with pytest.raises(StravaAuthError) as info:
            await strava.get("/athlete")

    assert str(store.path) in str(info.value)
    assert "mcp-strava auth" in str(info.value)
    assert fake.requests == []


async def test_429_reports_rate_limit_headers(client, fake: FakeStrava):
    fake.json(
        "GET",
        "/api/v3/athlete",
        {"message": "Rate Limit Exceeded"},
        status=429,
        headers={
            "X-RateLimit-Limit": "200,2000",
            "X-RateLimit-Usage": "201,540",
            "X-ReadRateLimit-Limit": "100,1000",
            "X-ReadRateLimit-Usage": "101,300",
        },
    )

    with pytest.raises(StravaRateLimitError) as info:
        await client.get("/athlete")
    message = str(info.value)
    assert "X-RateLimit-Limit=200,2000" in message
    assert "X-RateLimit-Usage=201,540" in message
    assert "X-ReadRateLimit-Usage=101,300" in message


async def test_other_errors_carry_status_and_stravas_message(client, fake: FakeStrava):
    fake.json(
        "GET",
        "/api/v3/activities/7",
        {
            "message": "Record Not Found",
            "errors": [{"resource": "Activity", "field": "id", "code": "invalid"}],
        },
        status=404,
    )

    with pytest.raises(StravaError) as info:
        await client.get("/activities/7")
    assert "404" in str(info.value)
    assert "Record Not Found" in str(info.value)


async def test_none_params_are_not_sent(client, fake: FakeStrava):
    fake.json("GET", "/api/v3/athlete/activities", [])

    await client.get("/athlete/activities", {"after": None, "page": 2})

    assert dict(fake.requests[0].url.params) == {"page": "2"}


async def test_athlete_id_comes_from_token_file(client, fake: FakeStrava):
    assert await client.athlete_id() == 42
    assert fake.requests == []


async def test_athlete_id_falls_back_to_the_api(authed_store: TokenStore, fake: FakeStrava):
    data = authed_store.load()
    del data["athlete_id"]
    authed_store.save(data)
    fake.json("GET", "/api/v3/athlete", ATHLETE)

    async with make_client(authed_store, fake) as strava:
        assert await strava.athlete_id() == 42
        assert await strava.athlete_id() == 42

    assert len(fake.calls("GET", "/api/v3/athlete")) == 1
    assert authed_store.load()["athlete_id"] == 42


async def test_get_object_rejects_a_response_that_is_not_an_object(client, fake: FakeStrava):
    fake.json("GET", "/api/v3/athlete", [])

    with pytest.raises(StravaError, match="expected an object"):
        await client.get_object("/athlete")
