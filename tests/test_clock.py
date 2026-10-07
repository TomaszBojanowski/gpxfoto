"""The camera clock correction from a photo of a clock (gpxfoto.engine.clock)."""
from datetime import date, time, timedelta, timezone

import pytest

from gpxfoto.engine.clock import ClockReading, parse_reading

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
