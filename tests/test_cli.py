"""End-to-end tests of the command-line tool (python -m gpxfoto)."""
import locale
import math
import os
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest

from conftest import (
    hike_gpx, latin2_name, make_jpeg, needs_exiftool, read_tags, run_cli,
    set_panasonic_time_stamp, set_tags, write_gpx)
from gpxfoto import cli, i18n
from gpxfoto.engine import track as track_module
from gpxfoto.engine.checks import Jump, Motion, ShiftHint, Shot
from gpxfoto.engine.clock import correction_from, parse_reading
from gpxfoto.engine.writer import image_checksum
from test_checks import at_stops, in_pauses, pauses_hike, stops_hike
from test_stops import T0, Hike, position

# run_cli uses Europe/Warsaw, which is UTC+2 on these dates, and the C
# locale (dates as MM/DD/YY).
TRACK = [
    ("2024-05-01T10:00:00Z", 50.0, 20.0, 200.0),
    ("2024-05-01T10:01:40Z", 50.001, 20.002, 210.0),
]
TRACK_LINE = ("Track track.gpx: 2 points, 05/01/24 12:00:00 – 05/01/24 12:01:40 "
              "(this computer’s time zone)")
PREVIEW_LINE = "This was a preview; no files were changed. Use --write to write the locations."
VERIFIED_LINE = "The image data of every written file was verified as unchanged."
# 50 s after the start of TRACK, halfway between its two points
MATCH_LINE = "  a.jpg            12:00:50  50.000500, 20.001000    205 m"

OLD_TIME_NS = 1_600_000_000_000_000_000
GPS_TAGS = ("GPS:GPSLatitude", "GPS:GPSLatitudeRef", "GPS:GPSLongitude", "GPS:GPSLongitudeRef",
            "GPS:GPSAltitude", "GPS:GPSAltitudeRef", "GPS:GPSDateStamp", "GPS:GPSTimeStamp",
            "GPS:GPSMapDatum")

needs_posix_shell = pytest.mark.skipif(os.name != "posix", reason="needs /bin/sh")


def written(count, errors):
    """Summary lines after writing; the check is reported only if a file was written."""
    lines = [f"Written: {count}, errors: {errors}"]
    return lines if str(count) == "0" else lines + [VERIFIED_LINE]


def taken(local_time, offset="+02:00", date="2024:05:01"):
    tags = [f"-DateTimeOriginal={date} {local_time}"]
    if offset is not None:
        tags.append(f"-OffsetTimeOriginal={offset}")
    return tags


def gps_written(lat, lon, ele, date, time):
    return {
        "GPSLatitude": pytest.approx(abs(lat), abs=1e-7),
        "GPSLatitudeRef": "N" if lat >= 0 else "S",
        "GPSLongitude": pytest.approx(abs(lon), abs=1e-7),
        "GPSLongitudeRef": "E" if lon >= 0 else "W",
        "GPSAltitude": pytest.approx(abs(ele)),
        "GPSAltitudeRef": 0 if ele >= 0 else 1,
        "GPSDateStamp": date,
        "GPSTimeStamp": time,
        "GPSMapDatum": "WGS-84",
    }


def fake_exiftool(directory, script):
    """Put an executable "exiftool" shell script into directory."""
    directory.mkdir(exist_ok=True)
    path = directory / "exiftool"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return {"PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"}


@pytest.fixture
def gpx(tmp_path):
    return write_gpx(tmp_path / "track.gpx", TRACK)


@pytest.fixture
def photo(jpeg_file):
    """Create photos/<name> with the given tags and an old modification time."""
    def create(name, tags=()):
        path = jpeg_file("photos/" + name)
        if tags:
            set_tags(path, *tags)
        os.utime(path, ns=(OLD_TIME_NS, OLD_TIME_NS))
        return path
    return create


@needs_exiftool
def test_preview_shows_positions_and_changes_nothing(photo, gpx):
    files = [photo("a.jpg", taken("12:00:50")),
             photo("nodate.jpg"),
             photo("nozone.jpg", taken("12:00:30", offset=None))]
    before = {f: f.read_bytes() for f in files}

    result = run_cli(files[0].parent, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE,
        MATCH_LINE,
        "  nodate.jpg       skipped: no capture time in EXIF",
        "  nozone.jpg       12:00:30  50.000300, 20.000600    203 m"
        "  [computer’s time zone (not in EXIF)]",
        "Matched: 2, skipped: 1",
        PREVIEW_LINE,
    ]
    assert result.stderr == ""
    for f in files:
        assert f.read_bytes() == before[f]
        assert f.stat().st_mtime_ns == OLD_TIME_NS
    assert sorted(os.listdir(files[0].parent)) == ["a.jpg", "nodate.jpg", "nozone.jpg"]


@needs_exiftool
def test_write_adds_location_and_keeps_image_mode_and_mtime(photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    path.chmod(0o600)
    checksum = image_checksum(path)

    result = run_cli(path, "--gpx", gpx, "--write")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", *written(1, 0)]
    assert read_tags(path, *GPS_TAGS) == gps_written(50.0005, 20.001, 205, "2024:05:01",
                                                     "10:00:50")
    assert image_checksum(path) == checksum
    assert path.stat().st_mtime_ns == OLD_TIME_NS
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # No temporary file and no backup directory are left behind
    assert os.listdir(path.parent) == ["a.jpg"]


@needs_exiftool
def test_several_gpx_files_and_southern_western_positions(tmp_path, photo, gpx):
    chile_gpx = write_gpx(tmp_path / "chile.gpx", [
        ("2024-05-02T16:00:00Z", -33.0, -70.0, -10.0),
        ("2024-05-02T16:01:40Z", -33.001, -70.002, -20.0),
    ])
    chile = photo("chile.jpg", taken("12:00:50", "-04:00", date="2024:05:02"))
    poland = photo("poland.jpg", taken("12:00:50"))

    result = run_cli(chile.parent, "-g", gpx, "-g", chile_gpx, "--write")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Track: 4 points, 05/01/24 12:00:00 – 05/02/24 18:01:40 (this computer’s time zone)",
        "  chile.jpg        12:00:50  -33.000500, -70.001000    -15 m  chile.gpx",
        "  poland.jpg       12:00:50  50.000500, 20.001000    205 m  track.gpx",
        "Matched: 2, skipped: 0",
        *written(2, 0),
    ]
    assert read_tags(chile, *GPS_TAGS) == gps_written(-33.0005, -70.001, -15, "2024:05:02",
                                                      "16:00:50")
    assert read_tags(poland, *GPS_TAGS) == gps_written(50.0005, 20.001, 205, "2024:05:01",
                                                       "10:00:50")


@needs_exiftool
def test_several_gpx_files_name_the_source_of_each_photo(tmp_path, photo):
    morning = write_gpx(tmp_path / "morning.gpx", [("2024-05-01T10:00:00Z", 50.0, 20.0, None),
                                                   ("2024-05-01T10:01:40Z", 50.0, 20.002, None)])
    # In another directory, with the same file name
    (tmp_path / "phone").mkdir()
    afternoon = write_gpx(tmp_path / "phone" / "morning.gpx", [
        ("2024-05-01T11:00:00Z", 50.1, 20.1, None), ("2024-05-01T11:01:40Z", 50.1, 20.102, None)])
    a = photo("a.jpg", taken("12:00:50"))
    photo("b.jpg", taken("12:30:00"))
    photo("c.jpg", taken("13:00:50"))

    result = run_cli(a.parent, "-g", morning, "-g", afternoon)

    assert result.returncode == 0, result.stderr
    second = f"{tmp_path}/phone/morning.gpx"
    first = f"{tmp_path}/morning.gpx"
    assert result.stdout.splitlines()[1:4] == [
        f"  a.jpg            12:00:50  50.000000, 20.001000        —  {first}",
        "  b.jpg            12:30:00  skipped: gap in the track recording, nearest point 28 min "
        f"away ({first}, {second})",
        f"  c.jpg            13:00:50  50.100000, 20.101000        —  {second}",
    ]


@pytest.fixture
def track_dir(tmp_path):
    """tracks/ with day0.gpx (30 April), day1.gpx, day2.gpx (Chile), sub/day3.gpx and others."""
    tracks = tmp_path / "tracks"
    (tracks / "sub").mkdir(parents=True)
    write_gpx(tracks / "day0.gpx", [("2024-04-30T10:00:00Z", 49.0, 19.0, None),
                                    ("2024-04-30T11:00:00Z", 49.1, 19.0, None)])
    write_gpx(tracks / "day1.gpx", TRACK)
    write_gpx(tracks / "day2.GPX", [("2024-05-02T16:00:00Z", -33.0, -70.0, -10.0),
                                    ("2024-05-02T16:01:40Z", -33.001, -70.002, -20.0)])
    write_gpx(tracks / "sub" / "day3.gpx", [("2024-05-03T10:00:00Z", 51.0, 21.0, None),
                                            ("2024-05-03T10:01:40Z", 51.001, 21.0, None)])
    write_gpx(tracks / ".hidden.gpx", TRACK)
    (tracks / "notes.txt").write_text("not a track")
    return tracks


@needs_exiftool
def test_directory_of_tracks_gives_each_photo_its_track(photo, track_dir):
    a = photo("a.jpg", taken("12:00:50"))
    photo("chile.jpg", taken("12:00:50", "-04:00", date="2024:05:02"))

    result = run_cli(a.parent, "-g", track_dir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Tracks covering the photos: 2 of 3 GPX files",
        "Track day1.gpx: 2 points, 05/01/24 12:00:00 – 05/01/24 12:01:40 "
        "(this computer’s time zone)",
        "Track day2.GPX: 2 points, 05/02/24 18:00:00 – 05/02/24 18:01:40 "
        "(this computer’s time zone)",
        MATCH_LINE + "  day1.gpx",
        "  chile.jpg        12:00:50  -33.000500, -70.001000    -15 m  day2.GPX",
        "Matched: 2, skipped: 0",
        PREVIEW_LINE,
    ]


@needs_exiftool
def test_recursive_also_finds_tracks_in_subdirectories(photo, track_dir):
    path = photo("a.jpg", taken("12:00:50", date="2024:05:03"))

    flat = run_cli(path, "-g", track_dir)
    deep = run_cli(path, "-g", track_dir, "-r")

    assert flat.returncode == deep.returncode == 0, flat.stderr + deep.stderr
    assert flat.stdout.splitlines()[:2] == [
        "Tracks covering the photos: 0 of 3 GPX files",
        "  a.jpg            12:00:50  skipped: 17 h 59 min after the end of the nearest track "
        "(day2.GPX)"]
    assert deep.stdout.splitlines()[:3] == [
        "Tracks covering the photos: 1 of 4 GPX files",
        "Track day3.gpx: 2 points, 05/03/24 12:00:00 – 05/03/24 12:01:40 "
        "(this computer’s time zone)",
        "  a.jpg            12:00:50  51.000500, 21.000000        —  day3.gpx"]


@needs_exiftool
def test_track_files_no_photo_needs_are_not_read(photo, track_dir):
    # Its times are readable, but the rest of the file is not
    broken = track_dir / "broken.gpx"
    broken.write_text('<gpx><trk><trkseg><trkpt lat="1" lon="2">'
                      "<time>2024-06-01T10:00:00Z</time></trkpt>")
    path = photo("a.jpg", taken("12:00:50"))

    result = run_cli(path, "-g", track_dir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:3] == [
        "Tracks covering the photos: 1 of 4 GPX files",
        "Track day1.gpx: 2 points, 05/01/24 12:00:00 – 05/01/24 12:01:40 "
        "(this computer’s time zone)",
        MATCH_LINE + "  day1.gpx"]


@needs_exiftool
def test_track_file_that_cannot_be_scanned_quickly_is_read(photo, tmp_path):
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    # A DOCTYPE could define what a time is, so the quick scan gives up
    text = write_gpx(tracks / "day1.gpx", TRACK).read_text()
    (tracks / "day1.gpx").write_text(text.replace("<gpx ", "<!DOCTYPE gpx>\n<gpx ", 1))
    path = photo("a.jpg", taken("12:00:50"))

    result = run_cli(path, "-g", tracks)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[2] == MATCH_LINE + "  day1.gpx"


@needs_exiftool
@pytest.mark.parametrize("write", [False, True])
def test_track_file_that_cannot_be_read_leaves_only_its_photos(photo, track_dir, write):
    # Its times can be scanned, but the file is cut off
    text = (track_dir / "day2.GPX").read_text()
    (track_dir / "day2.GPX").write_text(text[:text.rindex("</trkpt>")])
    a = photo("a.jpg", taken("12:00:50"))
    chile = photo("chile.jpg", taken("12:00:50", "-04:00", date="2024:05:02"))
    before = chile.read_bytes()

    result = run_cli(a.parent, "-g", track_dir, *(["--write"] if write else []))

    # The run goes on; the exit status tells that a file was skipped
    assert result.returncode == 1
    error, skipped = result.stderr.splitlines()
    assert error.startswith(f"Cannot read the GPX file {track_dir / 'day2.GPX'}: ")
    assert skipped == SKIPPED_FILE
    assert result.stdout.splitlines()[2:5] == [
        MATCH_LINE + "  day1.gpx",
        "  chile.jpg        12:00:50  skipped: the track day2.GPX cannot be read",
        "Matched: 1, skipped: 1"]
    assert chile.read_bytes() == before
    if write:
        assert read_tags(a, "GPSLatitude")["GPSLatitude"] == pytest.approx(50.0005)


SKIPPED_FILE = "  This file is skipped; the other tracks are used."

# Files whose times cannot be scanned quickly, so which photos they would
# cover is not known, and which then cannot be read either
UNSCANNABLE = {
    "doctype": "<!DOCTYPE gpx>\n<gpx><trk>".encode(),
    "utf-16": '<?xml version="1.0" encoding="UTF-16"?><gpx><trk>'.encode("utf-16"),
    "binary": bytes(range(256)) * 4,
}


@needs_exiftool
@pytest.mark.parametrize("content", UNSCANNABLE.values(), ids=UNSCANNABLE)
def test_track_file_of_unknown_time_that_cannot_be_read_is_skipped(photo, track_dir, content):
    (track_dir / "broken.gpx").write_bytes(content)
    path = photo("a.jpg", taken("12:00:50"))
    checksum = image_checksum(path)

    result = run_cli(path, "-g", track_dir, "--write")

    assert result.returncode == 1
    error, skipped = result.stderr.splitlines()
    assert error.startswith(f"Cannot read the GPX file {track_dir / 'broken.gpx'}: ")
    assert skipped == SKIPPED_FILE
    # The other tracks are used as if the file were not there
    assert result.stdout.splitlines() == [
        "Tracks covering the photos: 1 of 4 GPX files",
        TRACK_LINE.replace("track.gpx", "day1.gpx"),
        MATCH_LINE + "  day1.gpx",
        "Matched: 1, skipped: 0",
        *written(1, 0)]
    assert read_tags(path, "GPSLatitude")["GPSLatitude"] == pytest.approx(50.0005)
    assert image_checksum(path) == checksum


@needs_exiftool
def test_other_tracks_place_photos_in_the_time_of_a_file_that_cannot_be_read(photo, track_dir):
    # A cut-off copy of day1.gpx covers the same time
    text = (track_dir / "day1.gpx").read_text()
    (track_dir / "copy.gpx").write_text(text[:text.rindex("</trkpt>")])
    path = photo("a.jpg", taken("12:00:50"))

    result = run_cli(path, "-g", track_dir)

    assert result.returncode == 1
    assert result.stderr.splitlines()[1:] == [SKIPPED_FILE]
    assert result.stdout.splitlines()[2:4] == [MATCH_LINE + "  day1.gpx", "Matched: 1, skipped: 0"]


@needs_exiftool
def test_every_track_file_that_cannot_be_read_is_skipped(photo, tmp_path):
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    (tracks / "a.gpx").write_bytes(UNSCANNABLE["doctype"])
    text = write_gpx(tracks / "b.gpx", TRACK).read_text()
    (tracks / "b.gpx").write_text(text[:text.rindex("</trkpt>")])
    path = photo("a.jpg", taken("12:00:50"))
    before = path.read_bytes()

    result = run_cli(path, "-g", tracks, "--write")

    assert result.returncode == 1
    assert result.stderr.splitlines()[1::2] == [SKIPPED_FILE] * 2
    assert "Traceback" not in result.stderr
    assert result.stdout.splitlines() == [
        "Tracks covering the photos: 0 of 2 GPX files",
        "  a.jpg            12:00:50  skipped: the track b.gpx cannot be read",
        "Matched: 0, skipped: 1",
        *written(0, 0)]
    assert path.read_bytes() == before


@needs_exiftool
def test_a_track_file_that_cannot_be_read_is_read_once(photo, track_dir, monkeypatch, capsys):
    """Not again for each photo whose nearest track it would be."""
    text = (track_dir / "day2.GPX").read_text()
    (track_dir / "day2.GPX").write_text(text[:text.rindex("</trkpt>")])
    chile = photo("chile.jpg", taken("12:00:50", "-04:00", date="2024:05:02"))
    for hour in (1, 2, 3):          # half a day after day2.GPX, which no track covers
        photo(f"later{hour}.jpg", taken(f"0{hour}:00:00", "-04:00", date="2024:05:03"))
    reads = []
    read_points = track_module._read_points
    monkeypatch.setattr(track_module, "_read_points",
                        lambda path: reads.append(os.path.basename(path)) or read_points(path))
    monkeypatch.setenv("LANGUAGE", "C")
    monkeypatch.setattr(i18n, "setup", lambda: None)
    monkeypatch.setattr(sys, "argv", ["gpxfoto", str(chile.parent), "-g", str(track_dir)])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    assert reads.count("day2.GPX") == 1
    assert capsys.readouterr().err.count("This file is skipped") == 1


@needs_exiftool
def test_named_track_file_that_cannot_be_read_still_stops_the_run(photo, track_dir, tmp_path):
    broken = tmp_path / "broken.gpx"
    broken.write_text("<gpx>")
    path = photo("a.jpg", taken("12:00:50"))
    before = path.read_bytes()

    result = run_cli(path, "-g", broken, "-g", track_dir, "--write")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Cannot read the GPX file {broken}: no element found: line 1, column 5\n"
    assert path.read_bytes() == before


@needs_exiftool
def test_a_file_that_cannot_be_read_is_not_named_as_the_nearest_track(photo, track_dir):
    # 30 min after day1.gpx; a cut-off file whose times can be scanned,
    # 18 min later, would be nearer
    text = write_gpx(track_dir / "later.gpx", [("2024-05-01T10:50:00Z", 50.0, 20.0, None),
                                               ("2024-05-01T10:51:00Z", 50.0, 20.0, None)]
                     ).read_text()
    (track_dir / "later.gpx").write_text(text[:text.rindex("</trkpt>")])
    path = photo("a.jpg", taken("12:31:40"))

    result = run_cli(path, "-g", track_dir)

    # Read only to find the nearest track, and reported like any other
    assert result.returncode == 1
    error, skipped = result.stderr.splitlines()
    assert error.startswith(f"Cannot read the GPX file {track_dir / 'later.gpx'}: ")
    assert skipped == SKIPPED_FILE

    assert result.stdout.splitlines()[1] == (
        "  a.jpg            12:31:40  skipped: 30 min after the end of the nearest track "
        "(day1.gpx)")


@needs_exiftool
def test_photo_no_track_covers_names_the_nearest_one(photo, track_dir):
    early = photo("early.jpg", taken("11:00:00"))
    photo("late.jpg", taken("12:30:00"))
    photo("far.jpg", taken("12:00:00", date="2024:04:25"))

    result = run_cli(early.parent, "-g", track_dir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        "  early.jpg        11:00:00  skipped: 60 min before the start of the nearest track "
        "(day1.gpx)",
        "  far.jpg          12:00:00  skipped: no track covers this time",
        "  late.jpg         12:30:00  skipped: 28 min after the end of the nearest track "
        "(day1.gpx)"]


@needs_exiftool
def test_directory_without_tracks(photo, tmp_path):
    path = photo("a.jpg", taken("12:00:50"))
    (tmp_path / "empty").mkdir()

    result = run_cli(path, "-g", tmp_path / "empty")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "No GPX files found.\n"


def test_file_labels():
    assert cli.file_labels(["a/x.gpx", "b/y.gpx", "c/x.gpx"]) == {
        "a/x.gpx": "a/x.gpx", "b/y.gpx": "y.gpx", "c/x.gpx": "c/x.gpx"}


@needs_exiftool
def test_photo_with_location_is_skipped_unless_overwrite(photo, gpx):
    path = photo("a.jpg", [*taken("12:00:50"), "-GPSLatitude=1.5", "-GPSLatitudeRef=N",
                           "-GPSLongitude=2.5", "-GPSLongitudeRef=E"])
    original = path.read_bytes()

    kept = run_cli(path, "-g", gpx, "--write")
    assert kept.returncode == 0, kept.stderr
    assert kept.stdout.splitlines() == [
        TRACK_LINE,
        "  a.jpg            skipped: already has a location",
        "Matched: 0, skipped: 1",
        *written(0, 0),
    ]
    assert path.read_bytes() == original

    replaced = run_cli(path, "-g", gpx, "--write", "--overwrite")
    assert replaced.returncode == 0, replaced.stderr
    assert replaced.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", *written(1, 0)]
    assert read_tags(path, "GPS:GPSLatitude", "GPS:GPSLongitude") == {
        "GPSLatitude": pytest.approx(50.0005, abs=1e-7),
        "GPSLongitude": pytest.approx(20.001, abs=1e-7)}


@needs_exiftool
def test_zero_latitude_and_elevation_are_values_not_missing_data(tmp_path, photo):
    gpx = write_gpx(tmp_path / "equator.gpx", [("2024-05-01T10:00:00Z", 0.0, 9.0, 0.0),
                                               ("2024-05-01T10:01:40Z", 0.0, 9.002, 0.0)])
    path = photo("a.jpg", taken("12:00:00"))
    located = photo("null-island.jpg", [*taken("12:00:50"), "-GPSLatitude=0", "-GPSLatitudeRef=N",
                                        "-GPSLongitude=0", "-GPSLongitudeRef=E"])
    original = located.read_bytes()

    result = run_cli(path.parent, "-g", gpx, "--offset", "50", "--write")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE.replace("track.gpx", "equator.gpx"),
        "Clock correction: +50 s",
        "  a.jpg            12:00:50  0.000000, 9.001000      0 m",
        "  null-island.jpg  skipped: already has a location",
        "Matched: 1, skipped: 1",
        *written(1, 0),
    ]
    # The GPS time includes the --offset correction
    assert read_tags(path, *GPS_TAGS) == gps_written(0.0, 9.001, 0.0, "2024:05:01", "10:00:50")
    assert located.read_bytes() == original


@needs_exiftool
@pytest.mark.parametrize("offset, local_time, correction, match_line", [
    ("50", "12:00:00", "+50 s", MATCH_LINE),
    ("-50", "12:01:40", "-50 s", MATCH_LINE),
    ("49.5", "12:00:01", "+49.5 s", "  a.jpg            12:00:50  50.000505, 20.001010    205 m"),
])
def test_offset_shifts_capture_time(photo, gpx, offset, local_time, correction, match_line):
    path = photo("a.jpg", taken(local_time))

    result = run_cli(path, "-g", gpx, "--offset", offset)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, f"Clock correction: {correction}", match_line, "Matched: 1, skipped: 0",
        PREVIEW_LINE]


@needs_exiftool
@pytest.mark.parametrize("offset", ["0", "-0", "0.0"])
def test_zero_offset_shows_no_correction(photo, gpx, offset):
    path = photo("a.jpg", taken("12:00:50"))

    result = run_cli(path, "-g", gpx, "--offset", offset)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", PREVIEW_LINE]


@pytest.fixture
def clock_photo(jpeg_file):
    """A photo of a watch, outside the photos directory, taken at 11:58:38+02:00."""
    def create(*tags, name="watch.jpg"):
        path = jpeg_file(name)
        set_tags(path, *(tags or taken("11:58:38")))
        return path
    return create


CLOCK_LINES = [
    "Clock correction: +2 min 12 s (equivalent to --offset=132)",
    "Clock photo watch.jpg: camera 05/01/24 11:58:38 UTC+02:00, clock 05/01/24 12:00:50 UTC+02:00",
]


@needs_exiftool
def test_clock_photo_corrects_all_photos(photo, gpx, clock_photo):
    watch = clock_photo()
    path = photo("a.jpg", taken("11:58:38"))
    before = {f: f.read_bytes() for f in (watch, path)}

    result = run_cli(path.parent, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00:50")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, *CLOCK_LINES, MATCH_LINE, "Matched: 1, skipped: 0", PREVIEW_LINE]
    assert {f: f.read_bytes() for f in before} == before


@needs_exiftool
def test_clock_correction_is_written_into_the_gps_time(photo, gpx, clock_photo):
    watch = clock_photo()
    path = photo("a.jpg", taken("11:58:38"))
    checksum = image_checksum(path)

    result = run_cli(path.parent, "-g", gpx, "--clock-photo", watch, "--clock-time",
                     "12:00:50+02:00", "--write")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, *CLOCK_LINES, MATCH_LINE, "Matched: 1, skipped: 0", *written(1, 0)]
    assert read_tags(path, *GPS_TAGS) == gps_written(50.0005, 20.001, 205.0, "2024:05:01",
                                                     "10:00:50")
    assert image_checksum(path) == checksum


@needs_exiftool
def test_clock_photo_among_the_photos_gets_the_clock_time(photo, gpx):
    watch = photo("watch.jpg", taken("11:58:38"))

    result = run_cli(watch.parent, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00:50")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, *CLOCK_LINES, MATCH_LINE.replace("a.jpg    ", "watch.jpg"),
        "Matched: 1, skipped: 0", PREVIEW_LINE]


@needs_exiftool
def test_clock_photo_among_the_photos_keeps_its_access_time(photo, gpx):
    # The clock photo is read before the other photos
    watch = photo("watch.jpg", taken("11:58:38"))
    atime = OLD_TIME_NS - 86400 * 10**9
    os.utime(watch, ns=(atime, OLD_TIME_NS))
    result = run_cli(watch, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00:50",
                     "--write")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == written(1, 0)
    assert (watch.stat().st_atime_ns, watch.stat().st_mtime_ns) == (atime, OLD_TIME_NS)


@needs_exiftool
@pytest.mark.parametrize("options, note", [
    ((), "  [computer’s time zone (not in EXIF)]"),
    (("--timezone", "+02:00"), "  [time zone from --timezone]"),
])
def test_time_zone_of_the_clock_photo_is_noted(photo, gpx, clock_photo, options, note):
    watch = clock_photo(*taken("11:58:38", offset=None))
    path = photo("a.jpg", taken("11:58:38"))

    result = run_cli(path, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00:50", *options)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:3] == [CLOCK_LINES[0], CLOCK_LINES[1] + note]


@needs_exiftool
def test_clock_time_without_seconds_is_noted(photo, gpx, clock_photo):
    watch = clock_photo()
    path = photo("a.jpg", taken("11:58:38"))

    result = run_cli(path, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        "Clock correction: +1 min 52 s (equivalent to --offset=112)",
        "Clock photo watch.jpg: camera 05/01/24 11:58:38 UTC+02:00, clock 05/01/24 12:00:30 "
        "UTC+02:00",
        "The time on the clock was given without seconds, so the middle of the minute was used; "
        "the correction may be off by up to 30 s.",
    ]


@needs_exiftool
def test_correction_that_could_be_wrong_changes_nothing(photo, gpx, clock_photo):
    # The camera was not switched to summer time, so its offset is +01:00
    watch = clock_photo(*taken("10:57:38", offset="+01:00"))
    path = photo("a.jpg", taken("10:58:38", offset="+01:00"))
    before = path.read_bytes()

    result = run_cli(path, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00:50",
                     "--write")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "The time on the clock is 1 h 3 min 12 s away from the capture time of the clock photo, "
        "so the clock may have shown a different time zone than the camera. Add the UTC offset "
        "of the time on the clock, for example “12:00:50+02:00” or “12:00:50+01:00”.\n")
    assert path.read_bytes() == before

    fixed = run_cli(path, "-g", gpx, "--clock-photo", watch, "--clock-time", "12:00:50+02:00")
    assert fixed.returncode == 0, fixed.stderr
    assert fixed.stdout.splitlines()[1] == (
        "Clock correction: +3 min 12 s (equivalent to --offset=192)")


@needs_exiftool
def test_unusable_clock_photo_stops_the_run(photo, gpx, tmp_path):
    path = photo("a.jpg", taken("12:00:50"))
    missing = tmp_path / "missing.jpg"

    result = run_cli(path, "-g", gpx, "--clock-photo", missing, "--clock-time", "12:00:50")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Cannot use the clock photo {missing}: no such file or directory\n"


@pytest.mark.parametrize("options, message", [
    (("--clock-photo", "w.jpg"), "--clock-photo must be used together with --clock-time"),
    (("--clock-time", "12:00"), "--clock-time must be used together with --clock-photo"),
    (("--clock-photo", "w.jpg", "--clock-time", "12:00", "--offset", "0"),
     "--offset cannot be used together with --clock-photo"),
    (("--clock-photo", "w.jpg", "--clock-time", "2 PM"),
     "argument --clock-time: not a valid time: 2 PM (use HH:MM:SS or HH:MM, optionally with a "
     "date and a UTC offset, for example 14:03:27, 2026-10-06T14:03:27 or 14:03:27+02:00)"),
])
def test_clock_options_are_checked_first(tmp_path, options, message):
    result = run_cli(tmp_path, "-g", "missing.gpx", *options)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.endswith(f"gpxfoto: error: {message}\n")


@pytest.fixture
def s5ii_photo(photo):
    """A photo with the UTC time an S5II records in its maker note."""
    def create(name, local_time, camera_utc, offset="+02:00"):
        path = photo(name, taken(local_time, offset))
        set_panasonic_time_stamp(path, f"2024:05:01 {camera_utc}")
        os.utime(path, ns=(OLD_TIME_NS, OLD_TIME_NS))
        return path
    return create


@needs_exiftool
def test_capture_time_matching_the_camera_utc_time_changes_nothing(s5ii_photo, gpx):
    path = s5ii_photo("a.jpg", "12:00:50", "10:00:50")

    result = run_cli(path, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", PREVIEW_LINE]


@needs_exiftool
def test_capture_time_against_the_camera_utc_time_is_noted(s5ii_photo, gpx):
    # The camera was at +02:00, but its photos have no offset in EXIF
    first = s5ii_photo("a.jpg", "12:00:50", "10:00:50", offset=None)
    s5ii_photo("b.jpg", "12:30:00", "10:30:00", offset=None)
    s5ii_photo("c.jpg", "11:00:50", "09:00:50", offset=None)

    result = run_cli(first.parent, "-g", gpx, "--timezone", "+01:00")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        "  a.jpg            12:00:50  skipped: 59 min after the end of the track"
        "  [camera’s UTC time suggests +02:00]",
        "  b.jpg            12:30:00  skipped: 88 min after the end of the track"
        "  [camera’s UTC time suggests +02:00]",
        "  c.jpg            11:00:50  50.000500, 20.001000    205 m"
        "  [time zone from --timezone; camera’s UTC time suggests +02:00]",
    ]
    assert result.stdout.splitlines()[4:] == [
        "Matched: 1, skipped: 2",
        "Warning: 3 photos have capture times that do not match the UTC time recorded by the "
        "camera.",
        "  Either the time zone given with --timezone or the camera’s time zone setting is wrong.",
        "  Both times would match with --timezone=+02:00.",
        PREVIEW_LINE,
    ]


@needs_exiftool
def test_no_time_zone_suggested_that_breaks_matching_photos(s5ii_photo, gpx):
    # Before and after a change to winter time: no single --timezone fits
    first = s5ii_photo("a.jpg", "12:00:50", "10:00:50", offset=None)
    s5ii_photo("c.jpg", "12:30:00", "11:30:00", offset=None)

    result = run_cli(first.parent, "-g", gpx, "--timezone", "+02:00")

    lines = result.stdout.splitlines()
    assert lines[2].endswith("  [camera’s UTC time suggests +01:00]")
    start = lines.index("Warning: 1 photo has a capture time that does not match the UTC time "
                        "recorded by the camera.")
    assert lines[start + 1:] == [
        "  Either the time zone given with --timezone or the camera’s time zone setting is wrong.",
        PREVIEW_LINE]


@needs_exiftool
def test_capture_time_changed_in_exif_is_noted(s5ii_photo, gpx):
    # Another program moved the capture time in EXIF by an hour
    path = s5ii_photo("a.jpg", "12:00:20", "09:00:20")

    result = run_cli(path, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:] == [
        "  a.jpg            12:00:20  50.000200, 20.000400    202 m"
        "  [differs by 60 min from the camera’s UTC time]",
        "Matched: 1, skipped: 0",
        "Warning: 1 photo has a capture time that does not match the UTC time recorded by the "
        "camera.",
        "  Another program may have changed the capture time or the time zone in EXIF; the "
        "locations follow the time in EXIF.",
        PREVIEW_LINE,
    ]


@needs_exiftool
@pytest.mark.parametrize("with_zone_in_exif", [False, True])
def test_computer_time_zone_against_the_camera_utc_time(s5ii_photo, photo, gpx,
                                                        with_zone_in_exif):
    # The camera was at +03:00 and recorded no offset; the computer is at +02:00
    path = s5ii_photo("a.jpg", "13:00:50", "10:00:50", offset=None)
    if with_zone_in_exif:
        photo("b.jpg", taken("12:00:50"))

    result = run_cli(path.parent, "-g", gpx)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    start = lines.index("Warning: 1 photo has a capture time that does not match the UTC time "
                        "recorded by the camera.")
    advice = [] if with_zone_in_exif else ["  Both times would match with --timezone=+03:00."]
    # Without a matched photo there is no preview line
    after = [PREVIEW_LINE] if with_zone_in_exif else []
    assert lines[start + 1:] == [
        "  EXIF has no time zone, so this computer’s time zone was used; either it or the "
        "camera’s time zone setting is wrong.", *advice, *after]


# Stops. The track walks 120 s east at 1.2 m/s, stands 180 s and walks
# 120 s, one point a second. While standing, the points jitter 2 m north
# and south of the place and the barometer by 0.4 m; the stop's position
# is their median.
M_PER_DEGREE = math.pi * 6371000.0 / 180
STAND_EAST = 144.0


def lon_at(east):
    return 20.0 + east / (M_PER_DEGREE * math.cos(math.radians(50.0)))


def stop_track():
    points = []
    for i in range(420):
        if 120 <= i < 300:
            east = STAND_EAST
            north, ele = [(2, 200.4), (-2, 199.6), (0, 200.0)][i % 3]
        else:
            east = 1.2 * i if i < 120 else STAND_EAST + 1.2 * (i - 300)
            north, ele = 0, 200.0
        points.append((f"2024-05-01T10:{i // 60:02d}:{i % 60:02d}Z",
                       50.0 + north / M_PER_DEGREE, lon_at(east), ele))
    return points


STOP_TRACK_LINE = ("Track stop.gpx: 420 points, 05/01/24 12:00:00 – 05/01/24 12:06:59 "
                   "(this computer’s time zone)")
# 10:03:30 UTC is 210 s into the track, while standing; the track says 2 m north
AT_STOP = (f"  b.jpg            12:03:30  50.000000, {lon_at(STAND_EAST):.6f}    200 m"
           "  [stop 12:01:52 – 12:05:08]")
ON_TRACK = f"  b.jpg            12:03:30  50.000018, {lon_at(STAND_EAST):.6f}    200 m"
WALKING = f"  a.jpg            12:00:50  50.000000, {lon_at(60):.6f}    200 m"


@pytest.fixture
def stop_gpx(tmp_path):
    return write_gpx(tmp_path / "stop.gpx", stop_track())


@needs_exiftool
def test_photo_taken_during_a_stop_gets_the_stop_position(photo, stop_gpx):
    a = photo("a.jpg", taken("12:00:50"))
    b = photo("b.jpg", taken("12:03:30"))
    checksum = image_checksum(b)

    preview = run_cli(a.parent, "-g", stop_gpx)

    assert preview.returncode == 0, preview.stderr
    assert preview.stdout.splitlines() == [
        STOP_TRACK_LINE, WALKING, AT_STOP, "Matched: 2, skipped: 0",
        "During stops: 1 of 2 matched photos", PREVIEW_LINE]

    written = run_cli(b, "-g", stop_gpx, "--write")

    assert written.returncode == 0, written.stderr
    assert read_tags(b, "GPSLatitude", "GPSLongitude", "GPSAltitude") == {
        "GPSLatitude": pytest.approx(50.0, abs=1e-7),
        "GPSLongitude": pytest.approx(lon_at(STAND_EAST), abs=1e-7),
        "GPSAltitude": pytest.approx(200.0)}
    assert image_checksum(b) == checksum


@needs_exiftool
def test_no_stops_keeps_the_track_position(photo, stop_gpx):
    a = photo("a.jpg", taken("12:00:50"))
    photo("b.jpg", taken("12:03:30"))

    result = run_cli(a.parent, "-g", stop_gpx, "--no-stops")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        STOP_TRACK_LINE, WALKING, ON_TRACK, "Matched: 2, skipped: 0", PREVIEW_LINE]


@needs_exiftool
def test_photo_before_a_stop_at_the_start_of_the_track_keeps_the_first_point(tmp_path, photo):
    # The track starts with the stop; before it, nothing is known
    gpx = write_gpx(tmp_path / "late.gpx", stop_track()[120:])
    b = photo("b.jpg", taken("12:01:30"))

    result = run_cli(b, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        f"  b.jpg            12:01:30  50.000018, {lon_at(STAND_EAST):.6f}    200 m",
        "Matched: 1, skipped: 0",
        "During stops: 0 of 1 matched photo"]


@needs_exiftool
@pytest.mark.parametrize("offset, line, indicator", [
    # A clock 2 min fast puts the photo on the walk before the stop
    ("-120", f"  b.jpg            12:01:30  50.000000, {lon_at(108):.6f}    200 m",
     "During stops: 0 of 1 matched photo"),
    ("0", AT_STOP, "During stops: 1 of 1 matched photo"),
])
def test_count_during_stops_follows_the_clock_correction(photo, stop_gpx, offset, line,
                                                         indicator):
    b = photo("b.jpg", taken("12:03:30"))

    result = run_cli(b, "-g", stop_gpx, "--offset", offset)

    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.splitlines() if not l.startswith("Clock correction")]
    assert lines[1:4] == [line, "Matched: 1, skipped: 0", indicator]


@needs_exiftool
def test_stop_over_a_night_shows_its_dates(tmp_path, photo):
    gpx = write_gpx(tmp_path / "two.gpx", [("2024-05-01T16:00:00Z", 50.0, 20.0, 1000),
                                           ("2024-05-02T05:00:00Z", 50.0, 20.00001, 1000)])
    b = photo("b.jpg", taken("21:00:00"))

    result = run_cli(b, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        "  b.jpg            21:00:00  50.000000, 20.000005   1000 m"
        "  [stop 05/01/24 18:00:00 – 05/02/24 07:00:00]",
        "Matched: 1, skipped: 0",
        "During stops: 1 of 1 matched photo"]


@needs_exiftool
@pytest.mark.parametrize("zone, track_line", [
    ("<+14>-14", "Track edges.gpx: 2 points, 01/03/01 14:00:00 – 12/30/99 13:59:59"),
    ("<-1556>15:56", "Track edges.gpx: 2 points, 01/02/01 08:04:00 – 12/29/99 08:03:59"),
])
def test_track_near_the_ends_of_the_calendar(tmp_path, photo, zone, track_line):
    gpx = write_gpx(tmp_path / "edges.gpx", [
        (time_text, 50.0, 20.0, None) for time_text in (
            "0001-01-02T23:59:59Z", "0001-01-03T00:00:00Z",
            "9999-12-29T23:59:59Z", "9999-12-30T00:00:00Z")])
    path = photo("a.jpg")

    result = run_cli(path, "-g", gpx, env={"TZ": zone})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        track_line + " (this computer’s time zone)",
        "  a.jpg            skipped: no capture time in EXIF",
        "Matched: 0, skipped: 1",
    ]


@needs_exiftool
def test_offset_beyond_the_calendar_skips_the_photo(photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))

    result = run_cli(path, "-g", gpx, "--offset", "1e12")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE,
        "Clock correction: +277777777 h 46 min 40 s",
        "  a.jpg            skipped: the corrected capture time is out of range",
        "Matched: 0, skipped: 1",
    ]


@needs_exiftool
def test_offset_to_the_first_day_of_the_calendar_skips_the_photo(tmp_path, photo):
    # In UTC, the corrected time would fall before the year 1
    gpx = write_gpx(tmp_path / "early.gpx", [("0001-01-03T00:00:00Z", 50.0, 20.0, None)])
    path = photo("a.jpg", taken("00:00:00", offset=None, date="0001:01:03"))

    result = run_cli(path, "-g", gpx, "--timezone", "+14:00", "--offset=-172800",
                     "--max-gap", "1e6")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:] == [
        "Clock correction: -48 h",
        "  a.jpg            skipped: the corrected capture time is out of range",
        "Matched: 0, skipped: 1",
    ]


@needs_exiftool
def test_max_gap_of_zero_matches_only_exact_times(photo, gpx):
    photo("a.jpg", taken("12:00:00"))
    path = photo("b.jpg", taken("12:00:01"))

    result = run_cli(path.parent, "-g", gpx, "--max-gap", "0")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        "  a.jpg            12:00:00  50.000000, 20.000000    200 m",
        "  b.jpg            12:00:01  skipped: gap in the track recording, "
        "nearest point 1 s away",
        "Matched: 1, skipped: 1",
    ]

@needs_exiftool
def test_timezone_overrides_the_one_from_exif(photo, gpx):
    path = photo("a.jpg", taken("12:00:50", "+05:00"))

    from_exif = run_cli(path, "-g", gpx)
    assert from_exif.returncode == 0, from_exif.stderr
    assert from_exif.stdout.splitlines() == [
        TRACK_LINE,
        "  a.jpg            12:00:50  skipped: 2 h 59 min before the start of the track",
        "Matched: 0, skipped: 1",
    ]

    manual = run_cli(path, "-g", gpx, "--timezone", "+02:00")
    assert manual.returncode == 0, manual.stderr
    assert manual.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE + "  [time zone from --timezone]", "Matched: 1, skipped: 0",
        PREVIEW_LINE]


@needs_exiftool
@pytest.mark.parametrize("option", [["--timezone=-03:00"], ["--timezone", "-03:00"]])
def test_negative_timezone(photo, gpx, option):
    path = photo("a.jpg", taken("07:00:50", None))

    result = run_cli(path, "-g", gpx, *option)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1] == (
        "  a.jpg            07:00:50  50.000500, 20.001000    205 m  [time zone from --timezone]")


@needs_exiftool
@pytest.mark.parametrize("value", ["0200", "+2", "+24:00", "+02:75", "+-05:00", ""])
def test_invalid_timezone_exits_with_message(jpeg_file, gpx, value):
    result = run_cli(jpeg_file(), "-g", gpx, "--timezone", value)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "The time zone must be in the form +HH:MM, for example +02:00 or -05:00.\n"


@pytest.mark.parametrize("option", [
    ["--timez", "-05:00"], ["--timez", "+02:00"], ["--wri"], ["--over"], ["--back"],
])
def test_abbreviated_options_are_refused(jpeg_file, gpx, option):
    path = jpeg_file()
    before = path.read_bytes()

    result = run_cli(path, "-g", gpx, "--write", *option)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.endswith(
        "gpxfoto: error: unrecognized arguments: " + " ".join(option) + "\n")
    assert path.read_bytes() == before


@pytest.mark.parametrize("argv, expected", [
    (["--timezone", "-05:00", "a.jpg"], ["--timezone=-05:00", "a.jpg"]),
    (["a.jpg", "--timezone", "-5"], ["a.jpg", "--timezone=-5"]),
    (["--timezone", "+05:00"], ["--timezone", "+05:00"]),
    (["--timezone", "-g", "t.gpx"], ["--timezone", "-g", "t.gpx"]),
    (["--timezone"], ["--timezone"]),
    (["--offset", "-50", "--timezone=-01:00"], ["--offset", "-50", "--timezone=-01:00"]),
    (["--", "--timezone", "-05:00"], ["--", "--timezone", "-05:00"]),
])
def test_join_negative_time_zone(argv, expected):
    assert cli.join_negative_time_zone(argv) == expected


@needs_exiftool
def test_max_gap(tmp_path, photo):
    # Points 10 min and about 1.1 km apart, without elevation
    gpx = write_gpx(tmp_path / "gap.gpx", [("2024-05-01T10:00:00Z", 50.0, 20.0, None),
                                           ("2024-05-01T10:10:00Z", 50.01, 20.0, None)])
    path = photo("a.jpg", taken("12:03:00"))
    track_line = ("Track gap.gpx: 2 points, 05/01/24 12:00:00 – 05/01/24 12:10:00 "
                  "(this computer’s time zone)")

    default = run_cli(path, "-g", gpx)
    assert default.returncode == 0, default.stderr
    assert default.stdout.splitlines() == [
        track_line,
        "  a.jpg            12:03:00  skipped: gap in the track recording, nearest point 3 min away",
        "Matched: 0, skipped: 1",
    ]

    longer = run_cli(path, "-g", gpx, "--max-gap", "200")
    assert longer.returncode == 0, longer.stderr
    assert longer.stdout.splitlines() == [
        track_line,
        "  a.jpg            12:03:00  50.003000, 20.000000        —",
        "Matched: 1, skipped: 0",
        PREVIEW_LINE,
    ]


@needs_exiftool
def test_one_point_track_and_default_max_gap_of_120_s(tmp_path, photo):
    gpx = write_gpx(tmp_path / "point.gpx", [("2024-05-01T10:00:00Z", 50.0, 20.0, 200.0)])
    for name, local_time in [("a.jpg", "11:57:59"), ("b.jpg", "11:58:00"),
                             ("c.jpg", "12:02:00"), ("d.jpg", "12:02:01")]:
        directory = photo(name, taken(local_time)).parent

    result = run_cli(directory, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Track point.gpx: 1 point, 05/01/24 12:00:00 – 05/01/24 12:00:00 "
        "(this computer’s time zone)",
        "  a.jpg            11:57:59  skipped: 2 min before the start of the track",
        "  b.jpg            11:58:00  50.000000, 20.000000    200 m",
        "  c.jpg            12:02:00  50.000000, 20.000000    200 m",
        "  d.jpg            12:02:01  skipped: 2 min after the end of the track",
        "Matched: 2, skipped: 2",
        PREVIEW_LINE,
    ]


@needs_exiftool
def test_recursive_finds_photos_in_subdirectories(photo, gpx):
    top = photo("a.jpg", taken("12:00:50"))
    photo("sub/b.jpg", taken("12:01:40"))
    b_line = "  b.jpg            12:01:40  50.001000, 20.002000    210 m"

    flat = run_cli(top.parent, "-g", gpx)
    assert flat.returncode == 0, flat.stderr
    assert flat.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", PREVIEW_LINE]

    recursive = run_cli(top.parent, "-g", gpx, "-r")
    assert recursive.returncode == 0, recursive.stderr
    assert recursive.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, b_line, "Matched: 2, skipped: 0", PREVIEW_LINE]


@needs_exiftool
def test_backup_keeps_identical_copy_of_original(photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    original = path.read_bytes()

    result = run_cli(path, "-g", gpx, "--write", "--backup")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == written(1, 0)
    assert sorted(os.listdir(path.parent)) == ["a.jpg", "originals"]
    backup = path.parent / "originals" / "a.jpg"
    assert sorted(os.listdir(backup.parent)) == [".gpxfoto", "a.jpg"]
    assert backup.read_bytes() == original
    assert backup.stat().st_mtime_ns == OLD_TIME_NS
    assert read_tags(path, "GPS:GPSLatitude") == {
        "GPSLatitude": pytest.approx(50.0005, abs=1e-7)}



@needs_exiftool
def test_second_run_keeps_the_first_backup(tmp_path, photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    original = path.read_bytes()
    other = write_gpx(tmp_path / "other.gpx", [
        ("2024-05-01T10:00:00Z", 51.0, 21.0, 300.0),
        ("2024-05-01T10:01:40Z", 51.001, 21.002, 310.0),
    ])

    first = run_cli(path, "-g", gpx, "--write", "--backup")
    second = run_cli(path, "-g", other, "--write", "--backup", "--overwrite")

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert (path.parent / "originals" / "a.jpg").read_bytes() == original
    assert read_tags(path, "GPS:GPSLatitude") == {
        "GPSLatitude": pytest.approx(51.0005, abs=1e-7)}


@needs_exiftool
def test_recursive_run_leaves_backups_alone(photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    original = path.read_bytes()

    first = run_cli(path.parent, "-g", gpx, "--write", "--backup")
    second = run_cli(path.parent, "-g", gpx, "--write", "--backup", "--overwrite", "-r")

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    # The backup would be a second photo without a location
    assert second.stdout.splitlines()[-3] == "Matched: 1, skipped: 0"
    assert sorted(os.listdir(path.parent / "originals")) == [".gpxfoto", "a.jpg"]
    assert (path.parent / "originals" / "a.jpg").read_bytes() == original


@needs_exiftool
def test_photo_given_twice_is_written_once(photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    original = path.read_bytes()

    result = run_cli(path.parent, path, "-g", gpx, "--write", "--backup")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", *written(1, 0)]
    assert (path.parent / "originals" / "a.jpg").read_bytes() == original


@needs_exiftool
def test_overwrite_with_track_without_elevation_drops_old_elevation(tmp_path, photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    flat = write_gpx(tmp_path / "flat.gpx", [(t, lat + 1, lon + 1, None) for t, lat, lon, _ in TRACK])

    first = run_cli(path, "-g", gpx, "--write")
    second = run_cli(path, "-g", flat, "--write", "--overwrite")

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    tags = read_tags(path, "GPS:all")
    assert "GPSAltitude" not in tags and "GPSAltitudeRef" not in tags
    assert tags["GPSLatitude"] == pytest.approx(51.0005, abs=1e-7)


@needs_exiftool
def test_file_name_that_is_not_utf8(photo, gpx):
    source = photo("a.jpg", taken("12:00:50"))
    path = latin2_name(source.parent)
    os.rename(source, path)

    result = run_cli(source.parent, "-g", gpx, "--write")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1] == (
        "  zdj\ufffdcie.jpg      12:00:50  50.000500, 20.001000    205 m")
    assert read_tags(path, "GPS:GPSLatitude") == {
        "GPSLatitude": pytest.approx(50.0005, abs=1e-7)}


@needs_exiftool
def test_compass_direction_is_kept_on_a_photo_without_location(photo, gpx):
    path = photo("a.jpg", [*taken("12:00:50"), "-GPSImgDirection=123.4", "-GPSImgDirectionRef=M"])

    result = run_cli(path, "-g", gpx, "--write")

    assert result.returncode == 0, result.stderr
    tags = read_tags(path, "GPS:all")
    assert tags["GPSImgDirection"] == 123.4
    assert tags["GPSLatitude"] == pytest.approx(50.0005, abs=1e-7)


@needs_exiftool
def test_overwrite_in_the_southern_hemisphere_leaves_no_wrong_xmp(tmp_path, photo):
    gpx = write_gpx(tmp_path / "rio.gpx", [("2024-05-01T08:00:00Z", -22.95, -43.21, 10.0),
                                           ("2024-05-01T08:01:40Z", -22.951, -43.211, 12.0)])
    path = photo("a.jpg", [*taken("05:00:50", "-03:00"), "-GPSLatitude=1", "-GPSLatitudeRef=N",
                           "-GPSLongitude=1", "-GPSLongitudeRef=E", "-XMP-exif:GPSLatitude=1",
                           "-XMP-exif:GPSLongitude=1"])

    result = run_cli(path, "-g", gpx, "--write", "--overwrite")

    assert result.returncode == 0, result.stderr
    assert read_tags(path, "XMP-exif:all") == {}
    assert read_tags(path, "Composite:GPSLatitude", "Composite:GPSLongitude") == {
        "GPSLatitude": pytest.approx(-22.9505, abs=1e-7),
        "GPSLongitude": pytest.approx(-43.2105, abs=1e-7)}


@needs_exiftool
def test_names_exiftool_shows_differently_do_not_hide_other_photos(photo, gpx):
    """exiftool shows U+FFFF as "???"; that must not drop the photos after it."""
    for name in ["a.jpg", "b\uffff.jpg", "c.jpg", "d.jpg"]:
        photo(name, taken("12:00:50"))

    result = run_cli(photo("e.jpg", taken("12:00:50")).parent, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2] == "Matched: 5, skipped: 0"


@needs_exiftool
def test_photo_exiftool_cannot_read_is_skipped_with_a_reason(photo, gpx):
    path = photo("a.jpg", taken("12:00:50"))
    (path.parent / "empty.jpg").write_bytes(b"")

    result = run_cli(path.parent, "-g", gpx)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1:4] == [
        MATCH_LINE, "  empty.jpg        skipped: cannot be read: File is empty",
        "Matched: 1, skipped: 1"]


@needs_exiftool
def test_reused_file_name_does_not_lose_the_backup(photo, gpx):
    """Camera file numbers repeat; the backup of another photo is no backup."""
    path = photo("IMG_0001.jpg", taken("12:00:50"))
    first = run_cli(path, "-g", gpx, "--write", "--backup")
    path.write_bytes(make_jpeg(quantization=range(3, 67)))
    set_tags(path, *taken("12:00:50"))
    other = path.read_bytes()

    second = run_cli(path, "-g", gpx, "--write", "--backup")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 1
    backup = path.parent / "originals" / "IMG_0001.jpg"
    assert second.stdout.splitlines()[-2:] == [
        f"  Could not write IMG_0001.jpg: “{backup}” already exists and is not a copy of "
        "this photo (file unchanged)", "Written: 0, errors: 1"]
    assert path.read_bytes() == other


@needs_exiftool
def test_access_time_is_kept_end_to_end(photo, gpx):
    """exiftool reads the photo before it is written; the access time from
    before that is kept (relatime updates an access time older than mtime)."""
    path = photo("a.jpg", taken("12:00:50"))
    atime = OLD_TIME_NS - 86400 * 10**9
    os.utime(path, ns=(atime, OLD_TIME_NS))

    result = run_cli(path, "-g", gpx, "--write", "--backup")

    assert result.returncode == 0, result.stderr
    for f in (path, path.parent / "originals" / "a.jpg"):
        assert (f.stat().st_atime_ns, f.stat().st_mtime_ns) == (atime, OLD_TIME_NS)

@needs_exiftool
@needs_posix_shell
def test_failed_write_is_reported_and_exits_with_status_1(tmp_path, photo, gpx):
    bad = photo("bad.jpg", taken("12:00:50"))
    good = photo("good.jpg", taken("12:00:50"))
    original = bad.read_bytes()
    # Reading works; writing fails for bad.jpg, the last argument of the write command
    env = fake_exiftool(tmp_path / "bin", f"""\
[ "$1" = -json ] && exec {shutil.which("exiftool")} "$@"
for last; do :; done
case "$last" in *bad.jpg) echo "simulated failure" >&2; exit 1 ;; esac
exec {shutil.which("exiftool")} "$@"
""")

    result = run_cli(bad.parent, "-g", gpx, "--write", env=env)

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        TRACK_LINE,
        "  bad.jpg          12:00:50  50.000500, 20.001000    205 m",
        "  good.jpg         12:00:50  50.000500, 20.001000    205 m",
        "Matched: 2, skipped: 0",
        "  Could not write bad.jpg: simulated failure (file unchanged)",
        *written(1, 1),
    ]
    assert bad.read_bytes() == original
    assert read_tags(good, "GPS:GPSLatitude") == {
        "GPSLatitude": pytest.approx(50.0005, abs=1e-7)}
    assert sorted(os.listdir(bad.parent)) == ["bad.jpg", "good.jpg"]


@needs_exiftool
def test_damaged_jpeg_and_unusable_backup_directory_are_reported(photo, gpx):
    blocked = photo("blocked/a.jpg", taken("12:00:50"))
    (blocked.parent / "originals").write_text("a file where the backup directory should be")
    good = photo("b.jpg", taken("12:00:50"))
    damaged = photo("damaged.jpg", taken("12:00:50"))
    # exiftool still reads the metadata, but the image data is cut off
    data = damaged.read_bytes()
    damaged.write_bytes(data[:data.index(b"\xff\xda")])
    originals = {f: f.read_bytes() for f in (blocked, damaged)}
    matched = "12:00:50  50.000500, 20.001000    205 m"

    result = run_cli(blocked, good.parent, "-g", gpx, "--write", "--backup")

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        TRACK_LINE,
        f"  a.jpg            {matched}",
        f"  b.jpg            {matched}",
        f"  damaged.jpg      {matched}",
        "Matched: 3, skipped: 0",
        f"  Could not write a.jpg: “{blocked.parent / 'originals'}” already exists and is not "
        "a backup directory of gpxfoto (file unchanged)",
        "  Could not write damaged.jpg: damaged JPEG structure (file unchanged)",
        *written(1, 2),
    ]
    for f, content in originals.items():
        assert f.read_bytes() == content
    assert sorted(os.listdir(blocked.parent)) == ["a.jpg", "originals"]
    assert sorted(os.listdir(good.parent)) == ["b.jpg", "blocked", "damaged.jpg", "originals"]
    assert sorted(os.listdir(good.parent / "originals")) == [".gpxfoto", "b.jpg"]
    assert read_tags(good, "GPS:GPSLatitude") == {
        "GPSLatitude": pytest.approx(50.0005, abs=1e-7)}


# Error exits

@needs_exiftool
def test_missing_photo_path(tmp_path, gpx):
    missing = tmp_path / "missing.jpg"

    result = run_cli(missing, "-g", gpx)

    assert result.returncode == 1
    assert result.stdout == TRACK_LINE + "\n"
    assert result.stderr == f"No such file or directory: {missing}\n"


@needs_exiftool
@pytest.mark.parametrize("count, message", [
    (1, "The GPX file contains no track points with timestamps."),
    (2, "The GPX files contain no track points with timestamps."),
])
def test_gpx_without_timed_points(tmp_path, jpeg_file, count, message):
    gpx_args = []
    for i in range(count):
        gpx_args += ["-g", write_gpx(tmp_path / f"{i}.gpx", [(None, 50.0, 20.0, 200.0)])]

    result = run_cli(jpeg_file(), *gpx_args)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == message + "\n"



@needs_exiftool
@pytest.mark.parametrize("content, error", [
    (None, "No such file or directory"),
    ("<gpx>", "no element found: line 1, column 5"),
])
def test_unreadable_gpx_file(tmp_path, jpeg_file, content, error):
    path = tmp_path / "track.gpx"
    if content is not None:
        path.write_text(content)

    result = run_cli(jpeg_file(), "-g", path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"Cannot read the GPX file {path}: {error}\n"

@needs_exiftool
def test_directory_without_jpegs(tmp_path, gpx):
    directory = tmp_path / "empty"
    directory.mkdir()
    (directory / "notes.txt").write_text("not a photo")
    (directory / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = run_cli(directory, "-g", gpx)

    assert result.returncode == 1
    assert result.stdout == TRACK_LINE + "\n"
    assert result.stderr == "No JPEG photos found.\n"


def test_exiftool_not_on_path(tmp_path, gpx, jpeg_file):
    empty = tmp_path / "bin"
    empty.mkdir()

    result = run_cli(jpeg_file(), "-g", gpx, env={"PATH": str(empty)})

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == ("exiftool is not installed. On Fedora, install it with: "
                             "sudo dnf install perl-Image-ExifTool\n")


@needs_posix_shell
def test_exiftool_that_does_not_work(tmp_path, gpx, jpeg_file):
    env = fake_exiftool(tmp_path / "bin", 'echo "cannot read files" >&2\nexit 1\n')

    result = run_cli(jpeg_file(), "-g", gpx, env=env)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "exiftool does not work:\ncannot read files\n\n"


@pytest.mark.parametrize("args, message", [
    ((), "the following arguments are required: PHOTO, -g/--gpx"),
    (("a.jpg",), "the following arguments are required: -g/--gpx"),
    (("-g", "t.gpx"), "the following arguments are required: PHOTO"),
    (("a.jpg", "-g"), "argument -g/--gpx: expected one argument"),
    (("a.jpg", "-g", "t.gpx", "--offset", "abc"),
     "argument --offset: not a valid number of seconds: abc"),
    (("a.jpg", "-g", "t.gpx", "--offset", "nan"),
     "argument --offset: not a valid number of seconds: nan"),
    (("a.jpg", "-g", "t.gpx", "--offset=-inf"),
     "argument --offset: not a valid number of seconds: -inf"),
    (("a.jpg", "-g", "t.gpx", "--offset", "1e300"),
     "argument --offset: not a valid number of seconds: 1e300"),
    (("a.jpg", "-g", "t.gpx", "--max-gap", "1m"),
     "argument --max-gap: not a valid number of seconds: 1m"),
    (("a.jpg", "-g", "t.gpx", "--max-gap", "NaN"),
     "argument --max-gap: not a valid number of seconds: NaN"),
    (("a.jpg", "-g", "t.gpx", "--max-gap", "inf"),
     "argument --max-gap: not a valid number of seconds: inf"),
    (("a.jpg", "-g", "t.gpx", "--max-gap", "-5"), "argument --max-gap: must not be negative: -5"),
    (("a.jpg", "-g", "t.gpx", "--bogus"), "unrecognized arguments: --bogus"),
])
def test_usage_errors_exit_with_status_2(tmp_path, args, message):
    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("usage: gpxfoto ")
    assert result.stderr.splitlines()[-1] == "gpxfoto: error: " + message


def test_help(tmp_path):
    result = run_cli("--help", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.startswith("usage: gpxfoto ")
    assert "Adds locations from GPX tracks to photos without changing the image data." in result.stdout
    assert "--max-gap SECONDS" in result.stdout
    assert "(default: 120 s)" in result.stdout
    assert "“originals” subdirectory next to each photo" in result.stdout
    assert "[--overwrite] [--travel-direction] [--backup]" in " ".join(result.stdout.split())


# Regional settings

# Locales with a decimal comma come first
NUMERIC_LOCALES = ("pl_PL.UTF-8", "de_DE.UTF-8", "fr_FR.UTF-8", "ru_RU.UTF-8", "en_US.UTF-8",
                   "en_GB.UTF-8")


@pytest.fixture
def numeric_locale():
    """(name, decimal point, thousands separator) of an installed locale that groups digits."""
    saved = locale.setlocale(locale.LC_NUMERIC)
    try:
        for name in NUMERIC_LOCALES:
            try:
                locale.setlocale(locale.LC_NUMERIC, name)
            except locale.Error:
                continue
            conv = locale.localeconv()
            if conv["thousands_sep"] and conv["grouping"][:1] == [3]:
                return name, conv["decimal_point"], conv["thousands_sep"]
    finally:
        locale.setlocale(locale.LC_NUMERIC, saved)
    pytest.skip("no locale that groups digits is installed")


@needs_exiftool
def test_numbers_follow_the_system_locale(tmp_path, photo, numeric_locale):
    name, point, separator = numeric_locale
    between = "; " if point == "," else ", "
    # 1000 points one second apart, climbing from 1200 m
    gpx = write_gpx(tmp_path / "long.gpx", [
        (f"2024-05-01T10:{i // 60:02d}:{i % 60:02d}Z", 50.0 + i / 10000, 20.0, 1200 + i)
        for i in range(1000)])
    path = photo("a.jpg", taken("12:00:05"))

    # Only the number format changes; dates keep the C locale's format
    result = run_cli(path, "-g", gpx, env={"LC_ALL": "", "LANG": "C.UTF-8", "LC_NUMERIC": name})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"Track long.gpx: 1{separator}000 points, 05/01/24 12:00:00 – 05/01/24 12:16:39 "
        "(this computer’s time zone)",
        f"  a.jpg            12:00:05  50{point}000500{between}20{point}000000  1{separator}205 m",
        "Matched: 1, skipped: 0",
        PREVIEW_LINE,
    ]


def test_count_during_stops_follows_the_locale(tmp_path, monkeypatch, capsys):
    gpx = write_gpx(tmp_path / "stop.gpx", stop_track())
    metadata = [{"SourceFile": f"{i}.jpg", "DateTimeOriginal": "2024:05:01 12:03:30",
                 "OffsetTimeOriginal": "+02:00"} for i in range(2000)]
    monkeypatch.setenv("LANGUAGE", "C")
    monkeypatch.setattr(i18n, "setup", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: "/usr/bin/" + name)
    monkeypatch.setattr(cli, "find_photos", lambda paths, recursive: [
        m["SourceFile"] for m in metadata])
    monkeypatch.setattr(cli, "read_metadata", lambda files: metadata)
    monkeypatch.setattr(locale, "localeconv", lambda: {
        "decimal_point": ",", "thousands_sep": ".", "grouping": [3, 0]})
    monkeypatch.setattr(sys, "argv", ["gpxfoto", "photos", "-g", str(gpx)])

    cli.main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[-3:-1] == ["Matched: 2.000, skipped: 0", "During stops: 2.000 of 2.000 matched photos"]


def test_summary_counts_follow_the_locale(tmp_path, monkeypatch, capsys):
    """Thousands of photos are too slow end to end, so main() runs in-process
    with exiftool's part of the engine replaced and a locale that groups digits."""
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    metadata = [{"SourceFile": f"{i}.jpg", "DateTimeOriginal": "2024:05:01 12:00:50",
                 "OffsetTimeOriginal": "+02:00"} for i in range(2000)]
    metadata += [{"SourceFile": f"no-date-{i}.jpg"} for i in range(1000)]

    def write_location(path, *args, **kwargs):
        i = int(path[:-4])
        if i % 2:
            raise (RuntimeError, ValueError, OSError)[i % 3](f"failure {i}")

    monkeypatch.setenv("LANGUAGE", "C")
    monkeypatch.setattr(i18n, "setup", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: "/usr/bin/" + name)
    monkeypatch.setattr(cli, "find_photos", lambda paths, recursive: [
        m["SourceFile"] for m in metadata])
    monkeypatch.setattr(cli, "read_metadata", lambda files: metadata)
    monkeypatch.setattr(cli, "write_location", write_location)
    monkeypatch.setattr(locale, "localeconv", lambda: {
        "decimal_point": ",", "thousands_sep": ".", "grouping": [3, 0]})
    monkeypatch.setattr(sys, "argv", ["gpxfoto", "photos", "-g", str(gpx), "--write"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    lines = capsys.readouterr().out.splitlines()
    # Track line, 3000 photos, matched/skipped, 1000 errors, written/errors, check
    assert len(lines) == 4004
    assert lines[1] == "  0.jpg            12:00:50  50,000500; 20,001000    205 m"
    assert lines[2001] == "  no-date-0.jpg    skipped: no capture time in EXIF"
    assert lines[3001:3005] == [
        "Matched: 2.000, skipped: 1.000",
        "  Could not write 1.jpg: failure 1 (file unchanged)",
        "  Could not write 3.jpg: failure 3 (file unchanged)",
        "  Could not write 5.jpg: failure 5 (file unchanged)",
    ]
    assert lines[-2:] == written("1.000", "1.000")


# --- warnings about a suspicious match ---------------------------------
# The hike of test_checks.py: 200 min walking east at 1.2 m/s from T0
# (12:00 at UTC+02:00) with 2-min stops 20, 35, 54, 76, 118 and 161 min in

@pytest.fixture(scope="module")
def hike_points():
    return stops_hike()


@pytest.fixture
def hike(tmp_path, hike_points):
    return hike_gpx(tmp_path / "hike.gpx", hike_points)


def warnings(output):
    """The warnings and their explanations, from the first warning to the preview line."""
    lines = output.splitlines()
    first = next((i for i, line in enumerate(lines) if line.startswith("Warning:")), len(lines))
    return [line for line in lines[first:] if line != PREVIEW_LINE]


SHIFT_WARNING = ("Warning: with the photo times shifted by +1 h, clearly more photos fall during "
                 "stops: 12 of 12 instead of 0.")
ONE_HOUR = ("  A difference of exactly one hour usually means that the camera was not switched "
            "to or from summer time, or that its time zone is set wrong.")


@needs_exiftool
def test_clock_an_hour_behind_gets_a_shift_and_the_options_that_apply_it(photo_series, hike):
    # The camera showed 11:20 when the watch showed 12:20
    photo_series(at_stops(-3600))
    result = run_cli("photos", "-g", hike)
    assert result.returncode == 0, result.stderr
    assert "During stops: 0 of 6 matched photos" in result.stdout.splitlines()
    assert warnings(result.stdout) == [
        SHIFT_WARNING, ONE_HOUR,
        "  To apply this correction, run again with --offset=3600 or --timezone=+01:00."]
    assert result.stdout.splitlines()[-1] == PREVIEW_LINE
    for option in ("--offset=3600", "--timezone=+01:00"):
        result = run_cli("photos", "-g", hike, option)
        assert "During stops: 12 of 12 matched photos" in result.stdout.splitlines()
        assert warnings(result.stdout) == []


@needs_exiftool
def test_shift_with_a_clock_photo_moves_the_clock_s_utc_offset(photo_series, hike, jpeg_file):
    # The time on the clock was read without its UTC offset and taken for
    # the camera's: the clock photo gives +10 s instead of +1 h 10 s
    clock = jpeg_file("clock.jpg")
    set_tags(clock, *taken("12:00:00", date="2026:06:01"))
    photo_series(at_stops(-3610))
    result = run_cli("photos", "-g", hike, "--clock-photo", clock, "--clock-time", "12:00:10")
    assert warnings(result.stdout) == [
        SHIFT_WARNING, ONE_HOUR,
        "  To apply this correction, run again with --clock-time=12:00:10+01:00."]
    result = run_cli("photos", "-g", hike, "--clock-photo", clock,
                     "--clock-time=12:00:10+01:00")
    assert "Clock correction: +1 h 0 min 10 s (equivalent to --offset=3610)" in result.stdout
    assert warnings(result.stdout) == []


@needs_exiftool
def test_warnings_only_in_the_preview(photo_series, hike):
    photo_series(at_stops(-3600))
    result = run_cli("photos", "-g", hike, "--write")
    assert result.returncode == 0, result.stderr
    assert warnings(result.stdout) == []
    assert result.stdout.splitlines()[-2:] == written(6, 0)


@needs_exiftool
def test_no_shift_from_photos_placed_by_two_tracks(photo_series, tmp_path, hike_points):
    # One more photo, 5 h after the start, falls into a short track of its
    # own; the stops of the hike say nothing about that photo's clock
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    hike = hike_gpx(tracks / "hike.gpx", hike_points)
    later = T0 + 5 * 3600
    hike_gpx(tracks / "later.gpx", [(later + k, 49.3, 20.0, 500.0) for k in range(600)])
    photo_series(at_stops(-3600) + [later + 300])
    result = run_cli("photos", "-g", tracks)
    assert "Tracks covering the photos: 2 of 2 GPX files" in result.stdout.splitlines()
    assert warnings(result.stdout) == []
    result = run_cli("photos", "-g", hike)
    assert warnings(result.stdout)[0] == SHIFT_WARNING


@needs_exiftool
def test_shift_when_photos_are_skipped_for_a_track_that_cannot_be_read(
        photo_series, tmp_path, hike_points):
    # The hike is split in two files and the second one is cut off: the
    # photos in its time are skipped, as they would be without it, and the
    # shift is still found on the first one
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    split = T0 + 130 * 60
    hike_gpx(tracks / "a.gpx", [p for p in hike_points if p[0] < split])
    broken = hike_gpx(tracks / "b.gpx", [p for p in hike_points if p[0] >= split])
    broken.write_text(broken.read_text()[:-30])
    photo_series(at_stops(3600))
    result = run_cli("photos", "-g", tracks)
    assert result.returncode == 1
    assert "skipped: the track b.gpx cannot be read" in result.stdout
    assert warnings(result.stdout)[0].startswith(
        "Warning: with the photo times shifted by -1 h, clearly more photos fall during stops")


@needs_exiftool
def test_no_shift_without_stops(photo_series, hike):
    photo_series(at_stops(-3600))
    assert warnings(run_cli("photos", "-g", hike, "--no-stops").stdout) == []


@needs_exiftool
def test_no_timezone_proposed_for_photos_whose_camera_records_utc(photo_series, hike):
    # With --timezone, the S5II's own UTC time would no longer match
    times = at_stops(-3600)
    photo_series(times)
    utc = datetime.fromtimestamp(times[0], timezone.utc)
    set_panasonic_time_stamp("photos/p01.jpg", f"{utc:%Y:%m:%d %H:%M:%S}")
    lines = warnings(run_cli("photos", "-g", hike).stdout)
    assert lines == [SHIFT_WARNING, ONE_HOUR,
                     "  To apply this correction, run again with --offset=3600."]


@needs_exiftool
def test_shift_in_a_directory_of_tracks_that_cover_no_photo(photo_series, tmp_path, hike_points):
    # Five hours off: no track covers the photos, but the nearest one,
    # loaded to name it, shows the shift
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    hike_gpx(tracks / "hike.gpx", hike_points)
    photo_series(at_stops(-5 * 3600))
    result = run_cli("photos", "-g", tracks)
    assert "Tracks covering the photos: 0 of 1 GPX file" in result.stdout.splitlines()
    assert warnings(result.stdout)[0] == (
        "Warning: with the photo times shifted by +5 h, clearly more photos fall during stops: "
        "12 of 12 instead of 0.")
    assert warnings(result.stdout)[-1] == (
        "  To apply this correction, run again with --offset=18000 or --timezone=-03:00.")


@needs_exiftool
def test_shift_next_to_a_track_that_cannot_be_read(
        photo_series, tmp_path, hike_points):
    # Next to the hike, a file that cannot be read covers its second half;
    # it is skipped, so the counts of the hike alone hold
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    hike_gpx(tracks / "a.gpx", hike_points)
    broken = hike_gpx(tracks / "b.gpx", [p for p in hike_points if p[0] >= T0 + 100 * 60])
    broken.write_text(broken.read_text()[:-30])
    photo_series(at_stops(-3600))
    result = run_cli("photos", "-g", tracks)
    assert result.returncode == 1
    assert warnings(result.stdout)[0] == SHIFT_WARNING
    applied = run_cli("photos", "-g", tracks, "--offset=3600").stdout.splitlines()
    assert "During stops: 12 of 12 matched photos" in applied


def shift_shots(zones=(7200,), count=12):
    zones = [zones[k % len(zones)] for k in range(count)]
    return [Shot(T0 + 60 * k, T0 + 60 * k + zone, 49.2, 20.0) for k, zone in enumerate(zones)]


def shift_advice(hint, shots, correction=0.0, clock=None):
    return cli.shift_lines(hint, shots, correction, clock)[1:]


WHOLE_HOURS = ("  A difference of whole hours or of half an hour usually means that the time "
               "zone set in the camera is wrong, for example still the home one while travelling.")


@pytest.mark.parametrize("shift, zones, correction, advice", [
    (-1800, (7200,), 0.0, "--offset=-1800 or --timezone=+02:30"),
    (7200, (7200,), 0.0, "--offset=7200 or --timezone=+00:00"),
    (3 * 3600, (-5 * 3600,), 0.0, "--offset=10800 or --timezone=-08:00"),
    # A correction already given: --timezone would drop it
    (3600, (7200,), 131.5, "--offset=3731.5"),
    (3600, (7200,), -3600.0, "--offset=0"),
    # Photos from different time zones
    (3600, (7200, 3600), 0.0, "--offset=3600"),
    # No time zone is 15 h ahead of UTC
    (-3600, (14 * 3600,), 0.0, "--offset=-3600"),
])
def test_options_that_apply_a_shift(shift, zones, correction, advice):
    lines = shift_advice(ShiftHint(shift, 12, 12, 6, 0), shift_shots(zones), correction)
    assert lines[0] == (ONE_HOUR if abs(shift) == 3600 else WHOLE_HOURS)
    assert lines[1] == f"  To apply this correction, run again with {advice}."


CAMERA_TIME = datetime(2026, 6, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))


@pytest.mark.parametrize("reading, shift, option", [
    ("12:00:10", 3600, "--clock-time=12:00:10+01:00"),
    ("12:00", -1800, "--clock-time=12:00+02:30"),
    ("2026-06-01T12:00:10+02:00", 3600, "--clock-time=2026-06-01T12:00:10+01:00"),
    # Beyond two hours, the date is needed
    ("12:00:10", 3 * 3600, "--clock-time=2026-06-01T12:00:10-01:00"),
])
def test_shift_with_a_clock_photo(reading, shift, option):
    clock = correction_from(CAMERA_TIME, parse_reading(reading))
    lines = shift_advice(ShiftHint(shift, 12, 12, 6, 0), shift_shots(), clock.seconds, clock)
    assert lines[1] == f"  To apply this correction, run again with {option}."


def test_shift_with_a_clock_photo_whose_offset_cannot_move():
    # The clock was 14 h ahead of UTC; 15 h is no time zone, so --offset
    # has to take the place of the clock options
    clock = correction_from(CAMERA_TIME, parse_reading("23:30:00+14:00"))
    lines = shift_advice(ShiftHint(-3600, 12, 12, 6, 0), shift_shots(), clock.seconds, clock)
    offset = cli.offset_value(clock.seconds - 3600)
    assert lines[1] == (f"  To apply this correction, run again with --offset={offset} instead of "
                        "--clock-photo and --clock-time.")


def test_no_timezone_proposed_when_it_is_not_wanted():
    lines = cli.shift_lines(ShiftHint(3600, 12, 12, 6, 0), shift_shots(), 0.0, None,
                            zone_option=False)
    assert lines[2] == "  To apply this correction, run again with --offset=3600."


MOTION_ADVICE = ("  Photos are usually taken at stops or while slowing down. Check the camera "
                 "clock, for example with a photo of the watch that records the track and the "
                 "options --clock-photo and --clock-time.")


@needs_exiftool
def test_photos_taken_while_walking_get_a_warning(photo_series, tmp_path):
    # Walking at 1.2 m/s with a 20 s pause every 7.5 min; a photo in the
    # middle of 20 pauses, by a camera clock 90 s behind: all of them land
    # while walking
    walk = hike_gpx(tmp_path / "walk.gpx", pauses_hike())
    photo_series(in_pauses(20, -90))
    result = run_cli("photos", "-g", walk)
    assert warnings(result.stdout) == [
        "Warning: the camera clock may be off. 20 of 20 matched photos were taken while the "
        "track shows movement at full pace.", MOTION_ADVICE]
    assert warnings(run_cli("photos", "-g", walk, "--offset=90").stdout) == []


def test_motion_warning_with_a_clock_photo():
    clock = correction_from(CAMERA_TIME, parse_reading("12:00:10"))
    assert cli.motion_lines(Motion(1, 1, 1, 1), None) == [
        "Warning: the camera clock may be off. 1 of 1 matched photo was taken while the track "
        "shows movement at full pace.", MOTION_ADVICE]
    assert cli.motion_lines(Motion(1, 1, 1, 1), clock)[1] == (
        "  Photos are usually taken at stops or while slowing down. Check the time on the clock "
        "given with --clock-time, and its UTC offset.")


JUMP_WARNING = "Warning: photos taken less than a minute apart are placed implausibly far apart:"
JUMP_ADVICE = ("  Check the time zones of these photos, and whether the GPX files record different "
               "trips at the same time.")


@needs_exiftool
@pytest.mark.parametrize("options", [(), ("--no-stops",)])
def test_photos_with_different_time_zones_jump(photo_series, tmp_path, options):
    # Walking at 1.2 m/s. p02.jpg was taken 4 s after p01.jpg, but its EXIF
    # time zone is an hour behind, so it is placed an hour further on
    walk = hike_gpx(tmp_path / "walk.gpx", Hike(seed=9).walk(7200, east=1.2).points)
    photo_series([T0 + 1000, T0 + 1004 + 3600], zone=["+02:00", "+01:00"])
    result = run_cli("photos", "-g", walk, *options)
    assert warnings(result.stdout) == [
        JUMP_WARNING,
        "  p01.jpg and p02.jpg: taken 4 s apart, placed 4.3 km apart, time zones UTC+02:00 and "
        "UTC+01:00", JUMP_ADVICE]


@needs_exiftool
def test_two_tracks_recorded_at_the_same_time_jump(photo_series, tmp_path):
    # Two GPX files given together cover the same hour 5 km apart, one at
    # even and one at odd seconds, so the track zigzags between them
    a = hike_gpx(tmp_path / "a.gpx", [p for p in Hike(seed=12).walk(3600, east=1.2).points
                                      if p[0] % 2 == 0])
    b = hike_gpx(tmp_path / "b.gpx", [(t, lat, lon + 0.07, ele) for t, lat, lon, ele
                                      in Hike(seed=13).walk(3600, east=1.2).points if t % 2])
    photo_series([T0 + 600.5 + 7 * k for k in range(8)])
    lines = warnings(run_cli("photos", "-g", a, "-g", b).stdout)
    assert lines == [JUMP_WARNING,
                     "  p01.jpg and p02.jpg: taken 7 s apart, placed 5.1 km apart",
                     "  p02.jpg and p03.jpg: taken 7 s apart, placed 5.1 km apart",
                     "  p03.jpg and p04.jpg: taken 7 s apart, placed 5.1 km apart",
                     "  and 4 more pairs", JUMP_ADVICE]


def jump_shots(count, zone=7200):
    return [Shot(T0 + k, T0 + k + zone, *position(5000 * (k % 2), 0)) for k in range(count)]


@pytest.mark.parametrize("count, more", [(3, []), (4, ["  and 1 more pair"]),
                                         (6, ["  and 3 more pairs"])])
def test_jump_warning_names_three_pairs(count, more):
    shots = jump_shots(count + 1)
    names = [f"p{k}.jpg" for k in range(count + 1)]
    jumps = [Jump(k, k + 1, 1.0, 5000.0) for k in range(count)]
    assert cli.jump_lines(jumps, shots, names) == [
        JUMP_WARNING,
        *(f"  p{k}.jpg and p{k + 1}.jpg: taken 1 s apart, placed 5.0 km apart"
          for k in range(min(count, 3))),
        *more, JUMP_ADVICE]


# --- direction of travel -------------------------------------------------
# TRACK recorded once a second: the same line, so the same positions

def dense(first, last, seconds=100):
    (t0, lat0, lon0, ele0), (_, lat1, lon1, ele1) = first, last
    start = datetime.fromisoformat(t0.replace("Z", "+00:00"))
    return [(f"{start + timedelta(seconds=k):%Y-%m-%dT%H:%M:%SZ}",
             lat0 + (lat1 - lat0) * k / seconds, lon0 + (lon1 - lon0) * k / seconds,
             None if ele0 is None else ele0 + (ele1 - ele0) * k / seconds)
            for k in range(seconds + 1)]


@pytest.fixture
def dense_gpx(tmp_path):
    return write_gpx(tmp_path / "track.gpx", dense(*TRACK))


DENSE_TRACK_LINE = TRACK_LINE.replace("2 points", "101 points")

@needs_exiftool
def test_travel_direction_is_shown_and_written(photo, dense_gpx):
    gpx = dense_gpx
    a = photo("a.jpg", taken("12:00:50"))
    # An old direction of travel, from another program
    b = photo("b.jpg", [*taken("12:00:05"), "-GPSTrack=200", "-GPSTrackRef=M"])
    checksums = {a: image_checksum(a), b: image_checksum(b)}

    preview = run_cli(a.parent, "-g", gpx, "--travel-direction")

    assert preview.returncode == 0, preview.stderr
    assert preview.stdout.splitlines() == [
        DENSE_TRACK_LINE,
        MATCH_LINE + "  direction of travel  52°",
        "  b.jpg            12:00:05  50.000050, 20.000100    200 m"
        "  [no direction of travel: too close to the start or end of the track]",
        "Matched: 2, skipped: 0",
        "With a direction of travel: 1, without: 1",
        PREVIEW_LINE,
    ]
    # Without the option, nothing about directions
    plain = run_cli(a.parent, "-g", gpx)
    assert plain.stdout.splitlines()[1:4] == [
        MATCH_LINE, "  b.jpg            12:00:05  50.000050, 20.000100    200 m",
        "Matched: 2, skipped: 0"]

    saved = run_cli(a.parent, "-g", gpx, "--travel-direction", "--write")

    assert saved.returncode == 0, saved.stderr
    assert saved.stdout.splitlines()[-2:] == written(2, 0)
    assert read_tags(a, "GPSTrack", "GPSTrackRef", "GPSImgDirection") == {
        "GPSTrack": 52, "GPSTrackRef": "T"}
    # The old direction does not pass for one of ours
    assert read_tags(b, "GPSTrack", "GPSTrackRef", "GPSLatitude") == {
        "GPSLatitude": pytest.approx(50.00005, abs=1e-7)}
    for path, checksum in checksums.items():
        assert image_checksum(path) == checksum


@needs_exiftool
def test_without_the_option_an_old_direction_of_travel_stays(photo, dense_gpx):
    gpx = dense_gpx
    b = photo("b.jpg", [*taken("12:00:05"), "-GPSTrack=200", "-GPSTrackRef=M"])
    assert run_cli(b, "-g", gpx, "--write").returncode == 0
    assert read_tags(b, "GPSTrack", "GPSTrackRef") == {"GPSTrack": 200, "GPSTrackRef": "M"}


@needs_exiftool
def test_no_direction_of_travel_at_a_stop(photo, stop_gpx):
    a = photo("a.jpg", taken("12:00:50"))
    photo("b.jpg", taken("12:03:30"))

    preview = run_cli(a.parent, "-g", stop_gpx, "--travel-direction")

    assert preview.stdout.splitlines()[1:5] == [
        WALKING + "  direction of travel  90°",
        AT_STOP[:-1] + "; no direction of travel: taken during a stop]",
        "Matched: 2, skipped: 0",
        "With a direction of travel: 1, without: 1",
    ]
    no_stops = run_cli(a.parent, "-g", stop_gpx, "--travel-direction", "--no-stops")
    assert no_stops.stdout.splitlines()[2] == (
        ON_TRACK + "  [no direction of travel: the track stays within 20 m of this place for "
        "60 s before or after the photo]")


@needs_exiftool
def test_direction_of_travel_after_the_track_files(tmp_path, photo):
    north = write_gpx(tmp_path / "north.gpx", dense(("2024-05-01T10:00:00Z", 50.0, 20.0, None),
                                                    ("2024-05-01T10:01:40Z", 50.001, 20.0, None)))
    west = write_gpx(tmp_path / "west-longer-name.gpx", dense(
        ("2024-05-01T11:00:00Z", 50.1, 20.1, None), ("2024-05-01T11:01:40Z", 50.1, 20.098, None)))
    a = photo("a.jpg", taken("12:00:50"))
    photo("c.jpg", taken("13:00:50"))

    result = run_cli(a.parent, "-g", north, "-g", west, "--travel-direction")

    assert result.stdout.splitlines()[1:3] == [
        "  a.jpg            12:00:50  50.000500, 20.000000        —  north.gpx           "
        "  direction of travel   0°",
        "  c.jpg            13:00:50  50.100000, 20.099000        —  west-longer-name.gpx"
        "  direction of travel 270°",
    ]


@needs_exiftool
def test_a_photo_with_a_location_gets_a_direction_only_with_overwrite(photo, dense_gpx):
    gpx = dense_gpx
    a = photo("a.jpg", [*taken("12:00:50"), "-GPSLatitude=1", "-GPSLatitudeRef=N",
                        "-GPSLongitude=2", "-GPSLongitudeRef=E"])
    kept = run_cli(a, "-g", gpx, "--travel-direction")
    assert kept.stdout.splitlines()[1:3] == [
        "  a.jpg            skipped: already has a location",
        "Matched: 0, skipped: 1"]
    replaced = run_cli(a, "-g", gpx, "--travel-direction", "--overwrite")
    assert replaced.stdout.splitlines()[1] == MATCH_LINE + "  direction of travel  52°"


@needs_exiftool
def test_no_direction_of_travel_from_two_files_recorded_at_the_same_time(tmp_path,
                                                                         photo_series):
    # A watch and a phone 60 m apart record the same walk; given together,
    # the track zigzags between them
    watch = hike_gpx(tmp_path / "watch.gpx", Hike(seed=14).walk(600, east=1.4).points)
    north = position(0.0, 60.0)
    phone = hike_gpx(tmp_path / "phone.gpx",
                     Hike(seed=15, lat=north[0], lon=north[1]).walk(600, east=1.4).points)
    photo_series([T0 + 300, T0 + 310.5])
    lines = run_cli("photos", "-g", watch, "-g", phone, "--travel-direction").stdout.splitlines()
    assert [line.split("  [")[-1] for line in lines[1:3]] == [
        "no direction of travel: the track points around this place come from different "
        "files]"] * 2
    assert lines[4] == "With a direction of travel: 0, without: 2"


@needs_exiftool
def test_no_direction_of_travel_across_a_break_in_recording(photo, gpx):
    # TRACK has two points 100 s apart: the way between them is not known
    a = photo("a.jpg", taken("12:00:50"))
    assert run_cli(a, "-g", gpx, "--travel-direction").stdout.splitlines()[1] == (
        MATCH_LINE + "  [no direction of travel: the track has a break in recording here]")
