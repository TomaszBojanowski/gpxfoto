"""Tests on the author's own tracks and photos in tests/prywatne/.

That directory is ignored by git and never committed. The tests are
skipped when it holds no files; with real files they check the parts
that synthetic test files cannot: real Garmin tracks and real camera
JPEGs with maker notes, thumbnails and MPF previews.
"""
import glob
import os
import re
import shutil
import time
from datetime import datetime, timezone

import pytest

from conftest import needs_exiftool, read_tags, run_cli
from gpxfoto.engine.matching import match_photos
from gpxfoto.engine.photos import TZ_CAMERA, Photo
from gpxfoto.engine.track import load_track
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
