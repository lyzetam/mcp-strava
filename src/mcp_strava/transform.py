"""Shape Strava responses so they fit in a model's context window."""

from typing import Any

METERS_PER_MILE = 1609.344
POLYLINE_KEYS = frozenset({"polyline", "summary_polyline"})


def hms(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def compact_activity(a: dict[str, Any]) -> dict[str, Any]:
    """The fields of a SummaryActivity worth reading, with distance and time in human units too."""
    distance = a.get("distance")
    moving = a.get("moving_time")
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "sport_type": a.get("sport_type") or a.get("type"),
        "start_date_local": a.get("start_date_local"),
        "distance_m": distance,
        "distance_km": round(distance / 1000, 2) if distance is not None else None,
        "distance_mi": round(distance / METERS_PER_MILE, 2) if distance is not None else None,
        "moving_time_s": moving,
        "moving_time": hms(moving) if moving is not None else None,
        "elapsed_time_s": a.get("elapsed_time"),
        "total_elevation_gain_m": a.get("total_elevation_gain"),
        "average_speed_mps": a.get("average_speed"),
        "max_speed_mps": a.get("max_speed"),
        "average_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "average_watts": a.get("average_watts"),
        "average_cadence": a.get("average_cadence"),
        "suffer_score": a.get("suffer_score"),
        "kudos_count": a.get("kudos_count"),
        "achievement_count": a.get("achievement_count"),
        "gear_id": a.get("gear_id"),
        "trainer": a.get("trainer"),
        "commute": a.get("commute"),
        "private": a.get("private"),
    }


def strip_polylines(value: Any) -> Any:
    """A copy of `value` without encoded map polylines, which are long and unreadable to a model."""
    if isinstance(value, dict):
        return {k: strip_polylines(v) for k, v in value.items() if k not in POLYLINE_KEYS}
    if isinstance(value, list):
        return [strip_polylines(v) for v in value]
    return value


def downsample_streams(streams: dict[str, dict[str, Any]], max_points: int) -> dict[str, dict[str, Any]]:
    """Evenly sample every stream down to at most `max_points`, always keeping the first and last point."""
    result = {}
    for name, stream in streams.items():
        data = stream.get("data") or []
        n = len(data)
        if n > max_points:
            data = [data[round(i * (n - 1) / (max_points - 1))] for i in range(max_points)]
        result[name] = {
            "data": data,
            "series_type": stream.get("series_type"),
            "original_size": stream.get("original_size", n),
            "returned_points": len(data),
        }
    return result
