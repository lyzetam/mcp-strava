"""MCP server exposing the connected athlete's Strava data, read-only."""

import argparse
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from mcp_strava.auth import run_auth
from mcp_strava.client import StravaClient
from mcp_strava.tokens import TokenStore, token_path
from mcp_strava.transform import compact_activity, downsample_streams, strip_polylines

INSTRUCTIONS = """\
Read-only access to the connected athlete's Strava account (API v3).
Units are Strava's: distance and elevation in meters, time in seconds, speed in meters per second.
list_activities also gives km, miles and h:mm:ss.
Strava allows about 100 read requests per 15 minutes and 1,000 per day, so narrow list_activities
with after/before instead of walking every page, and fetch streams only for activities you need."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
DEFAULT_STREAM_KEYS = ["time", "distance", "altitude", "heartrate", "cadence", "watts", "velocity_smooth"]
MAX_PER_PAGE = 200
MAX_STREAM_POINTS = 10_000

Page = Annotated[int, Field(description="Page number, starting at 1.")]
PerPage = Annotated[int, Field(description=f"Items per page, at most {MAX_PER_PAGE}.")]


def _paging(page: int, per_page: int) -> dict[str, int]:
    return {"page": max(1, page), "per_page": max(1, min(per_page, MAX_PER_PAGE))}


def _epoch(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        raise ToolError(
            f"{name}={value!r} is not an ISO 8601 date or datetime, e.g. 2026-10-01 or 2026-10-01T06:00:00Z."
        ) from None


def build_server(client: StravaClient, *, close_client_on_shutdown: bool = False) -> MCPServer:
    """The MCP server with every tool bound to `client`. Pass close_client_on_shutdown=True when the
    server owns the client, so its HTTP connections are closed when the server stops."""

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if close_client_on_shutdown:
                await client.aclose()

    server = MCPServer("strava", instructions=INSTRUCTIONS, version=version("mcp-strava"), lifespan=lifespan)

    @server.tool(annotations=READ_ONLY)
    async def get_athlete() -> dict[str, Any]:
        """Profile of the connected athlete: name, location, sex, weight, FTP, measurement preference,
        and their bikes, shoes and clubs."""
        return await client.get_object("/athlete")

    @server.tool(annotations=READ_ONLY)
    async def get_athlete_stats() -> dict[str, Any]:
        """Ride, run and swim totals for the connected athlete: recent (last 4 weeks), year-to-date and
        all-time count, distance, moving time, elapsed time and elevation gain, plus biggest ride and climb.
        Strava only counts activities whose visibility is Everyone."""
        return await client.get_object(f"/athletes/{await client.athlete_id()}/stats")

    @server.tool(annotations=READ_ONLY)
    async def get_athlete_zones() -> dict[str, Any]:
        """The connected athlete's heart-rate zones (bpm) and power zones (watts)."""
        return await client.get_object("/athlete/zones")

    @server.tool(annotations=READ_ONLY)
    async def list_activities(
        after: Annotated[
            str | None,
            Field(
                description="Only activities that start after this ISO date or datetime, e.g. 2026-10-01. "
                "Without a time zone it is read in this computer's local time."
            ),
        ] = None,
        before: Annotated[
            str | None, Field(description="Only activities that start before this ISO date or datetime.")
        ] = None,
        page: Page = 1,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        """The connected athlete's activities as compact summaries: sport, start time, distance (m, km, mi),
        moving and elapsed time, elevation gain, speed, heart rate, power, cadence, suffer score, kudos.
        Strava returns newest first, but oldest first when `after` is set. `more_available` is true when
        the page came back full, so ask for the next page. Use get_activity for one activity's full detail."""
        paging = _paging(page, per_page)
        activities = await client.get(
            "/athlete/activities",
            {"after": _epoch(after, "after"), "before": _epoch(before, "before"), **paging},
        )
        return {
            **paging,
            "count": len(activities),
            "more_available": len(activities) == paging["per_page"],
            "activities": [compact_activity(a) for a in activities],
        }

    @server.tool(annotations=READ_ONLY)
    async def get_activity(
        activity_id: int,
        include_efforts: Annotated[
            bool,
            Field(
                description="Also return every segment effort, best efforts (fastest 400m, 1k, 5k, ...) and "
                "per-mile splits. Much larger."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Full detail for one activity: description, calories, device, gear, laps, per-km splits
        (splits_metric), heart rate, power, photos and achievement counts. Map polylines are removed."""
        detail = await client.get_object(
            f"/activities/{activity_id}", {"include_all_efforts": include_efforts}
        )
        if not include_efforts:
            for key in ("segment_efforts", "splits_standard", "best_efforts"):
                detail.pop(key, None)
        stripped: dict[str, Any] = strip_polylines(detail)
        return stripped

    @server.tool(annotations=READ_ONLY)
    async def get_activity_laps(activity_id: int) -> dict[str, Any]:
        """Laps of one activity, with distance, time, speed, heart rate, cadence and power per lap.
        Useful for interval sessions and races split by lap."""
        return {"laps": await client.get(f"/activities/{activity_id}/laps")}

    @server.tool(annotations=READ_ONLY)
    async def get_activity_zones(activity_id: int) -> dict[str, Any]:
        """Time spent in each heart-rate and power zone during one activity. Strava may require a
        subscription for this data."""
        return {"zones": await client.get(f"/activities/{activity_id}/zones")}

    @server.tool(annotations=READ_ONLY)
    async def get_activity_streams(
        activity_id: int,
        keys: Annotated[
            list[str] | None,
            Field(
                description="Streams to fetch, any of: time, distance, latlng, altitude, velocity_smooth, "
                "heartrate, cadence, watts, temp, moving, grade_smooth. Defaults to time, distance, "
                "altitude, heartrate, cadence, watts and velocity_smooth."
            ),
        ] = None,
        max_points: Annotated[
            int,
            Field(description=f"Points kept per stream after even downsampling, 2 to {MAX_STREAM_POINTS}."),
        ] = 500,
    ) -> dict[str, Any]:
        """Second-by-second data recorded during one activity (heart rate, power, cadence, altitude, speed in
        m/s, position), evenly downsampled so every stream lines up point for point. Use it for pacing,
        heart-rate drift, or interval analysis. Each stream reports its original_size."""
        streams = await client.get(
            f"/activities/{activity_id}/streams",
            {"keys": ",".join(keys or DEFAULT_STREAM_KEYS), "key_by_type": True},
        )
        points = max(2, min(max_points, MAX_STREAM_POINTS))
        return {"activity_id": activity_id, "streams": downsample_streams(streams, points)}

    @server.tool(annotations=READ_ONLY)
    async def list_starred_segments(page: Page = 1, per_page: PerPage = 30) -> dict[str, Any]:
        """Segments the connected athlete has starred, with distance, grade, elevation, location and
        their PR."""
        return {"segments": strip_polylines(await client.get("/segments/starred", _paging(page, per_page)))}

    @server.tool(annotations=READ_ONLY)
    async def get_segment(segment_id: int) -> dict[str, Any]:
        """Detail for one segment: distance, average and max grade, elevation, climb category, location,
        effort and athlete counts, and the connected athlete's PR (athlete_segment_stats)."""
        segment: dict[str, Any] = strip_polylines(await client.get_object(f"/segments/{segment_id}"))
        return segment

    @server.tool(annotations=READ_ONLY)
    async def list_segment_efforts(
        segment_id: int,
        start_date_local: Annotated[
            str | None, Field(description="Only efforts on or after this ISO 8601 datetime.")
        ] = None,
        end_date_local: Annotated[
            str | None, Field(description="Only efforts on or before this ISO 8601 datetime.")
        ] = None,
        per_page: PerPage = 30,
    ) -> dict[str, Any]:
        """The connected athlete's efforts on one segment, with elapsed time, heart rate, power and the
        activity each came from, so you can track progress on a climb or sprint. Strava may require a
        subscription for this data."""
        efforts = await client.get(
            "/segment_efforts",
            {
                "segment_id": segment_id,
                "start_date_local": start_date_local,
                "end_date_local": end_date_local,
                "per_page": _paging(1, per_page)["per_page"],
            },
        )
        return {"efforts": strip_polylines(efforts)}

    @server.tool(annotations=READ_ONLY)
    async def list_routes(page: Page = 1, per_page: PerPage = 30) -> dict[str, Any]:
        """Routes the connected athlete created, with distance, elevation gain, type and estimated time."""
        routes = await client.get(f"/athletes/{await client.athlete_id()}/routes", _paging(page, per_page))
        return {"routes": strip_polylines(routes)}

    @server.tool(annotations=READ_ONLY)
    async def get_gear(
        gear_id: Annotated[
            str,
            Field(
                pattern=r"^[A-Za-z0-9]+$",
                description="A gear id from an activity's gear_id or get_athlete, e.g. b1234567 or g7654321.",
            ),
        ],
    ) -> dict[str, Any]:
        """One bike or pair of shoes: brand, model, nickname and total distance in meters. Gear ids
        (b... for bikes, g... for shoes) appear as gear_id on activities and in get_athlete."""
        return await client.get_object(f"/gear/{gear_id}")

    @server.tool(annotations=READ_ONLY)
    async def list_clubs(page: Page = 1, per_page: PerPage = 30) -> dict[str, Any]:
        """Clubs the connected athlete belongs to, with sport type, location and member count."""
        return {"clubs": await client.get("/athlete/clubs", _paging(page, per_page))}

    return server


def main(argv: list[str] | None = None) -> None:
    """`mcp-strava` serves MCP over stdio; `mcp-strava auth` runs the one-time browser login."""
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["auth"]:
        try:
            raise SystemExit(run_auth(args[1:]))
        except KeyboardInterrupt:  # Ctrl-C while prompting or waiting for the browser
            raise SystemExit(130) from None
    parser = argparse.ArgumentParser(
        prog="mcp-strava",
        description="Read-only MCP server for your Strava data. An MCP client such as Claude starts it "
        "and talks to it over stdin and stdout.",
        epilog="commands:\n  auth    authorize Strava access in your browser, once; see `mcp-strava auth -h`",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('mcp-strava')}")
    parser.parse_args(args)
    build_server(StravaClient(TokenStore(token_path())), close_client_on_shutdown=True).run()
