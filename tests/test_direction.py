"""Direction of travel (gpxfoto.engine.track.travel_direction)."""
import math
import random

import pytest

from gpxfoto.engine.track import (
    TRAVEL_DISTANCE, TRAVEL_MAX_WINDING, TRAVEL_WINDOW, find_stops, locate, travel_direction)

M_PER_DEGREE = math.pi * 6371000.0 / 180
T0 = 1_700_000_000.0

EDGE = "too close to the start or end of the track"
STILL = "the track stays within 20 m of this place for 60 s before or after the photo"
NO_POINTS = "no track points within 60 s before or after the photo"
WINDING = "the track winds too much here"
FILES = "the track points around this place come from different files"
BREAK = "the track has a break in recording here"


def walk(legs, start=(50.0, 20.0), noise=None):
    """One point a second; legs are (seconds, speed in m/s, bearing in degrees).

    noise is (metres, seconds, seed): GPS error of about that size that
    changes over about that many seconds, as in a phone's track.
    """
    lat, lon = start
    t = T0
    points = [(t, lat, lon, None)]
    for seconds, speed, bearing in legs:
        for _ in range(int(seconds)):
            lat += speed * math.cos(math.radians(bearing)) / M_PER_DEGREE
            lon += (speed * math.sin(math.radians(bearing))
                    / (M_PER_DEGREE * math.cos(math.radians(lat))))
            lon = (lon + 180) % 360 - 180
            t += 1
            points.append((t, lat, lon, None))
    if noise:
        size, period, seed = noise
        r = random.Random(seed)
        keep = math.exp(-1 / period)
        new = math.sqrt(1 - keep * keep)
        east = north = 0.0
        noisy = []
        for t, lat, lon, ele in points:
            east = keep * east + new * size * r.gauss(0, 1)
            north = keep * north + new * size * r.gauss(0, 1)
            noisy.append((t, lat + north / M_PER_DEGREE,
                          lon + east / (M_PER_DEGREE * math.cos(math.radians(lat))), ele))
        points = noisy
    return points


def direction_at(points, t):
    """The direction of travel of a photo taken at time t, placed by locate()."""
    times = [p[0] for p in points]
    lat, lon = locate(points, times, t, 120)[:2]
    return travel_direction(points, times, t, lat, lon)


def test_the_values():
    assert (TRAVEL_DISTANCE, TRAVEL_WINDOW, TRAVEL_MAX_WINDING) == (20.0, 60.0, 1.2)


@pytest.mark.parametrize("bearing, expected", [
    (0, 0), (45, 45), (90, 90), (123.4, 123), (180, 180), (270, 270),
    (359.6, 0),          # whole degrees from 0 to 359
    (0.4, 0),
])
def test_walking_straight(bearing, expected):
    assert direction_at(walk([(300, 1.0, bearing)]), T0 + 150.5) == (expected, None)


@pytest.mark.parametrize("start, bearing", [
    ((10.0, 179.999), 90),       # across the 180° meridian
    ((-33.9, 151.2), 270),       # south of the equator
    ((69.0, -20.0), 45),         # in the far north
])
def test_anywhere_on_earth(start, bearing):
    assert direction_at(walk([(300, 1.0, bearing)], start), T0 + 150) == (bearing, None)


def test_slowest_movement_with_a_direction():
    # 20 m within 60 s on each side is at least 0.33 m/s
    assert direction_at(walk([(300, 0.40, 90)]), T0 + 150) == (90, None)
    assert direction_at(walk([(300, 0.30, 90)]), T0 + 150) == (None, STILL)


def test_no_direction_at_a_stop():
    points = walk([(120, 1.0, 0), (300, 0.0, 0), (120, 1.0, 0)])
    assert direction_at(points, T0 + 270) == (None, STILL)
    # Walking on 10 s after the stop: the way in is still at the stop
    assert direction_at(points, T0 + 430) == (None, STILL)
    assert direction_at(points, T0 + 470) == (0, None)


def test_a_short_pause_to_take_a_photo_keeps_the_direction():
    points = walk([(120, 1.0, 0), (40, 0.0, 0), (120, 1.0, 0)])
    assert direction_at(points, T0 + 140) == (0, None)


@pytest.mark.parametrize("offset", [-30, 0, 290, 300, 330])
def test_no_direction_near_the_ends_of_the_track(offset):
    assert direction_at(walk([(300, 1.0, 0)]), T0 + offset) == (None, EDGE)


@pytest.mark.parametrize("legs, t, expected", [
    # A hairpin: up 120 s, 5 s across, back 120 s
    ([(120, 1.0, 0), (5, 1.0, 90), (120, 1.0, 180)], 122, (None, WINDING)),
    ([(120, 1.0, 0), (120, 1.0, 90)], 120, (None, WINDING)),        # a right angle
    ([(120, 1.0, 0), (120, 1.0, 45)], 120, (22, None)),             # a bend of 45° passes
])
def test_turns(legs, t, expected):
    assert direction_at(walk(legs), T0 + t) == expected


def test_switchbacks_give_no_direction_across_the_bends():
    # Zigzag up a slope: 25 m legs to the north-east and the north-west
    points = walk([(25, 1.0, 45 if k % 2 == 0 else 315) for k in range(20)])
    found = [direction_at(points, T0 + t)[0] for t in range(80, 420)]
    assert all(d is None or min(abs(d - 0), 360 - d) <= 45 for d in found)


def test_sparse_tracks():
    every_30_s = walk([(600, 1.0, 30)])[::30]
    assert direction_at(every_30_s, T0 + 301) == (30, None)
    every_90_s = walk([(900, 1.0, 30)])[::90]
    assert direction_at(every_90_s, T0 + 450) == (None, NO_POINTS)


def test_no_direction_in_a_break_in_recording():
    before = walk([(120, 1.0, 0)])
    resumed = before[-1][0] + 600
    after = [(resumed + k, before[-1][1] + k / M_PER_DEGREE, before[-1][2], None)
             for k in range(120)]
    assert direction_at(before + after, resumed - 300) == (None, NO_POINTS)


@pytest.mark.parametrize("pause, t", [(100, 210), (40, 150), (40, 180)])
def test_no_direction_across_a_short_break_in_recording(pause, t):
    # Walking east for 160 s, then an auto-pause; recording resumes 45 m to
    # the north. The way between is not known, so north is no direction.
    before = walk([(160, 1.0, 90)])
    last = before[-1]
    lat = last[1] + 45 / M_PER_DEGREE
    after = [(last[0] + pause + p[0] - T0, p[1], p[2], None)
             for p in walk([(120, 1.0, 90)], start=(lat, last[2]))]
    assert direction_at(before + after, T0 + t) == (None, BREAK)
    assert direction_at(before + after, T0 + 100) == (90, None)


def test_no_direction_from_two_files_recorded_at_the_same_time():
    # A watch and a phone 60 m apart, both recording the same walk east:
    # merged, the track zigzags between them
    watch = walk([(300, 1.4, 90)])
    phone = walk([(300, 1.4, 90)], start=(50.0 + 60 / M_PER_DEGREE, 20.0))
    merged = sorted([(p, 0) for p in watch] + [(p, 1) for p in phone], key=lambda x: x[0][0])
    points = [p for p, _ in merged]
    sources = [source for _, source in merged]
    times = [p[0] for p in points]
    t = T0 + 150.5
    lat, lon = locate(points, times, t, 120)[:2]
    # Points 1 s apart but 60 m apart: the chord between them points south
    assert travel_direction(points, times, t, lat, lon)[0] in range(170, 190)
    assert travel_direction(points, times, t, lat, lon, sources) == (None, FILES)
    # One file alone is fine
    alone = [p for p, source in merged if source == 0]
    assert travel_direction(alone, [p[0] for p in alone], t, *locate(
        alone, [p[0] for p in alone], t, 120)[:2], [0] * len(alone)) == (90, None)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_gps_noise(seed):
    # A phone's error of about 5 m: standing never gets a direction, and
    # walking at 1 m/s gets one for about half of the photos, 95% of them
    # within 25° and all within 45°
    standing = walk([(600, 0.0, 0)], noise=(5.0, 10.0, seed))
    assert [direction_at(standing, T0 + t)[0] for t in range(70, 530)] == [None] * 460
    walking = walk([(600, 1.0, 30)], noise=(5.0, 10.0, seed))
    known = [d for d in (direction_at(walking, T0 + t)[0] for t in range(70, 530))
             if d is not None]
    errors = sorted(min(abs(d - 30), 360 - abs(d - 30)) for d in known)
    assert len(known) > 200
    assert errors[int(0.95 * len(errors))] <= 25 and errors[-1] <= 45


def test_no_direction_inside_the_stops_of_a_noisy_track():
    points = walk([(300, 1.0, 90), (600, 0.0, 0), (300, 1.0, 90)], noise=(3.0, 10.0, 7))
    [stop] = find_stops(points)
    assert all(direction_at(points, t)[0] is None for t in range(int(stop.start), int(stop.end)))
