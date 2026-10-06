"""Tests on the author's own tracks and photos in tests/prywatne/.

That directory is ignored by git and never committed. The tests are
skipped when it holds no files; with real files they check the parts
that synthetic test files cannot: real Garmin tracks and real camera
JPEGs with maker notes, thumbnails and MPF previews.
"""
import glob
import os
import shutil
import time

import pytest

from conftest import needs_exiftool, read_tags, run_cli
from gpxfoto.engine.track import load_gpx
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
    start = time.perf_counter()
    points = load_gpx([path])
    elapsed = time.perf_counter() - start
    assert points
    assert [p[0] for p in points] == sorted(p[0] for p in points)
    assert all(-90 <= p[1] <= 90 and -180 <= p[2] <= 180 for p in points)
    # Plan target: 20 000 points in under 0.5 s
    assert elapsed < 0.5 * max(1, len(points) / 20000)


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

    written = run_cli(tmp_path, *gpx_args, "--overwrite", "--write")
    assert written.returncode == 0, written.stdout + written.stderr
    located = 0
    for p in copies:
        assert (image_checksum(p), os.stat(p).st_mtime_ns) == before[p]
        tags = read_tags(p, "GPSLatitude", "GPSLongitude")
        located += "GPSLatitude" in tags and "GPSLongitude" in tags
    assert located, written.stdout
