"""Signs of a suspicious match (gpxfoto.engine.checks).

The thresholds were chosen on a real 5.5-hour mountain hike recorded
once a second (see test_private.py); these tests spell each one out on
small synthetic tracks.
"""
import pytest

from gpxfoto.engine import checks
from gpxfoto.engine.checks import (
    SHIFT_MIN_PHOTOS, SHIFT_MIN_SHARE, ShiftHint, Shot, clearly_more_stops, shift_stands_out,
    whole_hour_shift)
from gpxfoto.engine.track import find_stops, place
from test_stops import LAT, LON, T0, Hike, position

ZONE = 2 * 3600          # the photos were taken at UTC+02:00
MAX_GAP = 120.0
# Two-minute stops; no two of them start within 4 minutes of a whole
# hour or half an hour apart, so no tried shift moves one onto another
STOP_MINUTES = (20, 35, 54, 76, 118, 161)


def stops_hike():
    """Walking east at 1.2 m/s for 200 min with a stop at each of STOP_MINUTES."""
    hike = Hike(seed=1)
    now = 0
    for minute in STOP_MINUTES:
        hike.walk(minute * 60 - now, east=1.2)
        hike.stand(120, jitter=1.0)
        now = minute * 60 + 120
    hike.walk(200 * 60 - now, east=1.2)
    return hike.points


def shots(points, stops, photo_times, zone=ZONE):
    """The photos as the checks see them, placed as the program places them."""
    times = [p[0] for p in points]
    result = []
    for t in photo_times:
        found = place(points, times, stops, t, MAX_GAP)
        lat, lon = (found[0], found[1]) if found[0] is not None else (None, None)
        result.append(Shot(t, t + zone, lat, lon))
    return result


def at_stops(error=0.0, per_stop=(40, 80), minutes=STOP_MINUTES):
    """Photos taken during the stops by a camera whose clock is error s ahead."""
    return [T0 + m * 60 + s + error for m in minutes for s in per_stop]


@pytest.fixture(scope="module")
def track():
    points = stops_hike()
    stops = find_stops(points)
    assert len(stops) == len(STOP_MINUTES)
    return points, [p[0] for p in points], stops


def shift_for(track, photo_times):
    points, times, stops = track
    return whole_hour_shift(points, times, stops, shots(points, stops, photo_times), MAX_GAP)


# --- whole-hour shift -------------------------------------------------

def test_a_clock_an_hour_behind_gets_a_shift_of_one_hour(track):
    # The camera shows 11:20 when the watch shows 12:20
    assert shift_for(track, at_stops(-3600)) == ShiftHint(
        shift=3600, pinned=12, matched=12, stops=6, pinned_now=0)


def test_a_right_clock_gets_no_shift(track):
    assert shift_for(track, at_stops(0)) is None


@pytest.mark.parametrize("shift", [1800, -1800, 3600, -3600, 7200, -7200, 5 * 3600, -12 * 3600])
def test_the_shifts_that_are_tried(track, shift):
    assert shift_for(track, at_stops(-shift)).shift == shift


@pytest.mark.parametrize("error", [90 * 60, -45 * 60, 13 * 3600, 5 * 60])
def test_other_clock_errors_get_no_shift(track, error):
    # Only half an hour and whole hours up to 12 h are tried
    assert shift_for(track, at_stops(error)) is None


def test_shifts_are_tried_in_this_order():
    assert checks.SHIFTS[:6] == (1800, -1800, 3600, -3600, 7200, -7200)
    assert checks.SHIFTS[-2:] == (12 * 3600, -12 * 3600)
    assert len(checks.SHIFTS) == 26


@pytest.mark.parametrize("reached, reached_now, expected", [
    (4, 0, True),
    (4, 1, True),
    (3, 0, False),       # fewer than SHIFT_MIN_STOPS = 4 different stops
    (5, 2, True),
    (5, 3, False),       # only 2 more than now; SHIFT_MORE_STOPS = 3
    (9, 6, True),
])
def test_a_shift_must_reach_clearly_more_stops(reached, reached_now, expected):
    assert clearly_more_stops(reached, reached_now) is expected


@pytest.mark.parametrize("reached, nearby, pinned, matched, expected", [
    (6, 3.0, 4, 10, True),
    (6, 3.01, 4, 10, False),     # not 3 stops above the shifts 10-30 min away
    (6, 0.0, 4, 9, False),       # fewer than SHIFT_MIN_PHOTOS = 10 matched photos
    (6, 0.0, 3, 10, False),      # 30% of the photos at stops, below SHIFT_MIN_SHARE = 35%
    (6, 0.0, 7, 20, True),       # exactly 35%
    (6, 0.0, 7, 21, False),      # 33%
])
def test_a_shift_must_stand_out(reached, nearby, pinned, matched, expected):
    assert shift_stands_out(reached, nearby, pinned, matched) is expected
    assert (SHIFT_MIN_PHOTOS, SHIFT_MIN_SHARE) == (10, 0.35)


def test_three_stops_are_never_enough_and_four_not_on_this_track(track):
    # 12 photos at stops 20, 76 and 161 min: no stop 10 to 30 min away
    # from the shift, all photos at stops, but only 3 different stops
    three = at_stops(-3600, per_stop=(30, 45, 60, 75), minutes=(20, 76, 161))
    assert shift_for(track, three) is None
    # 4 stops reach SHIFT_MIN_STOPS, but shifts that are not whole or half
    # hours put these photos at 0.1 stop on average by chance, with a
    # spread below 1 stop: 4 stops stand only 3.9 spreads out, below SHIFT_Z
    four = at_stops(-3600, per_stop=(30, 50, 70), minutes=(20, 76, 118, 161))
    assert shift_for(track, four) is None
    five = at_stops(-3600, per_stop=(30, 50, 70), minutes=(20, 35, 76, 118, 161))
    assert shift_for(track, five) == ShiftHint(3600, 15, 15, 5, 0)


def test_ten_matched_photos_are_needed(track):
    photos = at_stops(-3600)
    assert shift_for(track, photos[:10]).shift == 3600
    assert shift_for(track, photos[:9]) is None


def test_photos_taken_mostly_while_walking_get_no_shift(track):
    # One photo at each of the 6 stops and the rest while walking, an
    # hour too early: 6 of 17 photos (35%) at stops is enough, 6 of 18 not
    walking = [T0 + minute * 60 for minute in (5, 10, 30, 45, 64, 66, 86, 90, 95, 105, 130, 140)]
    photos = at_stops(-3600, per_stop=(60,))
    assert shift_for(track, photos + [t - 3600 for t in walking[:11]]).shift == 3600
    assert shift_for(track, photos + [t - 3600 for t in walking[:12]]) is None


def test_no_shift_without_stops(track):
    points, times, _ = track
    assert whole_hour_shift(points, times, [], shots(points, [], at_stops(-3600)), MAX_GAP) is None


def test_a_night_at_one_place_does_not_suggest_a_shift():
    # Walk 4 h, sleep 14 h without recording, walk 4 h; photos every 7 min
    # while walking. Shifts of 4 to 12 h put many of them into the night,
    # which is one stop, not evidence from several places.
    hike = Hike(seed=2).walk(4 * 3600, east=1.0).pause(14 * 3600).walk(4 * 3600, east=-1.0)
    points = hike.points
    stops = find_stops(points)
    assert [round((s.end - s.start) / 3600) for s in stops] == [14]
    photo_times = [T0 + 600 + 420 * k for k in range(30)]
    photo_times += [T0 + 18 * 3600 + 600 + 420 * k for k in range(30)]
    found = shots(points, stops, photo_times)
    times = [p[0] for p in points]
    assert whole_hour_shift(points, times, stops, found, MAX_GAP) is None


def test_long_stops_do_not_make_a_shift_stand_out():
    # Five 40-min stops between 40-min walks; photos while walking. An hour
    # later, many photos fall into stops, but so they do 10 to 30 min later.
    hike = Hike(seed=3)
    for _ in range(5):
        hike.walk(2400, east=1.2).stand(2400, jitter=1.0)
    hike.walk(2400, east=1.2)
    points = hike.points
    stops = find_stops(points)
    assert len(stops) == 5
    photo_times = [T0 + 80 * 60 * k + m * 60 for k in range(6) for m in (5, 15, 25, 35)]
    times = [p[0] for p in points]
    found = shots(points, stops, photo_times)
    assert whole_hour_shift(points, times, stops, found, MAX_GAP) is None


def test_the_shift_takes_the_corrected_times(track):
    # The checks work on the times after the clock correction: with the
    # right correction already applied, nothing more is proposed
    assert shift_for(track, [t + 3600 for t in at_stops(-3600)]) is None
