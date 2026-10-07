"""Stops: stretches of a track without movement (find_stops, stop_at, place)."""
import math
import random
from datetime import datetime, timezone

import pytest

from conftest import write_gpx
from gpxfoto.engine.track import (
    PIN_HEIGHT, PIN_RADIUS, STILL_WINDOW, STOP_HEIGHT, STOP_RADIUS, Stop, find_stops, load_gpx,
    locate, place, stop_at)

T0 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()
LAT, LON = 49.2, 20.0
M_PER_DEGREE = math.pi * 6371000.0 / 180


def position(east, north, lat=LAT, lon=LON):
    """Latitude and longitude of a place given in metres from (lat, lon)."""
    lon = lon + east / (M_PER_DEGREE * math.cos(math.radians(lat)))
    return lat + north / M_PER_DEGREE, (lon + 180) % 360 - 180


def metres(stop, east, north):
    """Distance in metres from a stop to a place given in metres from (LAT, LON)."""
    lat, lon = position(east, north)
    change = (stop.lon - lon + 180) % 360 - 180
    return math.hypot(change * M_PER_DEGREE * math.cos(math.radians(lat)),
                      (stop.lat - lat) * M_PER_DEGREE)


class Hike:
    """A track recorded once a second; places in metres east and north."""

    def __init__(self, ele=1000.0, seed=1, lat=LAT, lon=LON):
        self.points = []
        self.t, self.x, self.y, self.ele = T0, 0.0, 0.0, ele
        self.lat, self.lon = lat, lon
        self.random = random.Random(seed)

    def add(self, dx=0.0, dy=0.0, dz=0.0):
        lat, lon = position(self.x + dx, self.y + dy, self.lat, self.lon)
        ele = None if self.ele is None else self.ele + dz
        self.points.append((self.t, lat, lon, ele))
        self.last = (self.t, self.x, self.y, self.ele)

    def walk(self, seconds, east=0.0, north=0.0, up=0.0, jitter=0.0):
        for _ in range(seconds):
            self.add(*self.noise(jitter))
            self.t += 1
            self.x += east
            self.y += north
            if self.ele is not None:
                self.ele += up
        return self

    def stand(self, seconds, jitter=0.0):
        return self.walk(seconds, jitter=jitter)

    def pause(self, seconds, east=0.0, north=0.0, up=0.0):
        """A break in recording, e.g. auto-pause: the next point comes seconds
        after the last one, moved by east, north and up from it."""
        t, x, y, ele = self.last
        self.t, self.x, self.y = t + seconds, x + east, y + north
        if ele is not None:
            self.ele = ele + up
        return self

    def noise(self, jitter):
        if not jitter:
            return 0.0, 0.0, 0.0
        r = self.random
        return r.gauss(0, jitter), r.gauss(0, jitter), r.gauss(0, 0.3)


def stopped(hike):
    """(start, end) of the stops, relative to the start of the hike."""
    return [(s.start - T0, s.end - T0) for s in find_stops(hike.points)]


# find_stops: what is a stop and what is not

def test_too_short_tracks_have_no_stops():
    assert find_stops([]) == []
    assert find_stops([(T0, LAT, LON, None)]) == []


def test_walking_has_no_stops():
    assert find_stops(Hike().walk(900, east=1.2, jitter=3).points) == []


def test_standing_between_walks_is_one_stop():
    hike = Hike().walk(180, east=1.2).stand(180).walk(180, east=1.2)

    stops = find_stops(hike.points)

    assert len(stops) == 1
    stop = stops[0]
    assert metres(stop, 216, 0) < 0.01
    assert stop.elevation == 1000.0
    # From where the track comes within STOP_RADIUS to where it leaves it
    assert stop.start == T0 + 180 - math.floor(STOP_RADIUS / 1.2)
    assert stop.end == T0 + 360 + math.floor(STOP_RADIUS / 1.2)
    assert (stop.first, stop.last) == (stop.start - T0, stop.end - T0)


@pytest.mark.parametrize("seconds, count", [(20, 0), (40, 0), (60, 1), (120, 1)])
def test_a_stop_takes_about_a_minute(seconds, count):
    hike = Hike().walk(120, east=1.2).stand(seconds).walk(120, east=1.2)
    assert len(find_stops(hike.points)) == count


@pytest.mark.parametrize("seed", range(5))
def test_jitter_while_standing_does_not_split_a_stop(seed):
    hike = Hike(seed=seed).walk(120, east=1.2).stand(600, jitter=4)
    # A jump of 18 m for 5 s, and a slow wander 8 m away and back
    hike.x += 18
    hike.stand(5, jitter=4)
    hike.x -= 18
    for step in [0.2] * 40 + [-0.2] * 40:
        hike.x += step
        hike.stand(1, jitter=4)
    hike.stand(600, jitter=4).walk(120, east=1.2)

    stops = find_stops(hike.points)

    # One stop; with this much jitter its edges are known to within STILL_WINDOW
    assert len(stops) == 1
    assert stops[0].start <= T0 + 120 + STILL_WINDOW
    assert stops[0].end >= T0 + 120 + 1285 - STILL_WINDOW
    assert metres(stops[0], 144, 0) < 2


@pytest.mark.parametrize("east, up", [
    (0.0, 0.05),     # the watch freezes the position while climbing slowly
    (0.0, -0.05),    # or descending
    (0.1, 0.045),    # 160 m/h up a very steep slope
    (0.15, 0.06),
    (0.2, 0.05),
])
def test_slow_steep_climbing_is_not_a_stop(east, up):
    hike = Hike().walk(120, east=1.2).walk(900, east=east, up=up, jitter=2).walk(120, east=1.2)
    assert find_stops(hike.points) == []


@pytest.mark.parametrize("seed", [1, 2, 4])
def test_steady_climb_just_above_the_limit_is_not_a_stop(seed):
    # Noise hides a climb this slow from the short windows; the whole stretch shows it
    hike = Hike(seed=seed).walk(60, east=1.2).walk(600, up=0.032, jitter=1.0).walk(60, east=1.2)
    assert find_stops(hike.points) == []


def test_slow_flat_walking_is_not_a_stop():
    hike = Hike().walk(900, east=0.25, jitter=2)
    assert find_stops(hike.points) == []


@pytest.mark.parametrize("up", [0.05, -0.05, 0.1])
def test_stop_followed_by_slow_climbing_with_a_frozen_position(up):
    hike = Hike().walk(120, east=1.2).stand(300).walk(600, up=up).walk(120, east=1.2)

    [stop] = find_stops(hike.points)

    # The stop ends where the height leaves STOP_HEIGHT, not after the climb
    assert stop.start == T0 + 112
    assert T0 + 420 <= stop.end <= T0 + 420 + STOP_HEIGHT / abs(up) + 1


def test_noisy_gps_elevations_are_not_used():
    # Without a barometer, elevations jump by metres from point to point
    hike = Hike().walk(120, east=1.2).stand(300).walk(120, east=1.2)
    rng = random.Random(5)
    hike.points = [(t, lat, lon, ele + rng.gauss(0, 3)) for t, lat, lon, ele in hike.points]
    [stop] = find_stops(hike.points)
    assert stop.start == T0 + 112 and stop.end == T0 + 428


def test_without_elevations_only_horizontal_movement_counts():
    # Climbing with a frozen position cannot be told from standing
    hike = Hike(ele=None).walk(120, east=1.2).walk(300, up=0.05).walk(120, east=1.2)
    assert len(find_stops(hike.points)) == 1


def test_elevation_is_the_median_of_the_still_points():
    hike = Hike().walk(120, east=1.2)
    # The mean would be 1000.08 m
    for dz in [0.0] * 50 + [0.6] * 30 + [-0.2] * 40:
        hike.add(dz=dz)
        hike.t += 1
    hike.walk(120, east=1.2)
    assert [s.elevation for s in find_stops(hike.points)] == [1000.0]


def test_stop_without_elevations():
    hike = Hike(ele=None).walk(120, east=1.2).stand(120).walk(120, east=1.2)
    assert [s.elevation for s in find_stops(hike.points)] == [None]


def test_position_is_the_median_not_the_mean():
    hike = Hike().walk(120, east=1.2).stand(90)
    hike.y += 8            # a quarter of the time 8 m further north
    hike.stand(30)
    hike.y -= 8
    hike.stand(10).walk(120, east=1.2)
    [stop] = find_stops(hike.points)
    assert metres(stop, 144, 0) < 0.01


# Breaks in recording

def test_auto_pause_is_a_stop():
    # The watch stops recording on stopping and resumes 3 m further on
    hike = Hike().walk(120, east=1.2).pause(600, east=3).walk(120, east=1.2)

    [stop] = find_stops(hike.points)

    assert stop.start <= T0 + 119 and stop.end >= T0 + 719
    assert metres(stop, 142.8 + 1.5, 0) < 0.01


@pytest.mark.parametrize("seconds, east, up, count", [
    (600, 9.9, 0, 1),
    (600, 10.1, 0, 0),    # moved more than STOP_RADIUS
    (40, 7.9, 0, 1),
    (40, 8.1, 0, 0),      # faster than STILL_SPEED
    (300, 2, 8.9, 1),
    (300, 2, 9.1, 0),     # climbed faster than STILL_CLIMB
    (300, 2, -9.1, 0),
])
def test_break_is_a_stop_when_its_ends_are_close(seconds, east, up, count):
    hike = Hike().walk(120, east=1.2).pause(seconds, east=east, up=up).walk(120, east=1.2)
    assert len(find_stops(hike.points)) == count


def test_break_between_two_files_is_a_stop(tmp_path):
    # Two activities with lunch in between, recorded as two GPX files
    def write(name, hike):
        return write_gpx(tmp_path / name, [
            (datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), lat, lon, ele)
            for t, lat, lon, ele in hike.points])
    morning = Hike().walk(300, east=1.2)
    afternoon = Hike()
    afternoon.t, afternoon.x = T0 + 3900, 360.0
    afternoon.walk(300, east=1.2)

    points = load_gpx([write("morning.gpx", morning), write("afternoon.gpx", afternoon)])

    [stop] = find_stops(points)
    assert stop.start <= T0 + 299 and stop.end >= T0 + 3900


def test_stop_over_a_night():
    # Two days of walking with a night in between, without recording
    hike = Hike().walk(3600, east=1.2).pause(14 * 3600, east=2).walk(3600, east=1.2)

    [stop] = find_stops(hike.points)

    assert T0 + 3599 - STILL_WINDOW <= stop.start <= T0 + 3599
    assert T0 + 3599 + 14 * 3600 <= stop.end <= T0 + 3599 + 14 * 3600 + STILL_WINDOW


def test_points_recorded_irregularly():
    # Smart recording: a point every few seconds, rarer when standing
    hike = Hike().walk(120, east=1.2)
    for gap in [5, 12, 25, 8, 20, 25, 3, 15, 25, 10]:
        hike.add()
        hike.t += gap
    hike.walk(120, east=1.2)
    assert len(find_stops(hike.points)) == 1


# The ends of the track

def test_stops_at_the_start_and_the_end_of_the_track():
    hike = Hike().stand(120).walk(300, east=1.2).stand(120)

    first, last = find_stops(hike.points)

    assert (first.first, first.start) == (0, T0)
    assert (last.last, last.end) == (len(hike.points) - 1, T0 + 539)


def test_two_points_at_the_same_place():
    points = [(T0, LAT, LON, 1000.0), (T0 + 600, LAT, LON, 1000.0)]
    assert find_stops(points) == [Stop(T0, T0 + 600, LAT, LON, 1000.0, 0, 1)]


# Several stops

def test_two_places_close_together_are_two_stops():
    hike = Hike().walk(120, east=1.2).stand(120).walk(20, north=1.25).stand(120)
    hike.walk(120, east=1.2)

    first, second = find_stops(hike.points)

    assert metres(first, 144, 0) < 0.01
    assert metres(second, 144, 25) < 0.01
    assert first.last < second.first


def test_stepping_away_and_back_is_one_stop():
    hike = Hike().walk(120, east=1.2).stand(120).walk(10, north=1).walk(10, north=-1).stand(120)
    hike.walk(120, east=1.2)
    assert len(find_stops(hike.points)) == 1


def test_stops_are_in_order_and_do_not_overlap():
    hike = Hike(seed=7)
    for i in range(10):
        hike.walk(60 + 30 * i, east=1.1, up=0.1, jitter=3).stand(30 + 20 * i, jitter=3)
    stops = find_stops(hike.points)
    assert len(stops) >= 7
    for a, b in zip(stops, stops[1:]):
        assert a.start < a.end < b.start < b.end
        assert a.first < a.last < b.first < b.last


# The 180th meridian

def test_stop_on_the_180th_meridian():
    hike = Hike(lat=-16.5, lon=179.99999).walk(120, east=1.2).stand(300, jitter=3)
    hike.walk(120, east=1.2)

    [stop] = find_stops(hike.points)

    lat, lon = position(144, 0, -16.5, 179.99999)
    assert -180 <= stop.lon < 180
    assert abs((stop.lon - lon + 180) % 360 - 180) < 2e-5
    assert stop_at([stop], stop.start + 60, lat, lon, None) is stop


# stop_at

STOP = Stop(T0 + 100, T0 + 200, LAT, LON, 1000.0, 100, 200)
LATER = Stop(T0 + 500, T0 + 600, LAT, LON, 1000.0, 500, 600)


@pytest.mark.parametrize("t, expected", [
    (T0 + 99.9, None),
    (T0 + 100, STOP),
    (T0 + 150, STOP),
    (T0 + 200, STOP),
    (T0 + 200.1, None),
    (T0 + 400, None),
    (T0 + 500, LATER),
    (T0 + 700, None),
])
def test_stop_at_time(t, expected):
    assert stop_at([STOP, LATER], t, LAT, LON, 1000.0) == expected


@pytest.mark.parametrize("elevation, pinned", [
    (1000.0 + PIN_HEIGHT - 0.1, True),
    (1000.0 - PIN_HEIGHT - 0.1, False),
    (None, True),
])
def test_stop_at_only_near_the_stop_in_height(elevation, pinned):
    assert (stop_at([STOP], T0 + 150, LAT, LON, elevation) is STOP) == pinned


def test_stop_at_without_stops():
    assert stop_at([], T0, LAT, LON, None) is None


@pytest.mark.parametrize("east, north, pinned", [
    (PIN_RADIUS - 0.1, 0, True),
    (PIN_RADIUS + 0.1, 0, False),
    (0, -(PIN_RADIUS - 0.1), True),
    (0, -(PIN_RADIUS + 0.1), False),
])
def test_stop_at_only_near_the_stop(east, north, pinned):
    lat, lon = position(east, north)
    assert (stop_at([STOP], T0 + 150, lat, lon, 1000.0) is STOP) == pinned


def test_photo_during_a_stop_on_a_real_track_shape():
    # locate gives the jittery point; the stop gives the median
    hike = Hike(seed=3).walk(120, east=1.2).stand(300, jitter=4).walk(120, east=1.2)
    points = hike.points
    times = [p[0] for p in points]
    [stop] = find_stops(points)
    t = T0 + 250
    lat, lon, _ele, _gap = locate(points, times, t, 120)
    assert stop_at([stop], t, lat, lon, _ele) is stop
    assert metres(stop, 144, 0) < 1.5


# Added by the review

@pytest.mark.parametrize("seed", [1, 2, 4])
def test_steady_climb_just_above_the_limit_is_not_a_stop(seed):
    # 115 m/h: the 30 s windows sometimes miss it in the jitter, the whole stretch does not
    hike = Hike(seed=seed).walk(60, east=1.2).walk(600, up=0.032, jitter=1.0).walk(60, east=1.2)
    assert find_stops(hike.points) == []


def test_place_pins_a_photo_to_the_stop():
    from gpxfoto.engine.track import place
    hike = Hike().walk(180, east=1.2).stand(180, jitter=2).walk(180, east=1.2)
    points = hike.points
    times = [p[0] for p in points]
    stops = find_stops(points)
    lat, lon, ele, gap, stop = place(points, times, stops, T0 + 270.5, 120)
    assert stop == stops[0] and (lat, lon, ele) == (stop.lat, stop.lon, stop.elevation)
    assert gap == 0.5
    walking = place(points, times, stops, T0 + 60.5, 120)
    assert walking[:4] == locate(points, times, T0 + 60.5, 120) and walking[4] is None
    assert place(points, times, stops, T0 - 600, 120) == locate(points, times, T0 - 600, 120)
    assert place(points, times, [], T0 + 270.5, 120)[:4] == locate(points, times, T0 + 270.5, 120)


def test_stop_over_a_night():
    hike = Hike().walk(3600, east=1.2, jitter=2).pause(14 * 3600).walk(3600, east=1.2, jitter=2)
    stops = find_stops(hike.points)
    assert len(stops) == 1
    assert 3599 - STILL_WINDOW <= stops[0].start - T0 <= 3599
    assert 3599 + 14 * 3600 <= stops[0].end - T0 <= 3599 + 14 * 3600 + STILL_WINDOW


# place

def test_place_pins_a_photo_taken_during_a_stop():
    hike = Hike(seed=3).walk(120, east=1.2).stand(300, jitter=4).walk(120, east=1.2)
    points = hike.points
    times = [p[0] for p in points]
    stops = find_stops(points)
    [stop] = stops
    during = T0 + 250.5
    assert place(points, times, stops, during, 120) == (
        stop.lat, stop.lon, stop.elevation, 0.5, stop)
    walking = T0 + 30.5
    assert place(points, times, stops, walking, 120) == (
        *locate(points, times, walking, 120), None)
    before = T0 - 600
    assert place(points, times, stops, before, 120) == locate(points, times, before, 120)
    assert place(points, times, [], during, 120) == (*locate(points, times, during, 120), None)


def test_place_keeps_the_track_elevation_when_the_stop_has_none():
    stop = Stop(T0 + 100, T0 + 200, LAT, LON, None, 1, 2)
    points = [(T0, LAT, LON, 990.0), (T0 + 100, LAT, LON, 1000.0), (T0 + 200, LAT, LON, 1010.0)]
    times = [p[0] for p in points]
    assert place(points, times, [stop], T0 + 150, 120) == (LAT, LON, 1005.0, 50, stop)
