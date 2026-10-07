"""GPX parsing (load_gpx, _parse_time), position lookup (locate) and durations."""
import os
import time
from datetime import datetime, timezone

import pytest

from conftest import write_gpx
from gpxfoto.engine import track as track_module
from gpxfoto.engine.track import _format_duration, _parse_time, load_gpx, locate


def utc(*fields):
    return datetime(*fields, tzinfo=timezone.utc).timestamp()


T0 = utc(2026, 6, 1, 10, 0, 0)


@pytest.fixture(autouse=True)
def english(monkeypatch):
    # Reason texts are compared exactly; make sure no catalogue translates them.
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def local_zone():
    """Switch the process time zone; restored afterwards."""
    saved = os.environ.get("TZ")

    def switch(name):
        os.environ["TZ"] = name
        time.tzset()

    yield switch
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def write_text(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# Same structure as an activity exported from Garmin Connect; the
# coordinates and times are made up.
GARMIN_POINT = """\
      <trkpt lat="{lat}" lon="{lon}">
        <ele>{ele}</ele>
        <time>{time}</time>
        <extensions>
          <ns3:TrackPointExtension>
            <ns3:atemp>21.0</ns3:atemp>
            <ns3:hr>{hr}</ns3:hr>
            <ns3:cad>45</ns3:cad>
          </ns3:TrackPointExtension>
        </extensions>
      </trkpt>
"""

GARMIN_POINTS = [
    ("49.29921561479568481445312500", "19.94962155260145664215087890625",
     "1012.4000244140625", "2026-06-01T07:28:09.000Z"),
    ("49.2992371506989002227783203125", "19.9496395327150821685791015625",
     "1012.5999755859375", "2026-06-01T07:28:10.000Z"),
    ("49.299258734285831451416015625", "19.949657507240772247314453125",
     "1013.2000122070312", "2026-06-01T07:28:11.000Z"),
]

GARMIN_GPX = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Garmin Connect" version="1.1"
  xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/11.xsd"
  xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
  xmlns="http://www.topografix.com/GPX/1/1"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" \
xmlns:ns2="http://www.garmin.com/xmlschemas/GpxExtensions/v3">
  <metadata>
    <link href="connect.garmin.com">
      <text>Garmin Connect</text>
    </link>
    <time>2026-06-01T07:00:00.000Z</time>
  </metadata>
  <trk>
    <name>Zakopane Hiking</name>
    <type>hiking</type>
    <trkseg>
{points}    </trkseg>
  </trk>
</gpx>
""".format(points="".join(GARMIN_POINT.format(lat=lat, lon=lon, ele=ele, time=t, hr=78 + i)
                          for i, (lat, lon, ele, t) in enumerate(GARMIN_POINTS)))


# load_gpx

def test_garmin_connect_file(tmp_path):
    path = write_text(tmp_path / "activity.gpx", GARMIN_GPX)
    assert load_gpx([path]) == [
        (utc(2026, 6, 1, 7, 28, 9), float(GARMIN_POINTS[0][0]), float(GARMIN_POINTS[0][1]),
         1012.4000244140625),
        (utc(2026, 6, 1, 7, 28, 10), float(GARMIN_POINTS[1][0]), float(GARMIN_POINTS[1][1]),
         1012.5999755859375),
        (utc(2026, 6, 1, 7, 28, 11), float(GARMIN_POINTS[2][0]), float(GARMIN_POINTS[2][1]),
         1013.2000122070312),
    ]


def test_metadata_time_is_not_a_point(tmp_path):
    path = write_text(tmp_path / "activity.gpx", GARMIN_GPX)
    times = [p[0] for p in load_gpx([path])]
    assert utc(2026, 6, 1, 7, 0, 0) not in times
    assert len(times) == len(GARMIN_POINTS)


def test_garmin_helper_file(tmp_path):
    path = write_gpx(tmp_path / "track.gpx", [
        ("2026-06-01T10:00:00.000Z", 50.0, 19.0, 210.5),
        ("2026-06-01T10:00:01.000Z", 50.0001, 19.0001, 211.0),
    ], garmin=True)
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 210.5), (T0 + 1, 50.0001, 19.0001, 211.0)]


def test_file_without_namespace(tmp_path):
    path = write_gpx(tmp_path / "plain.gpx", [
        ("2026-06-01T10:00:00Z", 50.0, 19.0, 210.0),
        ("2026-06-01T10:00:05Z", 50.001, 19.002, None),
    ], namespace=None)
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 210.0), (T0 + 5, 50.001, 19.002, None)]


def test_other_namespace(tmp_path):
    path = write_gpx(tmp_path / "gpx10.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, 1.0)],
                     namespace="http://www.topografix.com/GPX/1/0")
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 1.0)]


@pytest.mark.parametrize("text, expected", [
    ("2026-06-01T10:00:00Z", T0),
    ("2026-06-01T10:00:00z", T0),
    ("2026-06-01T10:00:00.5Z", T0 + 0.5),
    ("2026-06-01T10:00:00.123Z", T0 + 0.123),
    ("2026-06-01T10:00:00.123456Z", T0 + 0.123456),
    ("2026-06-01T12:00:00+02:00", T0),
    ("2026-06-01T04:30:00-05:30", T0),
    ("2026-06-01T12:00:00.250+02:00", T0 + 0.25),
    ("2026-06-01T10:00:00+00:00", T0),
    ("2026-06-01T10:00:00", T0),
    ("\n  2026-06-01T10:00:00Z\n  ", T0),
])
def test_time_formats(tmp_path, text, expected):
    path = write_gpx(tmp_path / "track.gpx", [(text, 50.0, 19.0, None)])
    assert load_gpx([path]) == [(pytest.approx(expected, abs=1e-6), 50.0, 19.0, None)]


@pytest.mark.parametrize("text", [
    "2026-06-01T10:00:00.5Z", "2026-06-01T12:00:00+02:00", "2026-06-01T10:00:00z",
    "2026-06-01T10:00:00",
])
def test_parse_time_returns_utc(text):
    parsed = _parse_time(text)
    assert parsed.tzinfo is timezone.utc
    assert parsed.replace(microsecond=0) == datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_naive_time_is_utc_not_local(tmp_path, local_zone):
    local_zone("IST-5:30")  # POSIX form, needs no tz database
    path = write_gpx(tmp_path / "track.gpx", [("2026-06-01T10:00:00", 50.0, 19.0, None)])
    assert load_gpx([path])[0][0] == T0
    assert _parse_time("2026-06-01T10:00:00").timestamp() == T0


@pytest.mark.parametrize("time_text", [
    None, "", "   ", "yesterday", "2026-13-01T10:00:00Z", "2026-06-01T25:00:00Z",
    "9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00+01:00",
])
def test_points_without_usable_time_are_skipped(tmp_path, time_text):
    path = write_gpx(tmp_path / "track.gpx", [
        ("2026-06-01T10:00:00Z", 50.0, 19.0, 1.0),
        (time_text, 50.5, 19.5, 2.0),
        ("2026-06-01T10:00:10Z", 51.0, 20.0, 3.0),
    ])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 1.0), (T0 + 10, 51.0, 20.0, 3.0)]


@pytest.mark.parametrize("lat, lon", [("north", 19.5), (50.5, ""), ("", 19.5)])
def test_points_with_unparsable_coordinates_are_skipped(tmp_path, lat, lon):
    path = write_gpx(tmp_path / "track.gpx", [
        ("2026-06-01T10:00:00Z", 50.0, 19.0, 1.0),
        ("2026-06-01T10:00:05Z", lat, lon, 2.0),
    ])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 1.0)]


def test_points_with_missing_coordinates_are_skipped(tmp_path):
    path = write_text(tmp_path / "track.gpx", """\
<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lon="19.0"><time>2026-06-01T10:00:00Z</time></trkpt>
<trkpt lat="50.0"><time>2026-06-01T10:00:01Z</time></trkpt>
<trkpt lat="50.0" lon="19.0"><time>2026-06-01T10:00:02Z</time></trkpt>
</trkseg></trk></gpx>
""")
    assert load_gpx([path]) == [(T0 + 2, 50.0, 19.0, None)]


def test_missing_or_empty_elevation_is_none(tmp_path):
    path = write_text(tmp_path / "track.gpx", """\
<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="50.0" lon="19.0"><time>2026-06-01T10:00:00Z</time></trkpt>
<trkpt lat="50.0" lon="19.0"><ele></ele><time>2026-06-01T10:00:01Z</time></trkpt>
<trkpt lat="50.0" lon="19.0"><ele>-12.5</ele><time>2026-06-01T10:00:02Z</time></trkpt>
</trkseg></trk></gpx>
""")
    assert [p[3] for p in load_gpx([path])] == [None, None, -12.5]


@pytest.mark.parametrize("lat, lon", [
    ("nan", "19.0"), ("50.0", "NaN"), ("inf", "19.0"), ("50.0", "-inf"), ("Infinity", "19.0"),
    ("90.0001", "19.0"), ("-90.5", "19.0"), ("50.0", "180.5"), ("50.0", "-181"),
])
def test_points_with_invalid_coordinates_are_skipped(tmp_path, lat, lon):
    path = write_gpx(tmp_path / "track.gpx", [
        ("2026-06-01T10:00:00Z", 50.0, 19.0, 1.0),
        ("2026-06-01T10:00:05Z", lat, lon, 2.0),
    ])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 1.0)]


@pytest.mark.parametrize("lat, lon", [(90, 180), (-90, -180), (0, 0)])
def test_coordinates_at_the_limits_are_kept(tmp_path, lat, lon):
    path = write_gpx(tmp_path / "track.gpx", [("2026-06-01T10:00:00Z", lat, lon, None)])
    assert load_gpx([path]) == [(T0, lat, lon, None)]


@pytest.mark.parametrize("ele", ["nan", "inf", "-inf", "n/a", "1,5", " "])
def test_unusable_elevation_keeps_the_point_without_it(tmp_path, ele):
    path = write_gpx(tmp_path / "track.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, ele)])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, None)]


def test_time_and_elevation_come_only_from_the_point_itself(tmp_path):
    path = write_text(tmp_path / "track.gpx", """\
<gpx xmlns="http://www.topografix.com/GPX/1/1" xmlns:x="urn:example:extension"><trk><trkseg>
<trkpt lat="50.0" lon="19.0"><ele>210</ele><time>2026-06-01T10:00:00Z</time>
<extensions><x:time>2026-06-01T11:00:00Z</x:time><x:ele>5</x:ele></extensions></trkpt>
<trkpt lat="51.0" lon="20.0"><extensions><x:time>2026-06-01T10:00:05Z</x:time></extensions></trkpt>
</trkseg></trk></gpx>
""")
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 210.0)]


def test_waypoints_and_route_points_are_ignored(tmp_path):
    path = write_text(tmp_path / "track.gpx", """\
<gpx xmlns="http://www.topografix.com/GPX/1/1">
<metadata><time>2026-06-01T09:00:00Z</time></metadata>
<wpt lat="10.0" lon="10.0"><ele>5</ele><time>2026-06-01T10:00:01Z</time><name>Hut</name></wpt>
<rte><rtept lat="20.0" lon="20.0"><time>2026-06-01T10:00:02Z</time></rtept></rte>
<trk><trkseg>
<trkpt lat="50.0" lon="19.0"><time>2026-06-01T10:00:00Z</time></trkpt>
</trkseg></trk>
</gpx>
""")
    assert load_gpx([path]) == [(T0, 50.0, 19.0, None)]


def test_several_segments_and_tracks(tmp_path):
    path = write_text(tmp_path / "track.gpx", """\
<gpx xmlns="http://www.topografix.com/GPX/1/1">
<trk><trkseg><trkpt lat="50.0" lon="19.0"><time>2026-06-01T10:00:00Z</time></trkpt></trkseg>
<trkseg><trkpt lat="50.1" lon="19.1"><time>2026-06-01T10:00:20Z</time></trkpt></trkseg></trk>
<trk><trkseg><trkpt lat="50.2" lon="19.2"><time>2026-06-01T10:00:10Z</time></trkpt></trkseg></trk>
</gpx>
""")
    assert load_gpx([path]) == [
        (T0, 50.0, 19.0, None), (T0 + 10, 50.2, 19.2, None), (T0 + 20, 50.1, 19.1, None)]


def test_points_are_sorted_by_time(tmp_path):
    path = write_gpx(tmp_path / "track.gpx", [
        ("2026-06-01T10:00:20Z", 50.2, 19.2, None),
        ("2026-06-01T10:00:00Z", 50.0, 19.0, None),
        ("2026-06-01T12:00:10+02:00", 50.1, 19.1, None),
    ])
    assert [p[1] for p in load_gpx([path])] == [50.0, 50.1, 50.2]


def test_several_files_are_merged_and_sorted(tmp_path):
    first = write_gpx(tmp_path / "watch.gpx", [
        ("2026-06-01T10:00:00Z", 50.0, 19.0, 200.0),
        ("2026-06-01T10:00:20Z", 50.2, 19.2, 220.0),
        ("2026-06-01T10:00:40Z", 50.4, 19.4, 240.0),
    ], garmin=True)
    second = write_gpx(tmp_path / "phone.gpx", [
        ("2026-06-01T10:00:10Z", 50.1, 19.1, None),
        ("2026-06-01T10:00:30Z", 50.3, 19.3, None),
    ], namespace=None)
    expected = [
        (T0, 50.0, 19.0, 200.0), (T0 + 10, 50.1, 19.1, None), (T0 + 20, 50.2, 19.2, 220.0),
        (T0 + 30, 50.3, 19.3, None), (T0 + 40, 50.4, 19.4, 240.0),
    ]
    assert load_gpx([first, second]) == expected
    assert load_gpx([str(second), str(first)]) == expected


def test_duplicate_times_keep_file_order(tmp_path):
    first = write_gpx(tmp_path / "a.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, None)])
    second = write_gpx(tmp_path / "b.gpx", [("2026-06-01T10:00:00Z", 51.0, 20.0, None)])
    assert load_gpx([first, second]) == [(T0, 50.0, 19.0, None), (T0, 51.0, 20.0, None)]
    assert load_gpx([second, first]) == [(T0, 51.0, 20.0, None), (T0, 50.0, 19.0, None)]


def test_no_files_or_no_timed_points(tmp_path):
    path = write_gpx(tmp_path / "route.gpx", [(None, 50.0, 19.0, 1.0)])
    assert load_gpx([]) == []
    assert load_gpx([path]) == []


@pytest.mark.parametrize("content, error", [
    (None, "No such file or directory"),
    ("", "no element found: line 1, column 0"),
    ("<gpx><trk><trkseg>", "no element found: line 1, column 18"),
    ("not xml", "syntax error: line 1, column 0"),
    ('<?xml version="1.0" encoding="EUC-JP"?><gpx/>', "multi-byte encodings are not supported"),
    ('<?xml version="1.0" encoding="bogus"?><gpx/>', "unknown encoding: bogus"),
])
def test_unreadable_file_is_reported_with_its_name(tmp_path, content, error):
    good = write_gpx(tmp_path / "good.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, None)])
    bad = tmp_path / "bad.gpx"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        load_gpx([good, bad])
    assert str(raised.value) == f"Cannot read the GPX file {bad}: {error}"


def test_directory_is_not_a_gpx_file(tmp_path):
    with pytest.raises(ValueError) as raised:
        load_gpx([tmp_path])
    assert str(raised.value) == f"Cannot read the GPX file {tmp_path}: Is a directory"


# locate

def track(*points):
    return list(points), [p[0] for p in points]


STEADY = track(
    (T0, 50.0, 19.0, 100.0),
    (T0 + 100, 50.5, 21.0, 200.0),
    (T0 + 200, 51.0, 20.0, 150.0),
)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_locate_exactly_on_a_point(index):
    points, times = STEADY
    t, lat, lon, ele = points[index]
    assert locate(points, times, t, 120) == (lat, lon, ele, 0)


def test_locate_interpolates_between_points():
    points, times = STEADY
    assert locate(points, times, T0 + 25, 120) == (50.125, 19.5, 125.0, 25)
    assert locate(points, times, T0 + 50, 120) == (50.25, 20.0, 150.0, 50)
    assert locate(points, times, T0 + 175, 120) == (50.875, 20.25, 162.5, 25)


def test_locate_with_fractional_time():
    points, times = STEADY
    assert locate(points, times, T0 + 99.5, 120) == pytest.approx(
        (50.4975, 20.99, 199.5, 0.5))


@pytest.mark.parametrize("first, second, offset, lon, gap", [
    (179.9995, -179.9995, 2.5, 179.99975, 2.5),
    (179.9995, -179.9995, 5, 180.0, 5),
    (179.9995, -179.9995, 7.5, -179.99975, 2.5),
    (-179.9995, 179.9995, 2.5, -179.99975, 2.5),
    (-179.9995, 179.9995, 7.5, 179.99975, 2.5),
])
def test_locate_across_the_180th_meridian(first, second, offset, lon, gap):
    # The two points are about 107 m apart, not around the whole globe
    points, times = track((T0, -16.5, first, None), (T0 + 10, -16.5, second, None))
    assert locate(points, times, T0 + offset, 120) == pytest.approx((-16.5, lon, None, gap))


@pytest.mark.parametrize("before_ele, after_ele, expected", [
    (None, 200.0, 200.0),
    (100.0, None, 100.0),
    (None, None, None),
])
def test_locate_with_elevation_missing(before_ele, after_ele, expected):
    points, times = track((T0, 50.0, 19.0, before_ele), (T0 + 100, 50.5, 21.0, after_ele))
    assert locate(points, times, T0 + 25, 120) == (50.125, 19.5, expected, 25)


@pytest.mark.parametrize("offset, gap", [(-60, 60), (-120, 120), (-0.5, 0.5)])
def test_locate_before_start_within_max_gap(offset, gap):
    points, times = STEADY
    assert locate(points, times, T0 + offset, 120) == (50.0, 19.0, 100.0, gap)


@pytest.mark.parametrize("offset, gap", [(60, 60), (120, 120)])
def test_locate_after_end_within_max_gap(offset, gap):
    points, times = STEADY
    assert locate(points, times, T0 + 200 + offset, 120) == (51.0, 20.0, 150.0, gap)


@pytest.mark.parametrize("offset, max_gap, reason", [
    (-121, 120, "2 min before the start of the track"),
    (-30, 10, "30 s before the start of the track"),
    (-7260, 120, "2 h 1 min before the start of the track"),
])
def test_locate_before_start_beyond_max_gap(offset, max_gap, reason):
    points, times = STEADY
    assert locate(points, times, T0 + offset, max_gap) == (None, reason)


@pytest.mark.parametrize("offset, max_gap, reason", [
    (121, 120, "2 min after the end of the track"),
    (30, 10, "30 s after the end of the track"),
    (90061, 120, "25 h 1 min after the end of the track"),
])
def test_locate_after_end_beyond_max_gap(offset, max_gap, reason):
    points, times = STEADY
    assert locate(points, times, T0 + 200 + offset, max_gap) == (None, reason)


def test_locate_outside_short_track_ignores_movement():
    points, times = track((T0, 50.0, 19.0, None))
    assert locate(points, times, T0, 120) == (50.0, 19.0, None, 0)
    assert locate(points, times, T0 - 5, 120) == (50.0, 19.0, None, 5)
    assert locate(points, times, T0 + 5, 120) == (50.0, 19.0, None, 5)


# Points 10 minutes apart; 0.01° of latitude is about 1.1 km.
MOVING = track((T0, 50.0, 19.0, 100.0), (T0 + 600, 50.01, 19.0, 160.0))


@pytest.mark.parametrize("offset, max_gap, reason", [
    (200, 120, "gap in the track recording, nearest point 3 min away"),
    (300, 120, "gap in the track recording, nearest point 5 min away"),
    (121, 120, "gap in the track recording, nearest point 2 min away"),
    (479, 120, "gap in the track recording, nearest point 2 min away"),
    (90, 60, "gap in the track recording, nearest point 90 s away"),
    (510, 60, "gap in the track recording, nearest point 90 s away"),
])
def test_locate_rejects_long_gap_with_movement(offset, max_gap, reason):
    points, times = MOVING
    assert locate(points, times, T0 + offset, max_gap) == (None, reason)


def test_locate_accepts_gap_within_max_gap_of_nearest_point():
    points, times = MOVING
    assert locate(points, times, T0 + 120, 120) == pytest.approx((50.002, 19.0, 112.0, 120))
    assert locate(points, times, T0 + 540, 120) == pytest.approx((50.009, 19.0, 154.0, 60))


def test_locate_accepts_long_gap_without_movement():
    # About 56 m apart after a 10 minute break, e.g. auto-pause.
    points, times = track((T0, 50.0, 19.0, 100.0), (T0 + 600, 50.0005, 19.0, 160.0))
    assert locate(points, times, T0 + 300, 120) == pytest.approx((50.00025, 19.0, 130.0, 300))


@pytest.mark.parametrize("lat_change, lon_change, accepted", [
    (0.000898, 0, True),   # 99.85 m
    (0.000901, 0, False),  # 100.19 m
    # At 50° N a degree of longitude is only 64% as long as at the equator:
    # 0.0013° ≈ 92.9 m, 0.0016° ≈ 114.4 m.
    (0, 0.0013, True),
    (0, 0.0016, False),
])
def test_locate_movement_limit_is_100_m(lat_change, lon_change, accepted):
    points, times = track((T0, 50.0, 19.0, None),
                          (T0 + 3600, 50.0 + lat_change, 19.0 + lon_change, None))
    result = locate(points, times, T0 + 1800, 120)
    if accepted:
        assert result == pytest.approx(
            (50.0 + lat_change / 2, 19.0 + lon_change / 2, None, 1800))
    else:
        assert result == (None, "gap in the track recording, nearest point 30 min away")


def test_locate_with_duplicate_timestamps():
    points, times = track(
        (T0, 50.0, 19.0, 100.0),
        (T0 + 10, 50.5, 19.5, 110.0),
        (T0 + 10, 51.0, 20.0, 120.0),
        (T0 + 20, 52.0, 21.0, 140.0),
    )
    assert locate(points, times, T0 + 10, 120) == (50.5, 19.5, 110.0, 0)
    assert locate(points, times, T0 + 5, 120) == (50.25, 19.25, 105.0, 5)
    assert locate(points, times, T0 + 15, 120) == (51.5, 20.5, 130.0, 5)


def test_locate_with_duplicate_timestamps_at_the_ends():
    points, times = track((T0, 50.0, 19.0, None), (T0, 51.0, 20.0, None),
                          (T0 + 10, 52.0, 21.0, None), (T0 + 10, 53.0, 22.0, None))
    assert locate(points, times, T0, 120) == (50.0, 19.0, None, 0)
    assert locate(points, times, T0 + 10, 120) == (52.0, 21.0, None, 0)
    assert locate(points, times, T0 + 5, 120) == (51.5, 20.5, None, 5)
    assert locate(points, times, T0 + 15, 120) == (53.0, 22.0, None, 5)


def test_locate_on_loaded_track(tmp_path):
    path = write_text(tmp_path / "activity.gpx", GARMIN_GPX)
    points = load_gpx([path])
    times = [p[0] for p in points]
    lat, lon, ele, gap = locate(points, times, utc(2026, 6, 1, 7, 28, 9) + 0.5, 120)
    assert (lat, lon, ele, gap) == pytest.approx((
        (float(GARMIN_POINTS[0][0]) + float(GARMIN_POINTS[1][0])) / 2,
        (float(GARMIN_POINTS[0][1]) + float(GARMIN_POINTS[1][1])) / 2,
        1012.5, 0.5))


# _format_duration

@pytest.mark.parametrize("seconds, text", [
    (0, "0 s"),
    (0.4, "0 s"),
    (59, "59 s"),
    (119, "119 s"),
    (119.4, "119 s"),
    (119.6, "2 min"),
    (120, "2 min"),
    (179, "2 min"),
    (180, "3 min"),
    (3600, "60 min"),
    (7199, "119 min"),
    (7199.4, "119 min"),
    (7199.6, "2 h 0 min"),
    (7200, "2 h 0 min"),
    (7259, "2 h 0 min"),
    (7260, "2 h 1 min"),
    (10799, "2 h 59 min"),
    (90061, "25 h 1 min"),
])
def test_format_duration(seconds, text):
    assert _format_duration(seconds) == text


MESSAGES = {
    "{duration} before the start of the track",
    "{duration} after the end of the track",
    "gap in the track recording, nearest point {duration} away",
    "{seconds} s",
    "{minutes} min",
    "{hours} h {minutes} min",
}


@pytest.fixture
def marked(monkeypatch):
    """Translate every message to «msgid», so untranslated text stands out."""
    def translate(text):
        assert text in MESSAGES, text  # looked up before formatting
        return f"«{text}»"
    monkeypatch.setattr(track_module, "_", translate)


@pytest.mark.parametrize("t, reason", [
    (T0 - 30, "««30 s» before the start of the track»"),
    (T0 + 20180, "««3 min» after the end of the track»"),
    (T0 + 7260, "«gap in the track recording, nearest point «2 h 1 min» away»"),
])
def test_reasons_are_translated(marked, t, reason):
    points, times = track((T0, 50.0, 19.0, None), (T0 + 20000, 51.0, 20.0, None))
    assert locate(points, times, t, 10) == (None, reason)
