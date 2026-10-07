"""Tests on the author's own tracks and photos in tests/prywatne/.

That directory is ignored by git and never committed. The tests are
skipped when it holds no files; with real files they check the parts
that synthetic test files cannot: real Garmin tracks and real camera
JPEGs with maker notes, thumbnails and MPF previews.
"""
import glob
import os
import random
import re
import shutil
import time
from datetime import datetime, timezone

import pytest

from conftest import needs_exiftool, read_tags, run_cli
from gpxfoto.engine.checks import Shot, pace, suspicious_match
from gpxfoto.engine.matching import match_photos
from gpxfoto.engine.photos import TZ_CAMERA, Photo
from gpxfoto.engine.track import load_track, place
from gpxfoto.engine.writer import image_checksum

PRIVATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prywatne")


def private_files(*patterns):
    found = []
    for pattern in patterns:
        found += glob.glob(os.path.join(PRIVATE, pattern))
    return sorted(set(found))


TRACKS = private_files("*.gpx", "*.GPX")
PHOTOS = private_files("*.jpg", "*.JPG", "*.jpeg", "*.JPEG")

needs_tracks = pytest.mark.skipif(not TRACKS, reason="no GPX files in tests/prywatne/")
needs_photos = pytest.mark.skipif(not (TRACKS and PHOTOS),
                                  reason="no GPX files and photos in tests/prywatne/")


@needs_tracks
@pytest.mark.parametrize("path", TRACKS, ids=os.path.basename)
def test_track_loads_quickly(path):
    # The best of five runs, so that a busy machine does not fail the test
    elapsed = []
    for _ in range(5):
        start = time.perf_counter()
        track = load_track([path])
        elapsed.append(time.perf_counter() - start)
    elapsed = min(elapsed)
    points = track.points
    assert [p[0] for p in points] == sorted(p[0] for p in points)
    assert all(-90 <= p[1] <= 90 and -180 <= p[2] <= 180 for p in points)
    # Plan target: 20 000 points in under 0.5 s, stops included
    assert elapsed < 0.5 * max(1, len(points) / 20000)
    for a, b in zip(track.stops, track.stops[1:]):
        assert a.last < b.first
    assert all(points[s.first][0] == s.start and points[s.last][0] == s.end for s in track.stops)


@needs_tracks
@pytest.mark.parametrize("path", TRACKS, ids=os.path.basename)
def test_matching_1000_photos_is_quick(path):
    track = load_track([path])
    step = (track.last - track.first) / 1000
    photos = [Photo(f"{i}.jpg", datetime.fromtimestamp(track.first + step * (i + 0.5),
                                                        timezone.utc), TZ_CAMERA, None, False)
              for i in range(1000)]
    elapsed = []
    for correction in range(3):
        start = time.perf_counter()
        results = match_photos(photos, [track], correction, 120)
        elapsed.append(time.perf_counter() - start)
    assert sum(result.reason is None for result in results) == 1000
    # Plan target: matching 1000 photos again after the time slider moves
    assert min(elapsed) < 0.016


@needs_exiftool
@needs_photos
def test_photos_get_location_and_keep_image(tmp_path):
    for photo in PHOTOS:
        shutil.copy2(photo, tmp_path)
    copies = sorted(str(tmp_path / os.path.basename(p)) for p in PHOTOS)
    before = {p: (image_checksum(p), os.stat(p).st_mtime_ns) for p in copies}
    gpx_args = [arg for track in TRACKS for arg in ("-g", track)]

    preview = run_cli(tmp_path, *gpx_args, "--overwrite")
    assert preview.returncode == 0, preview.stderr
    for p in copies:
        assert (image_checksum(p), os.stat(p).st_mtime_ns) == before[p]

    contents = {p: open(p, "rb").read() for p in copies}
    written = run_cli(tmp_path, *gpx_args, "--overwrite", "--write")
    assert written.returncode == 0, written.stdout + written.stderr
    matched = int(re.search(r"^Matched: (\d+)", written.stdout, re.M).group(1))
    count = int(re.search(r"^Written: (\d+), errors: 0$", written.stdout, re.M).group(1))
    # The photos must lie on the tracks, otherwise this test checks nothing
    assert count == matched >= 1, written.stdout
    changed = 0
    for p in copies:
        assert (image_checksum(p), os.stat(p).st_mtime_ns) == before[p]
        if open(p, "rb").read() != contents[p]:
            changed += 1
            tags = read_tags(p, "GPSLatitude", "GPSLongitude")
            assert "GPSLatitude" in tags and "GPSLongitude" in tags
    assert changed == count, written.stdout


# --- warnings about a suspicious match ---------------------------------
#
# The author's photos are not needed for most of these: photo times are
# modelled on the real track, the way a photographer takes them. A moment
# is a stop (a random time during a stop), a pause (a random time while
# the watch froze the position for 5 s or more outside stops) or walking
# (any other time); each moment has 1 to 3 photos 1 to 4 s apart.

PHOTOGRAPHERS = {            # shares of stop, pause and walking moments
    "stops": (0.5, 0.3, 0.2),
    "mixed": (0.3, 0.4, 0.3),
    "pauses": (0.1, 0.6, 0.3),
}


class Modelled:
    """A real track with what photo times are modelled on."""

    def __init__(self, path):
        track = load_track([path])
        self.points, self.times, self.stops = track.points, track.times, track.stops
        self.pace = pace(self.points, self.stops)
        standing = set()
        for stop in self.stops:
            standing.update(range(stop.first, stop.last + 1))
        points = self.points
        self.pauses = []
        i = 0
        while i < len(points):
            j = i
            while j + 1 < len(points) and points[j + 1][1:3] == points[i][1:3]:
                j += 1
            if points[j][0] - points[i][0] >= 5 and not standing.intersection(range(i, j + 1)):
                self.pauses.append((points[i][0], points[j][0]))
            i = j + 1
        self.walking = [k for k in range(len(points)) if k not in standing]

    def shots(self, seed, count, shares, error=0.0, zone=7200):
        """count photos from a camera whose clock is error s ahead."""
        r = random.Random(seed)
        true = []
        while len(true) < count:
            kind = r.choices(("stop", "pause", "walk"), shares)[0]
            if kind == "stop":
                stop = r.choice(self.stops)
                t = r.uniform(stop.start, stop.end)
            elif kind == "pause":
                t = r.uniform(*r.choice(self.pauses))
            else:
                t = self.times[r.choice(self.walking)] + r.random()
            true += [t + k * r.uniform(1, 4) for k in range(r.randint(1, 3))]
        shots = []
        for t in sorted(true[:count]):
            found = place(self.points, self.times, self.stops, t + error, 120)
            lat, lon = (found[0], found[1]) if found[0] is not None else (None, None)
            shots.append(Shot(t + error, t + error + zone, lat, lon))
        return shots

    def warnings(self, shots):
        return suspicious_match(self.points, self.times, self.stops, shots, 120, self.pace)


@pytest.fixture(scope="module", params=TRACKS, ids=os.path.basename)
def real_track(request):
    track = Modelled(request.param)
    if len(track.stops) < 6 or len(track.pauses) < 20:
        pytest.skip("the track has fewer than 6 stops or 20 pauses to model photos on")
    return track


@needs_tracks
def test_no_warnings_for_photos_at_the_right_time(real_track):
    # 900 photo sets: 3 photographers, 20, 40 and 100 photos, 100 seeds.
    # On the 5.5-hour hike: no warning at all
    shifts = motion = jumped = 0
    for shares in PHOTOGRAPHERS.values():
        for count in (20, 40, 100):
            for seed in range(100):
                found = real_track.warnings(real_track.shots(seed, count, shares))
                shifts += found.shift is not None
                motion += found.motion is not None
                jumped += bool(found.jumps)
    assert jumped == 0
    assert shifts <= 2 and motion <= 5, \
        f"false alarms in 900 sets: {shifts} shifts, {motion} motion"


@needs_tracks
@pytest.mark.parametrize("error", [3600, -3600, 1800])
def test_a_clock_off_by_whole_hours_is_found(real_track, error):
    # 40 photos, mostly at stops: on the hike the shift is found 65-68% of
    # the time, and never a wrong one
    found = [real_track.warnings(real_track.shots(seed, 40, PHOTOGRAPHERS["stops"], error)).shift
             for seed in range(100)]
    right = sum(f is not None and f.shift == -error for f in found)
    wrong = sum(f is not None and f.shift != -error for f in found)
    assert right >= 60 and wrong <= 1, f"right {right}, wrong {wrong} of 100"


@needs_tracks
@pytest.mark.parametrize("error", [120, -300, 600])
def test_a_clock_off_by_minutes_gives_the_motion_warning(real_track, error):
    # 40 photos from the mixed photographer: warned 59-68% of the time
    warned = sum(real_track.warnings(real_track.shots(seed, 40, PHOTOGRAPHERS["mixed"],
                                                      error)).motion is not None
                 for seed in range(100))
    assert warned >= 50, f"warned {warned} of 100"


@needs_tracks
def test_checking_1000_photos_is_quick(real_track):
    shots = real_track.shots(7, 1000, PHOTOGRAPHERS["mixed"], 300)
    elapsed = []
    for _ in range(3):
        start = time.perf_counter()
        real_track.warnings(shots)
        elapsed.append(time.perf_counter() - start)
    # Run again after the time slider stops, like the matching
    assert min(elapsed) < 0.030
    elapsed = []
    for _ in range(3):
        start = time.perf_counter()
        pace(real_track.points, real_track.stops)
        elapsed.append(time.perf_counter() - start)
    assert min(elapsed) < 0.1 * max(1, len(real_track.points) / 20000)


OPTIONS_FILE = os.path.join(PRIVATE, "options.txt")


@needs_exiftool
@needs_photos
def test_no_warning_for_the_reference_photos():
    # The author's reference photos with their correct match. Options the
    # match needs, such as --offset=2, go one per line into options.txt.
    options = []
    if os.path.exists(OPTIONS_FILE):
        with open(OPTIONS_FILE, encoding="utf-8") as f:
            options = [line.strip() for line in f if line.strip()]
    gpx_args = [arg for track in TRACKS for arg in ("-g", track)]
    preview = run_cli(*PHOTOS, *gpx_args, "--overwrite", *options)
    assert preview.returncode == 0, preview.stderr
    assert re.search(r"^Matched: [1-9]", preview.stdout, re.M), preview.stdout
    assert not [line for line in preview.stdout.splitlines() if line.startswith("Warning")], \
        preview.stdout
