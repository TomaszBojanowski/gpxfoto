"""match_photo(s): the per-photo rule shared by every interface."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import hike_gpx
from gpxfoto.engine.matching import (
    PhotoResult, Summary, corrected_times, match_photo, match_photos, placed_by_hand,
    shots_of, summarize, suspicion, with_nearest_tracks)
from gpxfoto.engine.photos import TZ_CAMERA, TZ_MANUAL, Photo
from gpxfoto.engine.track import Track, find_stops, load_track, locate, quick_span

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


def test_corrected_times_of_the_photos_to_match():
    photos = [photo((12, 0, 50)), photo((12, 0, 10), has_location=True),
              Photo("x.jpg", None, None, "no capture time in EXIF", False), photo((12, 0, 0))]
    assert corrected_times(photos, 10) == [T0 + 10, T0 + 60]
    assert corrected_times(photos, 10, overwrite=True) == [T0 + 10, T0 + 20, T0 + 60]
    assert corrected_times(photos, 1e12) == []


def test_a_photo_placed_by_hand_keeps_its_corrected_time():
    result = placed_by_hand(photo((12, 0, 50), has_location=True), -50, 49.5, 19.25)
    assert (result.lat, result.lon, result.ele, result.reason, result.files) == (
        49.5, 19.25, None, None, ())
    assert result.time == datetime(2024, 5, 1, 12, 0, 0, tzinfo=WARSAW)
    assert result.time_utc == datetime(2024, 5, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_a_photo_without_a_time_placed_by_hand_has_none():
    result = placed_by_hand(Photo("a.jpg", None, None, "no capture time in EXIF", False),
                            0, 49.5, 19.25)
    assert (result.time, result.time_utc, result.reason) == (None, None, None)


def test_a_time_zone_and_the_same_difference_as_a_correction_place_a_photo_alike(track):
    # What is written comes from the position, the elevation and the time
    # in UTC alone, so the file is the same either way
    taken = datetime(2024, 5, 1, 12, 0, 50)
    in_zone = Photo("a.jpg", taken.replace(tzinfo=WARSAW), TZ_MANUAL, None, False)
    wrong_zone = Photo("a.jpg", taken.replace(tzinfo=timezone.utc), TZ_MANUAL, None, False)
    one = match_photo(in_zone, [track], 0, 120)
    other = match_photo(wrong_zone, [track], -2 * 3600, 120)
    assert one.reason is None
    assert (one.lat, one.lon, one.ele, one.time_utc) == (
        other.lat, other.lon, other.ele, other.time_utc)


def test_shots_are_the_photos_with_a_time(track):
    photos = [photo((12, 0, 50)), Photo("x.jpg", None, None, "no capture time in EXIF", False),
              photo((12, 30, 0))]
    shots, indices = shots_of(match_photos(photos, [track], 10, 120))
    assert indices == [0, 2]
    assert [(shot.time, shot.clock) for shot in shots] == [
        (T0 + 60, T0 + 60 + 7200), (T0 + 1810, T0 + 1810 + 7200)]
    assert (shots[0].lat, shots[0].lon) == locate(track.points, track.times, T0 + 60, 120)[:2]
    assert (shots[1].lat, shots[1].lon) == (None, None)


def test_a_photo_no_track_covers_names_the_nearest_track(tmp_path):
    early = hike_gpx(str(tmp_path / "early.gpx"), [(T0 - 7200 + i, 50.0, 20.0, 200.0)
                                                 for i in range(0, 100, 10)])
    late = hike_gpx(str(tmp_path / "late.gpx"), [(T0 + 3600 + i, 50.0, 20.0, 200.0)
                                               for i in range(0, 100, 10)])
    broken = str(tmp_path / "broken.gpx")
    with open(broken, "w", encoding="utf-8") as f:
        f.write("<gpx><trk><trkseg><trkpt lat='50' lon='20'><time>2024-05-01T10:20:00Z</time>")
    tracks = [load_track([early], named=False)]
    spans = {path: quick_span(path) for path in (early, late, broken)}
    photos = [photo((12, 30, 0)), photo((10, 30, 0))]
    results = match_photos(photos, tracks, 0, 120)
    assert [result.covered for result in results] == [False, False]
    changed, loaded, failed = with_nearest_tracks(results, tracks, spans, stops=True)
    assert (changed[0].reason, changed[0].files) == (
        "30 min before the start of the nearest track", (late,))
    assert (changed[1].reason, changed[1].files) == (
        "28 min after the end of the nearest track", (early,))
    assert [t.files for t in loaded] == [(late,)]
    [(path, error)] = failed
    assert path == broken and isinstance(error, ValueError)
    # A file known to be unreadable is not read again
    assert with_nearest_tracks(results, tracks, spans, True, skipped=[broken])[2] == []


def test_jumps_are_looked_for_without_stops_and_on_several_tracks(track):
    far = Track(["far.gpx"], True, [(T0 + 110, 51.0, 20.0, 200.0), (T0 + 200, 51.0, 20.0, 200.0)])
    photos = [photo((12, 1, 30)), photo((12, 2, 0), path="b.jpg")]
    results = match_photos(photos, [track, far], 0, 120)
    assert [result.files for result in results] == [("track.gpx",), ("far.gpx",)]
    shots, _indices = shots_of(results)
    found = suspicion(results, [track, far], shots, 120)
    assert (found.shift, found.motion) == (None, None)
    [jump] = found.jumps
    assert (jump.first, jump.second, jump.clock_gap) == (0, 1, 30)
    one = match_photos(photos[:1], [track], 0, 120)
    assert suspicion(one, [track], shots_of(one)[0], 120, stops=False).jumps == []
