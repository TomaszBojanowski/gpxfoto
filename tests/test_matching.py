"""match_photo(s): the per-photo rule shared by every interface."""
from datetime import datetime, timedelta, timezone

import pytest

from gpxfoto.engine.matching import PhotoResult, Summary, match_photo, match_photos, summarize
from gpxfoto.engine.photos import TZ_CAMERA, TZ_MANUAL, Photo
from gpxfoto.engine.track import Track, find_stops, locate

WARSAW = timezone(timedelta(hours=2))
T0 = datetime(2024, 5, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()
POINTS = [(T0, 50.0, 20.0, 200.0), (T0 + 100, 50.001, 20.002, 210.0)]


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def track():
    return Track(["track.gpx"], True, list(POINTS))


def photo(local_time, has_location=False, path="a.jpg"):
    taken = datetime(2024, 5, 1, *local_time, tzinfo=WARSAW)
    return Photo(path, taken, TZ_CAMERA, None, has_location)


@pytest.mark.parametrize("local_time, correction", [
    ((12, 0, 50), 0), ((12, 0, 0), 0), ((12, 1, 40), 0), ((12, 0, 0), 50), ((12, 2, 0), -70),
    ((11, 59, 0), 0), ((12, 5, 0), 0),
])
def test_position_is_the_one_locate_gives(track, local_time, correction):
    result = match_photo(photo(local_time), [track], correction, 120)
    t = datetime(2024, 5, 1, *local_time, tzinfo=WARSAW).timestamp() + correction
    expected = locate(track.points, track.times, t, 120)
    if expected[0] is None:
        assert (result.lat, result.reason) == (None, expected[1])
    else:
        assert (result.lat, result.lon, result.ele, result.gap, result.reason) == (*expected, None)
    assert result.time == datetime(2024, 5, 1, *local_time, tzinfo=WARSAW) + timedelta(
        seconds=correction)
    assert result.time.utcoffset() == timedelta(hours=2)
    assert result.time_utc == result.time and result.time_utc.tzinfo == timezone.utc


def test_photo_with_a_location_is_skipped_unless_overwritten(track):
    taken = photo((12, 0, 50), has_location=True)
    assert match_photo(taken, [track], 0, 120) == PhotoResult(
        taken, reason="already has a location")
    assert match_photo(taken, [track], 0, 120, overwrite=True).reason is None


def test_photo_without_a_capture_time_keeps_its_reason(track):
    undated = Photo("a.jpg", None, None, "no capture time in EXIF", False)
    assert match_photo(undated, [track], 0, 120) == PhotoResult(
        undated, reason="no capture time in EXIF")


def test_correction_beyond_the_calendar_is_out_of_range(track):
    taken = photo((12, 0, 50))
    assert match_photo(taken, [track], 1e12, 120) == PhotoResult(
        taken, reason="the corrected capture time is out of range")


def test_skipped_photo_keeps_its_corrected_time(track):
    result = match_photo(photo((13, 0, 0)), [track], 0, 120)
    assert result.reason == "58 min after the end of the track"
    assert result.time == datetime(2024, 5, 1, 13, 0, 0, tzinfo=WARSAW)
    assert result.lat is None


def test_photos_are_matched_in_order_and_counted(track):
    photos = [photo((12, 0, 50), path="a.jpg"), photo((13, 0, 0), path="b.jpg"),
              photo((12, 0, 10), has_location=True, path="c.jpg")]
    results = match_photos(photos, [track], 0, 120)
    assert [r.photo.path for r in results] == ["a.jpg", "b.jpg", "c.jpg"]
    assert summarize(results) == Summary(matched=1, skipped=2, at_stops=0)
    results = match_photos(photos, [track], 0, 120, overwrite=True)
    assert summarize(results) == Summary(matched=2, skipped=1, at_stops=0)


def s5ii_photo(local_time, camera_utc_time, tz_source=TZ_CAMERA, offset=WARSAW):
    taken = datetime(2024, 5, 1, *local_time, tzinfo=offset)
    camera_utc = datetime(2024, 5, 1, *camera_utc_time, tzinfo=timezone.utc)
    return Photo("P1000123.JPG", taken, tz_source, None, False, camera_utc)


def test_matching_capture_time_has_no_time_check(track):
    assert match_photo(s5ii_photo((12, 0, 50), (10, 0, 50)), [track], 0, 120).time_check is None


def test_capture_time_against_the_camera_utc_time(track):
    # --timezone +03:00 for a camera at +02:00
    result = match_photo(s5ii_photo((12, 0, 50), (10, 0, 50), TZ_MANUAL,
                                    timezone(timedelta(hours=3))), [track], 0, 120)
    assert result.time_check == (-3600, timezone(timedelta(hours=2)))
    assert result.reason == "59 min before the start of the track"


def test_time_check_comes_before_the_correction(track):
    # The correction does not change the comparison: both times share the clock
    result = match_photo(s5ii_photo((11, 59, 0), (9, 59, 0)), [track], 110, 120)
    assert (result.reason, result.time_check) == (None, None)
    out_of_range = match_photo(s5ii_photo((12, 0, 50), (9, 0, 50)), [track], 1e12, 120)
    assert out_of_range.reason == "the corrected capture time is out of range"
    assert out_of_range.time_check == (3600, None)


def test_photo_taken_during_a_stop_gets_its_position():
    points = [(T0 + i, 50.0 + (i % 3 - 1) * 1e-5, 20.0, 200.0) for i in range(300)]
    track = Track(["stop.gpx"], True, points, find_stops(points))
    [stop] = track.stops
    result = match_photo(photo((12, 2, 0)), [track], 0, 120)
    assert (result.lat, result.lon, result.ele, result.stop) == (
        stop.lat, stop.lon, stop.elevation, stop)
    unpinned = match_photo(photo((12, 2, 0)), [Track(["stop.gpx"], True, points)], 0, 120)
    assert unpinned.stop is None and unpinned.lat != stop.lat
    assert summarize([result, unpinned]) == Summary(matched=2, skipped=0, at_stops=1)
