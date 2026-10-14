from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp import Client

from mcp_strava.server import build_server

from .conftest import FakeStrava, make_client

EXPECTED_TOOLS = {
    "get_athlete",
    "get_athlete_stats",
    "get_athlete_zones",
    "list_activities",
    "get_activity",
    "get_activity_laps",
    "get_activity_zones",
    "get_activity_streams",
    "list_starred_segments",
    "get_segment",
    "list_segment_efforts",
    "list_routes",
    "get_gear",
    "list_clubs",
}


@pytest.fixture
def mcp(client):
    """Calls tools through a real in-memory MCP session. A session per call, because anyio
    cancel scopes cannot span pytest-asyncio's separate fixture setup and teardown tasks."""

    async def call_tool(name: str, arguments: dict | None = None):
        async with Client(build_server(client)) as session:
            return await session.call_tool(name, arguments or {})

    return call_tool


async def call(mcp, name: str, arguments: dict | None = None):
    result = await mcp(name, arguments)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_every_tool_is_listed_read_only_and_documented(client):
    async with Client(build_server(client)) as session:
        tools = (await session.list_tools()).tools

    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True, tool.name
        assert tool.description and len(tool.description) > 40, tool.name


async def test_list_activities_converts_dates_and_returns_compact_summaries(mcp, fake: FakeStrava):
    fake.json(
        "GET",
        "/api/v3/athlete/activities",
        [
            {
                "id": 1,
                "name": "Run",
                "sport_type": "Run",
                "distance": 5000.0,
                "moving_time": 1500,
                "map": {"summary_polyline": "abc"},
            }
        ],
    )

    result = await call(
        mcp,
        "list_activities",
        {"after": "2026-10-01", "before": "2026-10-08T12:00:00+00:00", "page": 2, "per_page": 500},
    )

    params = dict(fake.requests[0].url.params)
    assert params == {
        "after": str(int(datetime(2026, 10, 1).timestamp())),
        "before": "1791460800",
        "page": "2",
        "per_page": "200",
    }
    assert result["page"] == 2
    assert result["per_page"] == 200
    assert result["count"] == 1
    assert result["more_available"] is False
    [activity] = result["activities"]
    assert activity["distance_km"] == 5.0
    assert activity["moving_time"] == "0:25:00"
    assert "map" not in activity


async def test_list_activities_rejects_unparseable_dates(mcp, fake: FakeStrava):
    result = await mcp("list_activities", {"after": "last tuesday"})

    assert result.is_error
    assert "ISO" in result.content[0].text
    assert fake.requests == []


async def test_get_activity_strips_polylines_and_efforts_by_default(mcp, fake: FakeStrava):
    detail = {
        "id": 9,
        "name": "Ride",
        "map": {"id": "a9", "polyline": "long", "summary_polyline": "short"},
        "segment_efforts": [{"id": 1}],
        "best_efforts": [{"id": 2}],
        "splits_standard": [{"split": 1}],
        "splits_metric": [{"split": 1}],
        "laps": [{"id": 3}],
    }
    fake.json("GET", "/api/v3/activities/9", detail)

    result = await call(mcp, "get_activity", {"activity_id": 9})

    assert result == {
        "id": 9,
        "name": "Ride",
        "map": {"id": "a9"},
        "splits_metric": [{"split": 1}],
        "laps": [{"id": 3}],
    }
    assert dict(fake.requests[0].url.params) == {"include_all_efforts": "false"}


async def test_get_activity_can_include_efforts(mcp, fake: FakeStrava):
    fake.json("GET", "/api/v3/activities/9", {"id": 9, "segment_efforts": [{"id": 1}], "best_efforts": []})

    result = await call(mcp, "get_activity", {"activity_id": 9, "include_efforts": True})

    assert result["segment_efforts"] == [{"id": 1}]
    assert dict(fake.requests[0].url.params) == {"include_all_efforts": "true"}


async def test_get_activity_streams_downsamples(mcp, fake: FakeStrava):
    fake.json(
        "GET",
        "/api/v3/activities/9/streams",
        {
            "time": {"data": list(range(1000)), "series_type": "distance", "original_size": 1000},
            "heartrate": {"data": [140] * 1000, "series_type": "distance", "original_size": 1000},
        },
    )

    result = await call(
        mcp, "get_activity_streams", {"activity_id": 9, "keys": ["heartrate"], "max_points": 50}
    )

    params = dict(fake.requests[0].url.params)
    assert params == {"keys": "heartrate", "key_by_type": "true"}
    assert result["activity_id"] == 9
    assert result["streams"]["time"]["returned_points"] == 50
    assert result["streams"]["time"]["original_size"] == 1000
    assert result["streams"]["time"]["data"][-1] == 999


async def test_get_activity_streams_defaults_to_the_common_channels(mcp, fake: FakeStrava):
    fake.json("GET", "/api/v3/activities/9/streams", {})

    await call(mcp, "get_activity_streams", {"activity_id": 9})

    assert (
        fake.requests[0].url.params["keys"]
        == "time,distance,altitude,heartrate,cadence,watts,velocity_smooth"
    )


async def test_get_athlete_stats_uses_the_stored_athlete_id(mcp, fake: FakeStrava):
    fake.json("GET", "/api/v3/athletes/42/stats", {"ytd_run_totals": {"count": 80}})

    result = await call(mcp, "get_athlete_stats")

    assert result == {"ytd_run_totals": {"count": 80}}


@pytest.mark.parametrize(
    ("tool", "arguments", "path", "body", "key"),
    [
        ("get_athlete", {}, "/api/v3/athlete", {"id": 42}, None),
        ("get_athlete_zones", {}, "/api/v3/athlete/zones", {"heart_rate": {}}, None),
        ("get_activity_laps", {"activity_id": 9}, "/api/v3/activities/9/laps", [{"id": 1}], "laps"),
        ("get_activity_zones", {"activity_id": 9}, "/api/v3/activities/9/zones", [{"type": "hr"}], "zones"),
        ("list_starred_segments", {}, "/api/v3/segments/starred", [{"id": 5}], "segments"),
        ("get_segment", {"segment_id": 5}, "/api/v3/segments/5", {"id": 5}, None),
        ("list_routes", {}, "/api/v3/athletes/42/routes", [{"id": 6}], "routes"),
        ("get_gear", {"gear_id": "b1"}, "/api/v3/gear/b1", {"id": "b1"}, None),
        ("list_clubs", {}, "/api/v3/athlete/clubs", [{"id": 8}], "clubs"),
    ],
)
async def test_simple_tools_hit_the_right_endpoint(mcp, fake: FakeStrava, tool, arguments, path, body, key):
    fake.json("GET", path, body)

    result = await call(mcp, tool, arguments)

    assert fake.requests[0].url.path == path
    assert (result[key] if key else result) == body


async def test_lists_strip_polylines(mcp, fake: FakeStrava):
    fake.json("GET", "/api/v3/athletes/42/routes", [{"id": 6, "map": {"summary_polyline": "abc"}}])

    result = await call(mcp, "list_routes", {"per_page": 5})

    assert result["routes"] == [{"id": 6, "map": {}}]
    assert dict(fake.requests[0].url.params) == {"page": "1", "per_page": "5"}


async def test_list_segment_efforts_passes_filters(mcp, fake: FakeStrava):
    fake.json("GET", "/api/v3/segment_efforts", [{"id": 1, "elapsed_time": 300}])

    result = await call(
        mcp,
        "list_segment_efforts",
        {"segment_id": 5, "start_date_local": "2026-01-01T00:00:00Z", "per_page": 10},
    )

    assert dict(fake.requests[0].url.params) == {
        "segment_id": "5",
        "start_date_local": "2026-01-01T00:00:00Z",
        "per_page": "10",
    }
    assert result["efforts"] == [{"id": 1, "elapsed_time": 300}]


@pytest.mark.parametrize("gear_id", ["../../oauth/deauthorize", "b1?x=1", "b1/../../athlete", ""])
async def test_get_gear_rejects_ids_that_would_change_the_url(mcp, fake: FakeStrava, gear_id: str):
    result = await mcp("get_gear", {"gear_id": gear_id})

    assert result.is_error
    assert fake.requests == []


async def test_strava_errors_reach_the_model_with_their_message(mcp, fake: FakeStrava):
    fake.json(
        "GET",
        "/api/v3/athlete",
        {"message": "Rate Limit Exceeded"},
        status=429,
        headers={"X-RateLimit-Usage": "201,540", "X-RateLimit-Limit": "200,2000"},
    )

    result = await mcp("get_athlete")

    assert result.is_error
    assert "X-RateLimit-Usage=201,540" in result.content[0].text


def test_main_dispatches_auth(monkeypatch):
    from mcp_strava import server

    seen = {}

    def fake_run_auth(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(server, "run_auth", fake_run_auth)

    with pytest.raises(SystemExit) as exit_info:
        server.main(["auth", "--no-browser"])

    assert seen["argv"] == ["--no-browser"]
    assert exit_info.value.code == 0


def test_main_exits_quietly_when_auth_is_interrupted(monkeypatch):
    from mcp_strava import server

    def interrupted(argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "run_auth", interrupted)

    with pytest.raises(SystemExit) as exit_info:
        server.main(["auth"])

    assert exit_info.value.code == 130


def test_main_prints_help_and_version(capsys):
    from mcp_strava import server

    for flag in ("--help", "--version"):
        with pytest.raises(SystemExit) as exit_info:
            server.main([flag])
        assert exit_info.value.code == 0

    out = capsys.readouterr().out
    assert "auth" in out
    assert f"mcp-strava {version('mcp-strava')}" in out


def test_main_rejects_unknown_arguments(capsys):
    from mcp_strava import server

    with pytest.raises(SystemExit) as exit_info:
        server.main(["--bogus"])

    assert exit_info.value.code == 2
    assert "--bogus" in capsys.readouterr().err


async def test_server_can_close_the_http_client_when_it_shuts_down(authed_store, fake: FakeStrava):
    strava = make_client(authed_store, fake)
    async with Client(build_server(strava, close_client_on_shutdown=True)) as session:
        await session.list_tools()

    with pytest.raises(RuntimeError, match="closed"):
        await strava.get("/athlete")


def test_readme_lists_every_tool():
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    missing = [name for name in sorted(EXPECTED_TOOLS) if f"`{name}`" not in readme]
    assert missing == []
