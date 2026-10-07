"""GPX parsing (load_gpx, _parse_time) and position lookup (locate)."""
import os
import time
from datetime import datetime, timezone

import pytest

from conftest import write_gpx
from gpxfoto import i18n
from gpxfoto.engine import track as track_module
from gpxfoto.engine.track import _distance_m, _parse_time, load_gpx, locate


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
    # Too close to the ends of the calendar to be shown in local time
    "0001-01-02T23:59:59Z", "9999-12-30T00:00:00Z",
])
def test_points_without_usable_time_are_skipped(tmp_path, time_text):
    path = write_gpx(tmp_path / "track.gpx", [
        ("2026-06-01T10:00:00Z", 50.0, 19.0, 1.0),
        (time_text, 50.5, 19.5, 2.0),
        ("2026-06-01T10:00:10Z", 51.0, 20.0, 3.0),
    ])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, 1.0), (T0 + 10, 51.0, 20.0, 3.0)]


@pytest.mark.parametrize("time_text, expected", [
    ("0001-01-03T00:00:00Z", utc(1, 1, 3, 0, 0, 0)),
    ("9999-12-29T23:59:59Z", utc(9999, 12, 29, 23, 59, 59)),
])
def test_points_near_the_ends_of_the_calendar_are_kept(tmp_path, time_text, expected):
    path = write_gpx(tmp_path / "track.gpx", [(time_text, 50.0, 19.0, None)])
    assert load_gpx([path]) == [(expected, 50.0, 19.0, None)]


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


@pytest.mark.parametrize("ele", ["nan", "inf", "-inf", "n/a", "1,5", " ",
                                 "1e30", "100000.1", "-1e6"])
def test_unusable_elevation_keeps_the_point_without_it(tmp_path, ele):
    path = write_gpx(tmp_path / "track.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, ele)])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, None)]


@pytest.mark.parametrize("ele", [100000, -100000])
def test_elevation_at_the_limits_is_kept(tmp_path, ele):
    path = write_gpx(tmp_path / "track.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, ele)])
    assert load_gpx([path]) == [(T0, 50.0, 19.0, ele)]


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


@pytest.mark.parametrize("first, last", [
    ((50.0, 19.0, -97746.14903322658), (50.0, 19.0, 100000.0)),
    ((-51.01210851648959, 19.0, None), (90.0, 19.0, None)),
])
def test_interpolation_never_goes_beyond_the_points(first, last):
    # first + (last - first) * 1.0 is not exactly last for these values
    points, times = track((T0, *first), (T0 + 1, *last))
    assert locate(points, times, T0 + 1, 120) == (*last, 0)


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


def test_locate_movement_of_exactly_100_m_is_movement():
    """The plan accepts a break if the position changed by less than 100 m."""
    points, times = track((T0, 0.0, 0.0, None), (T0 + 600, 0.0, 0.0008993216059187306, None))
    assert _distance_m(points[0], points[1]) == 100.0
    assert locate(points, times, T0 + 300, 120) == (
        None, "gap in the track recording, nearest point 5 min away")


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
    monkeypatch.setattr(i18n, "_", translate)


@pytest.mark.parametrize("t, reason", [
    (T0 - 30, "««30 s» before the start of the track»"),
    (T0 + 20180, "««3 min» after the end of the track»"),
    (T0 + 7260, "«gap in the track recording, nearest point «2 h 1 min» away»"),
])
def test_reasons_are_translated(marked, t, reason):
    points, times = track((T0, 50.0, 19.0, None), (T0 + 20000, 51.0, 20.0, None))
    assert locate(points, times, t, 10) == (None, reason)


# load_track and match

def test_load_track_keeps_the_files_and_the_times(tmp_path):
    first = write_gpx(tmp_path / "a.gpx", [("2026-06-01T10:00:10Z", 50.0, 19.0, None)])
    second = write_gpx(tmp_path / "b.gpx", [("2026-06-01T10:00:00Z", 51.0, 20.0, 5.0)])
    track = track_module.load_track([first, second])
    assert track.files == (first, second) and track.named
    assert track.points == [(T0, 51.0, 20.0, 5.0), (T0 + 10, 50.0, 19.0, None)]
    assert (track.times, track.first, track.last) == ([T0, T0 + 10], T0, T0 + 10)


def test_load_track_without_points_is_none(tmp_path):
    empty = write_gpx(tmp_path / "a.gpx", [(None, 50.0, 19.0, None)])
    assert track_module.load_track([empty]) is None


def test_match_gives_the_position_or_the_reason(tmp_path):
    loaded = track_module.Track(["t.gpx"], True, [(T0, 50.0, 19.0, None),
                                                  (T0 + 100, 51.0, 20.0, None)])
    assert track_module.match([loaded], T0 + 50, 120) == track_module.Match(
        50.5, 19.5, None, 50, loaded, files=("t.gpx",))
    assert track_module.match([loaded], T0 + 400, 120) == track_module.Match(
        track=loaded, reason="5 min after the end of the track", covered=False)


def straight(start, seconds, lat, step=1, named=False, files=None):
    """A Track going east from (lat, 19.0) at about 7 m/s, one point every step seconds."""
    points = [(start + i, lat, 19.0 + i * 1e-4, None) for i in range(0, seconds + 1, step)]
    return track_module.Track(files or [f"{lat}.gpx"], named, points)


def test_match_never_interpolates_between_tracks():
    morning, evening = straight(T0, 100, 50.0), straight(T0 + 3600, 100, 51.0)
    found = track_module.match([morning, evening], T0 + 1800, 120)
    assert (found.lat, found.covered) == (None, False)
    assert found.reason == "28 min after the end of the track"
    assert track_module.match([morning, evening], T0 + 3650, 120).track is evening


def test_match_prefers_a_position_between_two_close_points():
    # The watch records every second; the phone stopped 60 s before
    watch = straight(T0, 600, 50.0)
    phone = straight(T0, 540, 50.0001, named=True)
    assert track_module.match([phone, watch], T0 + 570, 120).track is watch
    # Both place it between two points: the named track wins
    assert track_module.match([watch, phone], T0 + 300, 120).track is phone


def test_match_prefers_more_frequent_recording_then_the_earlier_start():
    sparse = straight(T0, 600, 50.0, step=10)
    dense = straight(T0 + 1, 599, 50.0001)
    assert track_module.match([sparse, dense], T0 + 305, 120).track is dense
    early, late = straight(T0, 600, 50.0), straight(T0 + 1, 599, 50.0001)
    assert track_module.match([late, early], T0 + 305, 120).track is early


def test_match_reports_a_gap_with_the_files_around_it():
    points = [(T0, 50.0, 19.0, None), (T0 + 1000, 50.1, 19.0, None)]
    loaded = track_module.Track(["a.gpx", "b.gpx"], True, points, sources=[0, 1])
    found = track_module.match([loaded], T0 + 500, 120)
    assert found.reason == "gap in the track recording, nearest point 8 min away"
    assert (found.files, found.covered) == (("a.gpx", "b.gpx"), True)


@pytest.mark.parametrize("times, interval", [
    ([0, 1, 2, 3], 1), ([0, 5, 10, 12, 20], 5), ([0, 0.2, 0.4], 1), ([0], 1), ([0, 0, 0], 1)])
def test_track_interval(times, interval):
    points = [(T0 + t, 50.0, 19.0, None) for t in times]
    assert track_module.Track(["a.gpx"], True, points).interval == interval


@pytest.mark.parametrize("t, rank", [
    (T0, track_module.INSIDE), (T0 + 50, track_module.INSIDE),
    (T0 + 100, track_module.INSIDE), (T0 + 130, track_module.NEAR),
    (T0 - 100, track_module.NEAR), (T0 + 500, track_module.ACROSS_BREAK)])
def test_placement_rank(t, rank):
    # Points 100 s apart, then a break of 1000 s
    points = [(T0 + s, 50.0, 19.0, None) for s in (0, 100, 1100)]
    loaded = track_module.Track(["a.gpx"], True, points)
    assert track_module.placement_rank(loaded, t, 120) == rank


def test_track_knows_the_file_of_each_point(tmp_path):
    first = write_gpx(tmp_path / "a.gpx", [("2026-06-01T10:00:00Z", 50.0, 19.0, None),
                                           ("2026-06-01T10:00:20Z", 50.0, 19.0, None)])
    second = write_gpx(tmp_path / "b.gpx", [("2026-06-01T10:00:10Z", 51.0, 20.0, None),
                                            ("2026-06-01T10:00:30Z", 51.0, 20.0, None)])
    track = track_module.load_track([first, second])
    assert track.sources == [0, 1, 0, 1]
    assert track.files_at(T0 - 5) == (first,)
    assert track.files_at(T0) == (first,)
    assert track.files_at(T0 + 5) == (first, second)
    assert track.files_at(T0 + 10) == (second,)
    assert track.files_at(T0 + 40) == (second,)
    assert track_module.load_track([first]).sources is None
    assert track_module.load_track([first]).files_at(T0 + 5) == (first,)


def test_found_tracks_that_disagree_place_nothing():
    # Two found recordings of the same time, 1.1 km apart
    mine = straight(T0, 600, 50.0, files=["/t/mine.gpx"])
    other = straight(T0, 600, 50.01, files=["/t/other.gpx"])
    found = track_module.match([mine, other], T0 + 300, 120)
    assert (found.lat, found.conflict) == (None, other)
    assert found.reason == "the tracks mine.gpx and other.gpx put this photo 1.1 km apart"
    # Labels are the caller's
    found = track_module.match([mine, other], T0 + 300, 120, label=str)
    assert found.reason == "the tracks /t/mine.gpx and /t/other.gpx put this photo 1.1 km apart"


def test_found_tracks_that_agree_place_the_photo():
    mine = straight(T0, 600, 50.0)
    other = straight(T0, 600, 50.0015)     # 167 m north
    found = track_module.match([mine, other], T0 + 300, 120)
    assert (found.track, found.reason) == (mine, None)


def test_named_track_is_never_overruled():
    mine = straight(T0, 600, 50.0, named=True)
    other = straight(T0, 600, 50.01)
    found = track_module.match([other, mine], T0 + 300, 120)
    assert (found.track, found.reason) == (mine, None)


def test_only_equally_good_placements_are_compared():
    # The other track ended 100 s ago: its last point is no evidence against this one
    mine = straight(T0, 600, 50.0)
    other = straight(T0, 200, 50.01)
    found = track_module.match([mine, other], T0 + 300, 120)
    assert (found.track, found.reason) == (mine, None)


# find_tracks

def test_find_tracks(tmp_path):
    tracks = tmp_path / "tracks"
    for name in ["b.gpx", "a.GPX", "notes.txt", ".hidden.gpx", "sub/c.gpx", ".git/d.gpx"]:
        (tracks / name).parent.mkdir(parents=True, exist_ok=True)
        (tracks / name).write_text("")
    (tracks / "dir.gpx").mkdir()
    os.symlink(tracks / "sub", tracks / "link")
    named = tmp_path / "named.gpx"
    named.write_text("")
    os.symlink(named, tracks / "named-link.gpx")
    paths = [str(named), str(tracks), str(tracks / "b.gpx"), str(named)]
    assert track_module.find_tracks(paths, recursive=False) == (
        [str(named), str(tracks / "b.gpx")], [str(tracks / "a.GPX")])
    assert track_module.find_tracks(paths, recursive=True) == (
        [str(named), str(tracks / "b.gpx")], [str(tracks / "a.GPX"), str(tracks / "sub" / "c.gpx")])
    # A missing file is named, so that reading it reports the error
    assert track_module.find_tracks([str(tmp_path / "missing.gpx")], False) == (
        [str(tmp_path / "missing.gpx")], [])


# quick_span and tracks_needed

def write(path, text, encoding="utf-8"):
    path.write_bytes(text.encode(encoding))
    return str(path)


def span_of(*times):
    return tuple(_parse_time(t).timestamp() for t in times)


@pytest.mark.parametrize("text, expected", [
    ("", ()),
    ("<gpx/>", ()),
    ("<gpx><trk><trkseg></trkseg></trk></gpx>", ()),
    ('<gpx><time>2026-06-01T10:00:00Z</time><trk><trkseg>'
     '<trkpt lat="1" lon="2"><time>2026-06-01T10:00:05Z</time></trkpt>'
     '<trkpt lat="1" lon="2"><time>2026-06-01T09:59:58Z</time></trkpt>'
     "</trkseg></trk></gpx>",
     span_of("2026-06-01T09:59:58Z", "2026-06-01T10:00:05Z")),
    # Namespace prefixes, attributes, spaces and offsets
    ('<g:gpx xmlns:g="x"><g:trkpt><g:time a="1"> 2026-06-01T12:00:00+02:00 </g:time></g:trkpt>'
     "<g:trkpt><g:time>2026-06-01T10:00:01.5Z</g:time></g:trkpt><g:time/></g:gpx>",
     span_of("2026-06-01T10:00:00Z", "2026-06-01T10:00:01.5Z")),
    # A time the full reader cannot use is left out
    ("<gpx><time>yesterday</time><time>2026-06-01T10:00:00Z</time></gpx>",
     span_of("2026-06-01T10:00:00Z", "2026-06-01T10:00:00Z")),
    # <timestamp> is another element
    ("<gpx><timestamp>2020-01-01T00:00:00Z</timestamp><time>2026-06-01T10:00:00Z</time></gpx>",
     span_of("2026-06-01T10:00:00Z", "2026-06-01T10:00:00Z")),
    # Not sure: a reference, CDATA or a comment inside the time, a DOCTYPE
    ("<gpx><time>&#50;026-06-01T10:00:00Z</time></gpx>", None),
    ("<gpx><time><![CDATA[2026-06-01T10:00:00Z]]></time></gpx>", None),
    ("<gpx><time>2026-06-01<!-- x -->T10:00:00Z</time></gpx>", None),
    ('<!DOCTYPE gpx [<!ENTITY t "2026-06-01T10:00:00Z">]><gpx><time>&t;</time></gpx>', None),
])
def test_quick_span(tmp_path, text, expected):
    assert track_module.quick_span(write(tmp_path / "t.gpx", text)) == expected


@pytest.mark.parametrize("encoding, declared, expected", [
    ("utf-8", "UTF-8", True), ("latin-1", "ISO-8859-1", True), ("cp1250", "windows-1250", True),
    ("utf-16", "UTF-16", False), ("utf-8", "x-unknown", False),
])
def test_quick_span_only_for_encodings_that_keep_ascii(tmp_path, encoding, declared, expected):
    text = (f'<?xml version="1.0" encoding="{declared}"?><gpx><name>Zürich</name>'
            "<time>2026-06-01T10:00:00Z</time></gpx>")
    span = track_module.quick_span(write(tmp_path / "t.gpx", text, encoding))
    assert span == (span_of("2026-06-01T10:00:00Z", "2026-06-01T10:00:00Z") if expected else None)


def test_quick_span_of_a_real_garmin_track(tmp_path):
    points = [(f"2026-06-01T10:{i // 60:02d}:{i % 60:02d}.000Z", 50.0, 19.0, 200.0)
              for i in range(300)]
    path = write_gpx(tmp_path / "g.gpx", points, garmin=True)
    loaded = load_gpx([path])
    assert track_module.quick_span(path) == (loaded[0][0], loaded[-1][0])


def test_quick_span_of_a_large_or_missing_file(tmp_path, monkeypatch):
    path = write(tmp_path / "t.gpx", "<gpx><time>2026-06-01T10:00:00Z</time></gpx>")
    monkeypatch.setattr(track_module, "QUICK_SCAN_LIMIT", 10)
    assert track_module.quick_span(path) is None
    assert track_module.quick_span(str(tmp_path / "missing.gpx")) is None


def test_tracks_needed():
    spans = {"a": (100.0, 200.0), "b": (1000.0, 2000.0), "c": (), "d": None, "e": (300.0, 400.0)}
    assert track_module.tracks_needed(spans, [50.0, 450.0], 60) == ["a", "d", "e"]
    assert track_module.tracks_needed(spans, [39.0, 2061.0], 60) == ["d"]
    assert track_module.tracks_needed(spans, [], 60) == ["d"]
    assert track_module.tracks_needed(spans, [150.0], 0) == ["a", "d"]
