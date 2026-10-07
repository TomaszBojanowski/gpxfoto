"""The camera clock correction from a photo of a clock (gpxfoto.engine.clock)."""
import os
from datetime import date, datetime, time, timedelta, timezone

import pytest

from conftest import make_jpeg, needs_exiftool, set_tags
from gpxfoto.engine.clock import ClockError, ClockReading, correction_from, measure, parse_reading
from gpxfoto.engine.photos import TZ_CAMERA, TZ_MANUAL

INVALID = ("not a valid time: {} (use HH:MM:SS or HH:MM, optionally with a date and a UTC "
           "offset, for example 14:03:27, 2026-10-06T14:03:27 or 14:03:27+02:00)")


def zone(hours, minutes=0):
    return timezone(timedelta(hours=hours, minutes=minutes))


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "C")


# parse_reading

@pytest.mark.parametrize("text, expected", [
    ("14:03:27", ClockReading(time(14, 3, 27), True)),
    ("14:03", ClockReading(time(14, 3), False)),
    ("9:05", ClockReading(time(9, 5), False)),
    ("0:00:00", ClockReading(time(0, 0), True)),
    ("23:59:59", ClockReading(time(23, 59, 59), True)),
    ("  14:03:27  ", ClockReading(time(14, 3, 27), True)),
    ("14:03:27+02:00", ClockReading(time(14, 3, 27), True, None, zone(2))),
    ("13:03:27-05:00", ClockReading(time(13, 3, 27), True, None, zone(-5))),
    ("14:03:27 +05:45", ClockReading(time(14, 3, 27), True, None, zone(5, 45))),
    ("07:03:27Z", ClockReading(time(7, 3, 27), True, None, timezone.utc)),
    ("07:03:27z", ClockReading(time(7, 3, 27), True, None, timezone.utc)),
    ("07:03:27-00:00", ClockReading(time(7, 3, 27), True, None, timezone.utc)),
    ("2026-10-06T14:03:27+02:00", ClockReading(time(14, 3, 27), True, date(2026, 10, 6), zone(2))),
    ("2026-10-06t14:03", ClockReading(time(14, 3), False, date(2026, 10, 6))),
    ("2026-10-06 14:03:27", ClockReading(time(14, 3, 27), True, date(2026, 10, 6))),
])
def test_valid_readings(text, expected):
    assert parse_reading(text) == expected


@pytest.mark.parametrize("text", [
    "", "14", "14:3", "14:03:2", "24:00", "24:00:00", "14:60", "14:03:60", "114:03",
    "14:03:27.5", "2:03 PM", "14.03.27", "14:03:27+2", "14:03:27+15:00", "14:03:27 UTC",
    "2026-13-01T14:03", "2026-02-30T14:03", "26-10-06T14:03", "2026-10-06", "2026-10-06T",
    "１４:03", "14:03:27++02:00",
])
def test_invalid_readings(text):
    with pytest.raises(ValueError) as raised:
        parse_reading(text)
    assert str(raised.value) == INVALID.format(text)


@pytest.mark.parametrize("text, canonical", [
    ("9:05", "09:05"),
    ("14:03:27", "14:03:27"),
    ("07:03:27Z", "07:03:27+00:00"),
    ("2026-10-06 14:03:27-05:30", "2026-10-06T14:03:27-05:30"),
    ("0999-01-02T00:00", "0999-01-02T00:00"),
])
def test_text_is_read_back_unchanged(text, canonical):
    reading = parse_reading(text)
    assert reading.text() == canonical
    assert parse_reading(canonical) == reading


def test_text_with_another_offset_or_a_date_form():
    reading = parse_reading("2026-10-06T14:03:27+02:00")
    assert reading.text(utc_offset=zone(9)) == "2026-10-06T14:03:27+09:00"
    assert reading.text(date_form="YYYY-MM-DD") == "YYYY-MM-DDT14:03:27+02:00"
    assert parse_reading("14:03").text(utc_offset=zone(-3, -30)) == "14:03-03:30"


# correction_from

def camera(*fields, offset=2, microsecond=0):
    return datetime(*fields, microsecond=microsecond, tzinfo=zone(offset))


@pytest.mark.parametrize("camera_time, reading, seconds, clock_time", [
    # The same time zone: the clock 2 min 12 s ahead
    (camera(2026, 10, 6, 14, 1, 15), "14:03:27", 132, camera(2026, 10, 6, 14, 3, 27)),
    # Behind, by under 30 min
    (camera(2026, 10, 6, 14, 1, 15), "13:31:16", -1799, camera(2026, 10, 6, 13, 31, 16)),
    # Over midnight: the clock shows the next day
    (camera(2026, 10, 6, 23, 59, 40), "00:01:05", 85, camera(2026, 10, 7, 0, 1, 5)),
    (camera(2026, 10, 7, 0, 0, 40), "23:59:35", -65, camera(2026, 10, 6, 23, 59, 35)),
    # Across the date line, in Tonga
    (camera(2026, 10, 6, 23, 59, 58, offset=13), "00:00:03", 5,
     camera(2026, 10, 7, 0, 0, 3, offset=13)),
    # The camera did not switch to summer time; the watch did
    (camera(2026, 10, 6, 13, 1, 15, offset=1), "14:03:27+02:00", 132,
     camera(2026, 10, 6, 14, 3, 27)),
    # The camera's wall clock is an hour behind its own offset
    (camera(2026, 10, 6, 13, 1, 15), "14:03:27+02:00", 3732, camera(2026, 10, 6, 14, 3, 27)),
    # A Polish camera in Lisbon
    (camera(2026, 10, 6, 14, 1, 15), "13:03:27+01:00", 132,
     camera(2026, 10, 6, 13, 3, 27, offset=1)),
    # Sub-seconds of the clock photo count
    (camera(2026, 10, 6, 14, 1, 15, microsecond=480000), "14:03:27", 131.52,
     camera(2026, 10, 6, 14, 3, 27)),
    # Without seconds, the middle of the minute
    (camera(2026, 10, 6, 14, 1, 15), "14:03", 135, camera(2026, 10, 6, 14, 3, 30)),
    # With a date and an offset, any size
    (camera(2019, 1, 1, 0, 0, 0), "2026-10-06T14:03:27+02:00", 244_994_607,
     camera(2026, 10, 6, 14, 3, 27)),
    (camera(2026, 10, 6, 14, 1, 15), "2026-10-06T14:03:27", 132, camera(2026, 10, 6, 14, 3, 27)),
])
def test_correction(camera_time, reading, seconds, clock_time):
    correction = correction_from(camera_time, parse_reading(reading))
    assert correction.seconds == seconds
    assert correction.clock_time == clock_time
    assert correction.clock_time.utcoffset() == clock_time.utcoffset()
    assert correction.camera_time == camera_time
    assert correction.uncertainty == (0 if ":" in reading[3:] else 30)


@pytest.mark.parametrize("difference, accepted", [
    (timedelta(minutes=29, seconds=59), True),
    (timedelta(minutes=30), False),
    (-timedelta(minutes=29, seconds=59), True),
    (-timedelta(minutes=30), False),
])
def test_offset_required_from_30_min(difference, accepted):
    camera_time = camera(2026, 10, 6, 14, 0, 0)
    reading = parse_reading(f"{camera_time + difference:%H:%M:%S}")
    if accepted:
        assert correction_from(camera_time, reading).seconds == difference.total_seconds()
    else:
        with pytest.raises(ClockError):
            correction_from(camera_time, reading)


def test_minutes_only_reading_at_the_threshold_is_refused():
    # 14:30 stands for 14:30:30, which is 30 min 30 s after the camera time
    with pytest.raises(ClockError):
        correction_from(camera(2026, 10, 6, 14, 0, 0), parse_reading("14:30"))


@pytest.mark.parametrize("camera_time, reading, message", [
    (camera(2026, 10, 6, 13, 1, 15, offset=1), "14:03:27",
     "The time on the clock is 1 h 2 min 12 s away from the capture time of the clock photo, so "
     "the clock may have shown a different time zone than the camera. Add the UTC offset of the "
     "time on the clock, for example “14:03:27+02:00” or “14:03:27+01:00”."),
    (camera(2026, 10, 6, 14, 1, 15), "13:03:27",
     "The time on the clock is 57 min 48 s away from the capture time of the clock photo, so the "
     "clock may have shown a different time zone than the camera. Add the UTC offset of the time "
     "on the clock, for example “13:03:27+01:00” or “13:03:27+02:00”."),
    # A date without an offset still needs the offset
    (camera(2026, 10, 6, 7, 1, 15), "2026-10-06T14:03:27",
     "The time on the clock is 7 h 2 min 12 s away from the capture time of the clock photo, so "
     "the clock may have shown a different time zone than the camera. Add the UTC offset of the "
     "time on the clock, for example “2026-10-06T14:03:27+09:00” or “2026-10-06T14:03:27+02:00”."),
    # A suggestion beyond +14:00 goes round to the other side
    (camera(2026, 10, 6, 12, 0, 0, offset=14), "13:00:00",
     "The time on the clock is 1 h away from the capture time of the clock photo, so the clock "
     "may have shown a different time zone than the camera. Add the UTC offset of the time on the "
     "clock, for example “13:00:00-09:00” or “13:00:00+14:00”."),
    # Over 14 h only the camera's own offset is given as an example
    (camera(2019, 1, 1, 0, 0, 0), "2026-10-06T14:03:27",
     "The time on the clock is 68054 h 3 min 27 s away from the capture time of the clock photo. "
     "Add the UTC offset of the time on the clock as well, for example "
     "“2026-10-06T14:03:27+02:00”."),
    # With an offset but no date, over 2 h: a form of the date, not a guess
    (camera(2026, 10, 6, 14, 1, 15), "14:03:27+09:00",
     "The time on the clock is 6 h 57 min 48 s away from the capture time of the clock photo. If "
     "the camera’s clock really is that far off, give the date shown on the clock as well, in the "
     "form “YYYY-MM-DDT14:03:27+09:00”."),
    (camera(2019, 1, 1, 0, 0, 0), "14:03:27+02:00",
     "The time on the clock is 9 h 56 min 33 s away from the capture time of the clock photo. If "
     "the camera’s clock really is that far off, give the date shown on the clock as well, in the "
     "form “YYYY-MM-DDT14:03:27+02:00”."),
    (camera(2026, 10, 6, 14, 1, 15), "9999-12-31T23:59:59-14:00",
     "The time on the clock is out of range: 9999-12-31T23:59:59-14:00"),
])
def test_correction_that_could_be_wrong_is_refused(camera_time, reading, message):
    with pytest.raises(ClockError) as raised:
        correction_from(camera_time, parse_reading(reading))
    assert str(raised.value) == message


# measure

@pytest.fixture
def clock_photo(tmp_path):
    def create(*tags, name="watch.jpg"):
        path = tmp_path / name
        path.write_bytes(make_jpeg())
        if tags:
            set_tags(path, *tags)
        return str(path)
    return create


@needs_exiftool
def test_measure_reads_the_clock_photo(clock_photo):
    path = clock_photo("-DateTimeOriginal=2026:10:06 14:01:15", "-OffsetTimeOriginal=+02:00",
                       "-SubSecTimeOriginal=48")
    before = open(path, "rb").read()
    correction = measure(path, parse_reading("14:03:27"), None)
    assert (correction.seconds, correction.photo, correction.tz_source) == (131.52, path, TZ_CAMERA)
    assert open(path, "rb").read() == before


@needs_exiftool
def test_measure_uses_the_time_zone_given_for_all_photos(clock_photo):
    path = clock_photo("-DateTimeOriginal=2026:10:06 13:01:15", "-OffsetTimeOriginal=+01:00")
    reading = parse_reading("14:03:27+03:00")
    assert measure(path, reading, None).seconds == -3468
    correction = measure(path, reading, zone(2))
    assert (correction.seconds, correction.tz_source) == (132, TZ_MANUAL)


@needs_exiftool
def test_measure_refuses_a_photo_without_a_capture_time(clock_photo):
    path = clock_photo()
    with pytest.raises(ClockError) as raised:
        measure(path, parse_reading("14:03:27"), None)
    assert str(raised.value) == f"Cannot use the clock photo {path}: no capture time in EXIF"


def test_measure_refuses_what_is_not_a_file(tmp_path):
    missing = str(tmp_path / "missing.jpg")
    os.symlink("nowhere.jpg", tmp_path / "broken.jpg")
    for path, reason in [(missing, "no such file or directory"),
                         (str(tmp_path / "broken.jpg"), "not a regular file"),
                         (str(tmp_path), "not a regular file")]:
        with pytest.raises(ClockError) as raised:
            measure(path, parse_reading("14:03:27"), None)
        assert str(raised.value) == f"Cannot use the clock photo {path}: {reason}"
