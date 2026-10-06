"""End-to-end tests of the command-line tool (python -m gpxfoto)."""
import errno
import locale
import os
import shutil
import stat
import sys

import pytest

from conftest import needs_exiftool, read_tags, run_cli, set_tags, write_gpx
from gpxfoto import cli, i18n
from gpxfoto.engine.writer import image_checksum

# run_cli uses Europe/Warsaw, which is UTC+2 on these dates, and the C
# locale (dates as MM/DD/YY).
TRACK = [
    ("2024-05-01T10:00:00Z", 50.0, 20.0, 200.0),
    ("2024-05-01T10:01:40Z", 50.001, 20.002, 210.0),
]
TRACK_LINE = "Track: 2 points, 05/01/24 12:00:00 – 12:01:40 (local time of this computer)"
PREVIEW_LINE = "This was a preview; nothing was written. Use --write to write the locations."
WRITTEN_LINE = ("Written: {}, errors: {}. "
                "Image data checked in every written file: unchanged.")
# 50 s after the start of TRACK, halfway between its two points
MATCH_LINE = "  a.jpg            12:00:50  50.000500, 20.001000    205 m"

OLD_TIME_NS = 1_600_000_000_000_000_000
GPS_TAGS = ("GPS:GPSLatitude", "GPS:GPSLatitudeRef", "GPS:GPSLongitude", "GPS:GPSLongitudeRef",
            "GPS:GPSAltitude", "GPS:GPSAltitudeRef", "GPS:GPSDateStamp", "GPS:GPSTimeStamp",
            "GPS:GPSMapDatum")

needs_posix_shell = pytest.mark.skipif(os.name != "posix", reason="needs /bin/sh")


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
        "  nodate.jpg       skipped: no capture date in EXIF",
        "  nozone.jpg       12:00:30  50.000300, 20.000600    203 m"
        "  [system time zone (not in EXIF)]",
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
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", WRITTEN_LINE.format(1, 0)]
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
        "Track: 4 points, 05/01/24 12:00:00 – 18:01:40 (local time of this computer)",
        "  chile.jpg        12:00:50  -33.000500, -70.001000    -15 m",
        "  poland.jpg       12:00:50  50.000500, 20.001000    205 m",
        "Matched: 2, skipped: 0",
        WRITTEN_LINE.format(2, 0),
    ]
    assert read_tags(chile, *GPS_TAGS) == gps_written(-33.0005, -70.001, -15, "2024:05:02",
                                                      "16:00:50")
    assert read_tags(poland, *GPS_TAGS) == gps_written(50.0005, 20.001, 205, "2024:05:01",
                                                       "10:00:50")


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
        WRITTEN_LINE.format(0, 0),
    ]
    assert path.read_bytes() == original

    replaced = run_cli(path, "-g", gpx, "--write", "--overwrite")
    assert replaced.returncode == 0, replaced.stderr
    assert replaced.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE, "Matched: 1, skipped: 0", WRITTEN_LINE.format(1, 0)]
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
        TRACK_LINE,
        "  a.jpg            12:00:50  0.000000, 9.001000      0 m",
        "  null-island.jpg  skipped: already has a location",
        "Matched: 1, skipped: 1",
        WRITTEN_LINE.format(1, 0),
    ]
    # The GPS time includes the --offset correction
    assert read_tags(path, *GPS_TAGS) == gps_written(0.0, 9.001, 0.0, "2024:05:01", "10:00:50")
    assert located.read_bytes() == original


@needs_exiftool
@pytest.mark.parametrize("offset, local_time, match_line", [
    ("50", "12:00:00", MATCH_LINE),
    ("-50", "12:01:40", MATCH_LINE),
    ("49.5", "12:00:01", "  a.jpg            12:00:50  50.000505, 20.001010    205 m"),
])
def test_offset_shifts_capture_time(photo, gpx, offset, local_time, match_line):
    path = photo("a.jpg", taken(local_time))

    result = run_cli(path, "-g", gpx, "--offset", offset)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        TRACK_LINE, match_line, "Matched: 1, skipped: 0", PREVIEW_LINE]


@needs_exiftool
def test_timezone_overrides_the_one_from_exif(photo, gpx):
    path = photo("a.jpg", taken("12:00:50", "+05:00"))

    from_exif = run_cli(path, "-g", gpx)
    assert from_exif.returncode == 0, from_exif.stderr
    assert from_exif.stdout.splitlines() == [
        TRACK_LINE,
        "  a.jpg            12:00:50  skipped: before the start of the track by 2 h 59 min",
        "Matched: 0, skipped: 1",
    ]

    manual = run_cli(path, "-g", gpx, "--timezone", "+02:00")
    assert manual.returncode == 0, manual.stderr
    assert manual.stdout.splitlines() == [
        TRACK_LINE, MATCH_LINE + "  [manual time zone]", "Matched: 1, skipped: 0",
        PREVIEW_LINE]


@needs_exiftool
def test_negative_timezone_given_with_equals_sign(photo, gpx):
    path = photo("a.jpg", taken("07:00:50", None))

    result = run_cli(path, "-g", gpx, "--timezone=-03:00")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[1] == (
        "  a.jpg            07:00:50  50.000500, 20.001000    205 m  [manual time zone]")


@needs_exiftool
@pytest.mark.parametrize("value", ["0200", "+2", "+24:00"])
def test_invalid_timezone_exits_with_message(jpeg_file, gpx, value):
    result = run_cli(jpeg_file(), "-g", gpx, "--timezone", value)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "The time zone must be given as +02:00 or -05:00.\n"


@needs_exiftool
def test_max_gap(tmp_path, photo):
    # Points 10 min and about 1.1 km apart, without elevation
    gpx = write_gpx(tmp_path / "gap.gpx", [("2024-05-01T10:00:00Z", 50.0, 20.0, None),
                                           ("2024-05-01T10:10:00Z", 50.01, 20.0, None)])
    path = photo("a.jpg", taken("12:03:00"))
    track_line = "Track: 2 points, 05/01/24 12:00:00 – 12:10:00 (local time of this computer)"

    default = run_cli(path, "-g", gpx)
    assert default.returncode == 0, default.stderr
    assert default.stdout.splitlines() == [
        track_line,
        "  a.jpg            12:03:00  skipped: gap in the track, nearest point 3 min away",
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
        "Track: 1 point, 05/01/24 12:00:00 – 12:00:00 (local time of this computer)",
        "  a.jpg            11:57:59  skipped: before the start of the track by 2 min",
        "  b.jpg            11:58:00  50.000000, 20.000000    200 m",
        "  c.jpg            12:02:00  50.000000, 20.000000    200 m",
        "  d.jpg            12:02:01  skipped: after the end of the track by 2 min",
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
    assert result.stdout.splitlines()[-1] == WRITTEN_LINE.format(1, 0)
    assert sorted(os.listdir(path.parent)) == ["a.jpg", "originals"]
    backup = path.parent / "originals" / "a.jpg"
    assert os.listdir(backup.parent) == ["a.jpg"]
    assert backup.read_bytes() == original
    assert backup.stat().st_mtime_ns == OLD_TIME_NS
    assert read_tags(path, "GPS:GPSLatitude") == {
        "GPSLatitude": pytest.approx(50.0005, abs=1e-7)}


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
        "  ERROR bad.jpg: simulated failure (file unchanged)",
        WRITTEN_LINE.format(1, 1),
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
        f"  ERROR a.jpg: [Errno {errno.EEXIST}] {os.strerror(errno.EEXIST)}: "
        f"'{blocked.parent / 'originals'}' (file unchanged)",
        "  ERROR damaged.jpg: damaged JPEG structure (file unchanged)",
        WRITTEN_LINE.format(1, 2),
    ]
    for f, content in originals.items():
        assert f.read_bytes() == content
    assert sorted(os.listdir(blocked.parent)) == ["a.jpg", "originals"]
    assert sorted(os.listdir(good.parent)) == ["b.jpg", "blocked", "damaged.jpg", "originals"]
    assert os.listdir(good.parent / "originals") == ["b.jpg"]
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
    (1, "The GPX file contains no track points with a time."),
    (2, "The GPX files contain no track points with a time."),
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
def test_exiftool_without_output(tmp_path, gpx, jpeg_file):
    env = fake_exiftool(tmp_path / "bin", 'echo "cannot read files" >&2\nexit 1\n')

    result = run_cli(jpeg_file(), "-g", gpx, env=env)

    assert result.returncode == 1
    assert result.stdout == TRACK_LINE + "\n"
    assert result.stderr == "exiftool returned no data:\ncannot read files\n\n"


@pytest.mark.parametrize("args, message", [
    ((), "the following arguments are required: photos, -g/--gpx"),
    (("a.jpg",), "the following arguments are required: -g/--gpx"),
    (("-g", "t.gpx"), "the following arguments are required: photos"),
    (("a.jpg", "-g"), "argument -g/--gpx: expected one argument"),
    (("a.jpg", "-g", "t.gpx", "--offset", "abc"), "argument --offset: invalid float value: 'abc'"),
    (("a.jpg", "-g", "t.gpx", "--max-gap", "1m"),
     "argument --max-gap: invalid float value: '1m'"),
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
    assert "Adds locations from a GPX track to photos without changing the image." in result.stdout
    assert "--max-gap SECONDS" in result.stdout
    assert "(default: 120 s)" in result.stdout
    assert "“originals” subdirectory" in result.stdout


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
    # 1000 points one second apart, climbing from 1200 m
    gpx = write_gpx(tmp_path / "long.gpx", [
        (f"2024-05-01T10:{i // 60:02d}:{i % 60:02d}Z", 50.0 + i / 10000, 20.0, 1200 + i)
        for i in range(1000)])
    path = photo("a.jpg", taken("12:00:05"))

    # Only the number format changes; dates keep the C locale's format
    result = run_cli(path, "-g", gpx, env={"LC_ALL": "", "LANG": "C.UTF-8", "LC_NUMERIC": name})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"Track: 1{separator}000 points, 05/01/24 12:00:00 – 12:16:39 "
        "(local time of this computer)",
        f"  a.jpg            12:00:05  50{point}000500, 20{point}000000  1{separator}205 m",
        "Matched: 1, skipped: 0",
        PREVIEW_LINE,
    ]


def test_summary_counts_follow_the_locale(tmp_path, monkeypatch, capsys):
    """Thousands of photos are too slow end to end, so main() runs in-process
    with exiftool's part of the engine replaced and a locale that groups digits."""
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    metadata = [{"SourceFile": f"{i}.jpg", "DateTimeOriginal": "2024:05:01 12:00:50",
                 "OffsetTimeOriginal": "+02:00"} for i in range(2000)]
    metadata += [{"SourceFile": f"no-date-{i}.jpg"} for i in range(1000)]

    def write_location(path, *args):
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
    # Track line, 3000 photos, matched/skipped, 1000 errors, written/errors
    assert len(lines) == 4003
    assert lines[1] == "  0.jpg            12:00:50  50,000500, 20,001000    205 m"
    assert lines[2001] == "  no-date-0.jpg    skipped: no capture date in EXIF"
    assert lines[3001:3005] == [
        "Matched: 2.000, skipped: 1.000",
        "  ERROR 1.jpg: failure 1 (file unchanged)",
        "  ERROR 3.jpg: failure 3 (file unchanged)",
        "  ERROR 5.jpg: failure 5 (file unchanged)",
    ]
    assert lines[-1] == WRITTEN_LINE.format("1.000", "1.000")
