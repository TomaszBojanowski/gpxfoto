"""Finding photos, reading their metadata and working out the capture time."""
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import make_jpeg, needs_exiftool, set_tags
from gpxfoto.engine import photos
from gpxfoto.engine.photos import (
    TZ_CAMERA, TZ_MANUAL, TZ_SYSTEM, capture_time, find_photos, parse_utc_offset,
    read_metadata)

# POSIX rules work without the tz database: Central European Time with DST.
CENTRAL_EUROPE = "CET-1CEST,M3.5.0,M10.5.0/3"


def zone(hours, minutes=0):
    return timezone(timedelta(hours=hours, minutes=minutes))


@pytest.fixture
def system_tz():
    """Set the TZ of this process; restored together with time.tzset()."""
    with pytest.MonkeyPatch.context() as patch:
        def set_zone(name):
            patch.setenv("TZ", name)
            time.tzset()
        yield set_zone
    time.tzset()


# parse_utc_offset

@pytest.mark.parametrize("text, expected", [
    ("+02:00", zone(2)),
    ("-05:00", zone(-5)),
    ("+05:45", zone(5, 45)),
    ("-03:30", zone(-3, -30)),
    ("-00:30", zone(0, -30)),
    ("+00:00", timezone.utc),
    ("01:00", zone(1)),
    ("10:30", zone(10, 30)),
    ("+14:00", zone(14)),
    ("-14:00", zone(-14)),
    ("+05:59", zone(5, 59)),
])
def test_parse_utc_offset(text, expected):
    assert parse_utc_offset(text) == expected


@pytest.mark.parametrize("text", [
    "+0200", "Z", "UTC", "+ab:00", "+02:00:00", "+24:00", "-25:00", "",
    # Accepted before, with a wrong result
    "+02:75", "+02:60", "+-05:00", "--02:00", "+2:00", "+05:-30", "+14:01", "-15:00",
    " +02:00", "+02:00 ", "+٠٢:٠٠", "+٠٢:00", "+02.00",
])
def test_parse_utc_offset_rejects_anything_but_an_offset(text):
    with pytest.raises(ValueError):
        parse_utc_offset(text)


# capture_time

def test_time_zone_sources_are_distinct():
    assert len({TZ_CAMERA, TZ_MANUAL, TZ_SYSTEM}) == 3


def test_capture_time_uses_subseconds_and_offset_from_camera():
    meta = {"DateTimeOriginal": "2024:05:01 12:34:56", "SubSecTimeOriginal": "045",
            "OffsetTimeOriginal": "+02:00", "CreateDate": "2024:05:01 10:00:00",
            "OffsetTime": "+03:00"}
    taken, source = capture_time(meta, None)
    assert source == TZ_CAMERA
    assert taken == datetime(2024, 5, 1, 12, 34, 56, 45000, tzinfo=zone(2))
    assert taken.utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize("subsec, microseconds", [
    ("045", 45000),
    ("5", 500000),
    ("123456", 123456),
    ("1234567", 123457),    # rounded to the nearest microsecond
    (" 07 ", 70000),
    (45, 450000),       # exiftool -json gives "45" as a number
    (0, 0),
    ("", 0),
    ("abc", 0),
    ("-5", 0),
])
def test_capture_time_subseconds(subsec, microseconds):
    meta = {"DateTimeOriginal": "2024:05:01 12:34:56", "SubSecTimeOriginal": subsec,
            "OffsetTimeOriginal": "+00:00"}
    taken = capture_time(meta, None)[0]
    assert taken == datetime(2024, 5, 1, 12, 34, 56, microseconds, tzinfo=timezone.utc)


def test_capture_time_ignores_text_after_the_seconds():
    meta = {"DateTimeOriginal": "2024:05:01 12:34:56.78", "OffsetTimeOriginal": "-05:00"}
    taken, source = capture_time(meta, None)
    assert (taken, source) == (datetime(2024, 5, 1, 12, 34, 56, tzinfo=zone(-5)), TZ_CAMERA)


def test_capture_time_falls_back_to_create_date_and_offset_time():
    meta = {"CreateDate": "2023:12:31 23:59:59", "OffsetTime": "-03:30"}
    taken, source = capture_time(meta, None)
    assert source == TZ_CAMERA
    assert taken == datetime(2023, 12, 31, 23, 59, 59, tzinfo=zone(-3, -30))
    assert taken.utcoffset() == timedelta(hours=-3, minutes=-30)


@pytest.mark.parametrize("original", ["", None, "   :  ", "+02:75", 2, "Z"])
def test_capture_time_unusable_offset_time_original_falls_back_to_offset_time(original):
    meta = {"DateTimeOriginal": "2024:05:01 12:00:00", "OffsetTimeOriginal": original,
            "OffsetTime": "+03:00"}
    assert capture_time(meta, None) == (datetime(2024, 5, 1, 12, 0, 0, tzinfo=zone(3)), TZ_CAMERA)


def test_capture_time_prefers_date_time_original_over_create_date():
    meta = {"DateTimeOriginal": "2024:05:01 12:00:00", "CreateDate": "2024:05:01 13:00:00",
            "OffsetTime": "+01:00"}
    taken = capture_time(meta, None)[0]
    assert taken == datetime(2024, 5, 1, 12, 0, 0, tzinfo=zone(1))


def test_capture_time_empty_date_time_original_falls_back_to_create_date():
    meta = {"DateTimeOriginal": "", "CreateDate": "2024:05:01 13:00:00",
            "OffsetTimeOriginal": "+01:00"}
    taken = capture_time(meta, None)[0]
    assert taken == datetime(2024, 5, 1, 13, 0, 0, tzinfo=zone(1))


@pytest.mark.parametrize("offset", ["+02:00", None, "garbage"])
def test_capture_time_manual_time_zone_overrides_exif(offset, system_tz):
    system_tz("UTC0")
    meta = {"DateTimeOriginal": "2024:05:01 12:34:56", "SubSecTimeOriginal": "5"}
    if offset is not None:
        meta["OffsetTimeOriginal"] = offset
    taken, source = capture_time(meta, zone(-5))
    assert source == TZ_MANUAL
    assert taken == datetime(2024, 5, 1, 12, 34, 56, 500000, tzinfo=zone(-5))
    assert taken.utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize("date, hours", [
    ("2024:07:01 12:00:00", 2),
    ("2024:01:15 12:00:00", 1),
])
def test_capture_time_without_offset_uses_system_time_zone(date, hours, system_tz):
    system_tz(CENTRAL_EUROPE)
    taken, source = capture_time({"DateTimeOriginal": date, "SubSecTimeOriginal": "25"}, None)
    assert source == TZ_SYSTEM
    assert taken.utcoffset() == timedelta(hours=hours)
    assert taken.replace(tzinfo=None) == datetime.strptime(date, "%Y:%m:%d %H:%M:%S") \
        + timedelta(milliseconds=250)


@pytest.mark.parametrize("meta", [
    {"OffsetTimeOriginal": "garbage"},
    {"OffsetTimeOriginal": "+0200"},
    {"OffsetTimeOriginal": "+25:00"},
    {"OffsetTimeOriginal": "   :  "},
    {"OffsetTimeOriginal": ""},
    {"OffsetTime": "nonsense"},
    {"OffsetTimeOriginal": 2},      # exiftool -json prints numeric-looking values as numbers
])
def test_capture_time_invalid_offset_falls_back_to_system_time_zone(meta, system_tz):
    system_tz("XYZ-05:30")
    taken, source = capture_time({"DateTimeOriginal": "2024:05:01 12:00:00", **meta}, None)
    assert source == TZ_SYSTEM
    assert taken == datetime(2024, 5, 1, 12, 0, 0, tzinfo=zone(5, 30))
    assert taken.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("meta", [
    {},
    {"DateTimeOriginal": "", "CreateDate": ""},
    {"DateTimeOriginal": None},
    {"OffsetTimeOriginal": "+02:00", "SubSecTimeOriginal": "045"},
])
def test_capture_time_without_date(meta):
    assert capture_time(meta, None) == (None, "no capture time in EXIF")
    assert capture_time(meta, zone(2)) == (None, "no capture time in EXIF")


@pytest.mark.parametrize("value", [
    "0000:00:00 00:00:00",
    "2024-05-01 12:34:56",
    "2024:05:01",
    "2024:13:01 12:00:00",
    "2024:05:01 24:00:00",
    "2024:05:01 12:00:60",
    "2024:05:01 25:00:00+02:00",
    "unknown",
    2024,
])
def test_capture_time_unreadable_date(value):
    meta = {"DateTimeOriginal": value, "OffsetTimeOriginal": "+02:00"}
    assert capture_time(meta, None) == (None, f"invalid capture time in EXIF: {value}")


def test_capture_time_unreadable_date_does_not_fall_back_to_create_date():
    meta = {"DateTimeOriginal": "0000:00:00 00:00:00", "CreateDate": "2024:05:01 12:00:00"}
    assert capture_time(meta, None) == (None, "invalid capture time in EXIF: 0000:00:00 00:00:00")


# find_photos

@pytest.fixture
def photo_tree(tmp_path):
    """A directory with photos, other files and nested subdirectories."""
    names = ["z.jpg", "B.JPG", "a.jpeg", "c.JpEg", "d.png", "notes.txt", "e.jpg.bak", "jpg",
             "b_dir/y.jpg", "b_dir/x.JPEG", "a_dir/m.jpg", "a_dir/nested/k.jpg",
             "a_dir/nested/raw.cr2", "album.jpg/inside.jpg", "empty/.keep"]
    for name in reversed(names):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return tmp_path


def test_find_photos_in_directory_only_top_level_sorted(photo_tree):
    found = find_photos([str(photo_tree)], recursive=False)
    assert found == [os.path.join(str(photo_tree), n) for n in ["B.JPG", "a.jpeg", "c.JpEg", "z.jpg"]]


RECURSIVE_ORDER = [
    "B.JPG", "a.jpeg", "c.JpEg", "z.jpg",
    "a_dir/m.jpg", "a_dir/nested/k.jpg",
    "album.jpg/inside.jpg",
    "b_dir/x.JPEG", "b_dir/y.jpg",
]


def test_find_photos_recursive_walks_sorted_subdirectories_after_files(photo_tree):
    root = str(photo_tree)
    found = find_photos([root], recursive=True)
    assert found == [os.path.join(root, *n.split("/")) for n in RECURSIVE_ORDER]


@pytest.mark.parametrize("recursive", [False, True])
def test_find_photos_skips_backups_and_temporary_files(photo_tree, recursive):
    for name in ["originals/z.jpg", "a_dir/originals/m.jpg", ".gpxfoto-k2j4.jpg",
                 "b_dir/.gpxfoto-x1.jpg", "originals/.gpxfoto-c.jpg"]:
        (photo_tree / name).parent.mkdir(parents=True, exist_ok=True)
        (photo_tree / name).write_bytes(b"")
    root = str(photo_tree)
    expected = RECURSIVE_ORDER if recursive else RECURSIVE_ORDER[:4]
    assert find_photos([root], recursive=recursive) == [
        os.path.join(root, *n.split("/")) for n in expected]


def test_find_photos_searches_a_backup_directory_given_explicitly(tmp_path):
    backups = tmp_path / "originals"
    backups.mkdir()
    (backups / "a.jpg").write_bytes(b"")
    temp = tmp_path / ".gpxfoto-left.jpg"
    temp.write_bytes(b"")
    assert find_photos([str(backups), str(temp)], recursive=True) == [
        str(backups / "a.jpg"), str(temp)]


def test_find_photos_returns_each_photo_once(photo_tree, monkeypatch):
    monkeypatch.chdir(photo_tree)
    os.symlink("z.jpg", "link.jpg")
    os.symlink("b_dir", "b_link")
    found = find_photos(["z.jpg", ".", "./z.jpg", "link.jpg", "b_dir/y.jpg", "b_link", "b_dir"],
                        recursive=False)
    assert found == ["z.jpg", os.path.join(".", "B.JPG"), os.path.join(".", "a.jpeg"),
                     os.path.join(".", "c.JpEg"), "b_dir/y.jpg",
                     os.path.join("b_link", "x.JPEG")]


def test_find_photos_sorts_whatever_order_the_file_system_lists(photo_tree, monkeypatch):
    walk = os.walk

    def reverse_sorted_walk(top, **kwargs):
        for directory, subdirs, files in walk(top, **kwargs):
            subdirs.sort(reverse=True)
            files.sort(reverse=True)
            yield directory, subdirs, files

    monkeypatch.setattr(photos.os, "walk", reverse_sorted_walk)
    root = str(photo_tree)
    found = find_photos([root], recursive=True)
    assert found == [os.path.join(root, *n.split("/")) for n in RECURSIVE_ORDER]


def test_find_photos_accepts_explicit_files_of_any_extension(photo_tree):
    files = [str(photo_tree / n) for n in ["notes.txt", "d.png", "z.jpg", "a_dir/nested/raw.cr2"]]
    assert find_photos(files, recursive=False) == files
    assert find_photos(files, recursive=True) == files


def test_find_photos_keeps_order_of_arguments(photo_tree):
    file = str(photo_tree / "z.jpg")
    directory = str(photo_tree / "b_dir")
    assert find_photos([file, directory, str(photo_tree / "a_dir")], recursive=False) == [
        file, os.path.join(directory, "x.JPEG"), os.path.join(directory, "y.jpg"),
        str(photo_tree / "a_dir" / "m.jpg")]


def test_find_photos_keeps_relative_paths_as_given(photo_tree, monkeypatch):
    monkeypatch.chdir(photo_tree)
    file = os.path.join(".", "a_dir", "nested", "..", "m.jpg")
    assert find_photos([file, "b_dir"], recursive=False) == [
        file, os.path.join("b_dir", "x.JPEG"), os.path.join("b_dir", "y.jpg")]


def test_find_photos_directory_without_photos(photo_tree):
    assert find_photos([str(photo_tree / "empty")], recursive=True) == []
    assert find_photos([], recursive=True) == []


def test_find_photos_missing_path_raises_file_not_found(photo_tree):
    missing = str(photo_tree / "missing.jpg")
    with pytest.raises(FileNotFoundError) as error:
        find_photos([str(photo_tree / "z.jpg"), missing], recursive=False)
    assert str(error.value) == f"No such file or directory: {missing}"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_find_photos_rejects_explicit_path_that_is_not_a_regular_file(tmp_path):
    fifo = tmp_path / "pipe.jpg"
    os.mkfifo(fifo)
    with pytest.raises(FileNotFoundError):
        find_photos([str(fifo)], recursive=False)


# read_metadata with the real exiftool

def write_photo(path, *assignments):
    path.write_bytes(make_jpeg())
    if assignments:
        set_tags(path, *assignments)
    return str(path)


@needs_exiftool
def test_read_metadata_reads_tags_back(tmp_path):
    first = write_photo(tmp_path / "first.jpg",
                        "-DateTimeOriginal=2024:05:01 12:34:56", "-SubSecTimeOriginal=045",
                        "-OffsetTimeOriginal=+02:00", "-CreateDate=2024:05:01 12:00:00",
                        "-OffsetTime=+03:00")
    second = write_photo(tmp_path / "second.jpg",
                         "-GPSLatitude=52.25", "-GPSLatitudeRef=N",
                         "-GPSLongitude=21.5", "-GPSLongitudeRef=W", "-SubSecTimeOriginal=45")
    third = write_photo(tmp_path / "zdjęcie bez danych.jpg")
    assert read_metadata([first, second, third]) == [
        {"SourceFile": first, "DateTimeOriginal": "2024:05:01 12:34:56",
         "CreateDate": "2024:05:01 12:00:00", "OffsetTimeOriginal": "+02:00",
         "OffsetTime": "+03:00", "SubSecTimeOriginal": "045"},
        {"SourceFile": second, "SubSecTimeOriginal": 45,
         "GPSLatitude": 52.25, "GPSLongitude": -21.5},
        {"SourceFile": third},
    ]


@needs_exiftool
def test_read_metadata_result_feeds_capture_time(tmp_path):
    path = write_photo(tmp_path / "photo.jpg", "-DateTimeOriginal=2024:05:01 12:34:56",
                       "-SubSecTimeOriginal=045", "-OffsetTimeOriginal=-04:00")
    [meta] = read_metadata([path])
    assert "GPSLatitude" not in meta
    assert capture_time(meta, None) == (
        datetime(2024, 5, 1, 12, 34, 56, 45000, tzinfo=zone(-4)), TZ_CAMERA)


@needs_exiftool
def test_read_metadata_file_name_starting_with_dash(tmp_path, monkeypatch):
    write_photo(tmp_path / "-photo.jpg", "-CreateDate=2024:05:01 08:00:00")
    monkeypatch.chdir(tmp_path)
    assert read_metadata(["-photo.jpg"]) == [
        {"SourceFile": "-photo.jpg", "CreateDate": "2024:05:01 08:00:00"}]


@needs_exiftool
def test_read_metadata_damaged_file_does_not_fail_the_batch(tmp_path):
    # exiftool exits with status 1 here but still prints data for every file
    damaged = tmp_path / "damaged.jpg"
    damaged.write_bytes(b"\xff\xd8 not a JPEG")
    photo = write_photo(tmp_path / "photo.jpg", "-CreateDate=2024:05:01 08:00:00")
    assert subprocess.run(["exiftool", "--", str(damaged)], capture_output=True).returncode != 0
    assert read_metadata([str(damaged), photo]) == [
        {"SourceFile": str(damaged)},
        {"SourceFile": photo, "CreateDate": "2024:05:01 08:00:00"}]


@needs_exiftool
def test_read_metadata_raises_when_exiftool_prints_nothing(tmp_path):
    missing = str(tmp_path / "missing.jpg")
    with pytest.raises(RuntimeError) as error:
        read_metadata([missing])
    message = str(error.value)
    assert message.startswith("exiftool returned no data:\n")
    assert f"File not found - {missing}" in message


# read_metadata with a fake exiftool

PREFIX = ["exiftool", "-json", "-n", "-DateTimeOriginal", "-CreateDate",
          "-OffsetTimeOriginal", "-OffsetTime", "-SubSecTimeOriginal",
          "-GPSLatitude", "-GPSLongitude", "--"]


@pytest.fixture
def fake_exiftool(monkeypatch):
    """Replace subprocess.run; returns the list of recorded commands."""
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs == {"capture_output": True, "text": True}
        files = command[len(PREFIX):]
        stdout = json.dumps([{"SourceFile": f} for f in files])
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(photos.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("count, batches", [
    (0, []),
    (1, [1]),
    (500, [500]),
    (501, [500, 1]),
    (1001, [500, 500, 1]),
])
def test_read_metadata_runs_exiftool_in_batches_of_500(fake_exiftool, count, batches):
    files = [f"/photos/{i:04}.jpg" for i in range(count)]
    assert read_metadata(files) == [{"SourceFile": f} for f in files]
    assert [len(c) - len(PREFIX) for c in fake_exiftool] == batches
    assert all(c[:len(PREFIX)] == PREFIX for c in fake_exiftool)
    assert [f for c in fake_exiftool for f in c[len(PREFIX):]] == files


@pytest.mark.parametrize("stdout", ["", "  \n"])
def test_read_metadata_raises_with_exiftool_errors(monkeypatch, stdout):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout, "Error: boom\n")

    monkeypatch.setattr(photos.subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        read_metadata(["a.jpg"])
    assert str(error.value) == "exiftool returned no data:\nError: boom\n"


def test_read_metadata_raises_when_a_later_batch_is_empty(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        stdout = json.dumps([{"SourceFile": f} for f in command[len(PREFIX):]])
        return subprocess.CompletedProcess(command, 0, stdout if len(calls) == 1 else "", "")

    monkeypatch.setattr(photos.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        read_metadata([f"{i}.jpg" for i in range(600)])
    assert len(calls) == 2
