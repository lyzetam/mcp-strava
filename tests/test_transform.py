from mcp_strava.transform import compact_activity, downsample_streams, hms, strip_polylines

SUMMARY = {
    "id": 101,
    "name": "Morning Run",
    "type": "Run",
    "sport_type": "TrailRun",
    "start_date_local": "2026-10-12T06:30:00Z",
    "distance": 10000.0,
    "moving_time": 3725,
    "elapsed_time": 3900,
    "total_elevation_gain": 120.4,
    "average_speed": 2.685,
    "max_speed": 4.1,
    "average_heartrate": 151.2,
    "max_heartrate": 178.0,
    "average_cadence": 84.5,
    "suffer_score": 72,
    "kudos_count": 5,
    "achievement_count": 2,
    "gear_id": "g123",
    "trainer": False,
    "commute": False,
    "private": True,
    "map": {"id": "a101", "summary_polyline": "abc~~", "resource_state": 2},
    "athlete": {"id": 42},
}


def test_compact_activity_keeps_the_useful_fields_and_adds_units():
    compact = compact_activity(SUMMARY)

    assert compact == {
        "id": 101,
        "name": "Morning Run",
        "sport_type": "TrailRun",
        "start_date_local": "2026-10-12T06:30:00Z",
        "distance_m": 10000.0,
        "distance_km": 10.0,
        "distance_mi": 6.21,
        "moving_time_s": 3725,
        "moving_time": "1:02:05",
        "elapsed_time_s": 3900,
        "total_elevation_gain_m": 120.4,
        "average_speed_mps": 2.685,
        "max_speed_mps": 4.1,
        "average_heartrate": 151.2,
        "max_heartrate": 178.0,
        "average_watts": None,
        "average_cadence": 84.5,
        "suffer_score": 72,
        "kudos_count": 5,
        "achievement_count": 2,
        "gear_id": "g123",
        "trainer": False,
        "commute": False,
        "private": True,
    }


def test_compact_activity_falls_back_to_type_and_tolerates_missing_numbers():
    compact = compact_activity({"id": 1, "name": "Yoga", "type": "Yoga"})

    assert compact["sport_type"] == "Yoga"
    assert compact["distance_km"] is None
    assert compact["moving_time"] is None


def test_hms_formats_hours_minutes_seconds():
    assert hms(0) == "0:00:00"
    assert hms(59) == "0:00:59"
    assert hms(3725) == "1:02:05"
    assert hms(90061) == "25:01:01"


def test_strip_polylines_removes_them_at_any_depth():
    detail = {
        "map": {"id": "a1", "polyline": "xyz", "summary_polyline": "abc"},
        "segment_efforts": [{"segment": {"map": {"polyline": "deep"}, "name": "Hill"}}],
        "name": "Ride",
    }

    assert strip_polylines(detail) == {
        "map": {"id": "a1"},
        "segment_efforts": [{"segment": {"map": {}, "name": "Hill"}}],
        "name": "Ride",
    }
    assert detail["map"]["polyline"] == "xyz", "input must not be mutated"


def test_downsample_keeps_endpoints_and_reports_sizes():
    streams = {
        "time": {
            "data": list(range(1001)),
            "series_type": "distance",
            "original_size": 1001,
            "resolution": "high",
        },
        "heartrate": {"data": [120, 121, 122], "series_type": "distance", "original_size": 3},
    }

    result = downsample_streams(streams, max_points=11)

    assert result["time"]["data"] == [0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    assert result["time"]["original_size"] == 1001
    assert result["time"]["returned_points"] == 11
    assert result["time"]["series_type"] == "distance"
    assert result["heartrate"]["data"] == [120, 121, 122]
    assert result["heartrate"]["returned_points"] == 3
