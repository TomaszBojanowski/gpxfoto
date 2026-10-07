"""write_location: GPS tags through exiftool, verified and written atomically."""
import os
import stat
import struct
import subprocess
import time
from datetime import datetime, timezone

import pytest

from conftest import latin2_name, make_jpeg, needs_exiftool, read_tags, set_tags
from gpxfoto.engine import writer
from gpxfoto.engine.writer import BACKUP_DIR, image_checksum, write_location

TIME = datetime(2026, 6, 1, 8, 30, 15, tzinfo=timezone.utc)
MTIME_NS = 1_700_000_000_123_456_789
REJECTED = "exiftool changed the image data; the result was discarded"


@pytest.fixture(autouse=True)
def english(monkeypatch):
    # Error texts are compared exactly; make sure no catalogue translates them.
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def photo(tmp_path):
    """A synthetic JPEG with mode 0o640 and a fixed modification time."""
    path = tmp_path / "photo.jpg"
    path.write_bytes(make_jpeg())
    os.chmod(path, 0o640)
    os.utime(path, ns=(MTIME_NS, MTIME_NS))
    return path


def state(path):
    """Bytes, mtime and permission bits of a file."""
    info = os.stat(path)
    return path.read_bytes(), info.st_mtime_ns, stat.S_IMODE(info.st_mode)


def add_comment(data):
    """A metadata-only change, as exiftool would make it."""
    return data[:2] + b"\xff\xfe" + struct.pack(">H", 11) + b"geotagged" + data[2:]


def with_exif(data, tiff):
    """Insert an APP1 Exif segment holding the given TIFF structure."""
    exif = b"Exif\x00\x00" + tiff
    return data[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + data[2:]


TRUNCATED_IFD = b"II*\x00\x08\x00\x00\x00" + struct.pack("<H", 5) + bytes(6)   # 5 entries, none there
BAD_IFD_OFFSET = b"II*\x00\x00\x10\x00\x00"          # IFD0 beyond the end of the segment


@pytest.fixture
def far_east_time_zone():
    """Local time 14 hours ahead of UTC, so local and UTC dates differ."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is not available")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "<+14>-14"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = old
        time.tzset()


class FakeExiftool:
    """Replaces subprocess.run; writes output(original bytes) to the path after -o."""

    def __init__(self, output=add_comment, returncode=0, stderr="", create=True, error=None):
        self.output, self.returncode, self.stderr = output, returncode, stderr
        self.create, self.error = create, error
        self.commands, self.temp_existed = [], []

    def __call__(self, command, capture_output=False, text=False, check=False, errors=None):
        self.commands.append(command)
        temp = command[command.index("-o") + 1]
        self.temp_existed.append(os.path.exists(temp))
        if self.create:
            with open(command[-1], "rb") as f:
                data = f.read()
            with open(temp, "wb") as f:
                f.write(self.output(data))
        if self.error:
            raise self.error
        # Output types as subprocess.run would return them for these arguments.
        stdout, stderr = ("", self.stderr) if text else (b"", self.stderr.encode())
        if not capture_output:
            stdout = stderr = None
        if check and self.returncode:
            raise subprocess.CalledProcessError(self.returncode, command, stdout, stderr)
        return subprocess.CompletedProcess(command, self.returncode, stdout, stderr)


@pytest.fixture
def fake_exiftool(monkeypatch):
    def install(**kwargs):
        fake = FakeExiftool(**kwargs)
        monkeypatch.setattr(writer.subprocess, "run", fake)
        return fake
    return install


@pytest.fixture
def replace_calls(monkeypatch):
    """Records the arguments of every os.replace call."""
    calls = []
    real = os.replace

    def spy(source, target):
        calls.append((source, target))
        real(source, target)

    monkeypatch.setattr(writer.os, "replace", spy)
    return calls


# With a fake exiftool

@pytest.mark.parametrize("mode", [0o640, 0o444], ids=oct)
def test_verified_result_replaces_photo(tmp_path, photo, fake_exiftool, mode):
    os.chmod(photo, mode)
    expected = add_comment(photo.read_bytes())
    fake_exiftool()
    write_location(photo, 50.0614, 19.9366, 219.4, TIME, backup=False)
    assert state(photo) == (expected, MTIME_NS, mode)
    assert os.listdir(tmp_path) == ["photo.jpg"]


@pytest.mark.parametrize("path", ["photo.jpg", os.path.join("album", "photo.jpg")])
def test_result_goes_through_temp_file_in_same_directory(
        tmp_path, monkeypatch, fake_exiftool, replace_calls, path):
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(make_jpeg())
    fake = fake_exiftool()
    write_location(path, 50.0, 19.0, None, TIME, backup=False)

    assert len(replace_calls) == 1
    temp, target = replace_calls[0]
    assert target == os.path.realpath(path)
    assert os.path.dirname(temp) == os.path.dirname(target)
    assert os.path.basename(temp).startswith(".gpxfoto-")
    assert temp.endswith(".jpg")
    assert fake.commands[0][-4:] == ["-o", temp, "--", target]
    assert fake.temp_existed == [False]              # exiftool -o refuses existing files
    assert not os.path.exists(temp)


@pytest.mark.parametrize("lat, lon, ele, expected", [
    pytest.param(50.06143217, 19.93658333, 1234.56, {
        "GPSLatitude": 50.06143217, "GPSLatitudeRef": "N", "GPSLongitude": 19.93658333,
        "GPSLongitudeRef": "E", "GPSAltitude": 1234.6, "GPSAltitudeRef": "0"}, id="north east"),
    pytest.param(-33.8568, -151.2153, -12.5, {
        "GPSLatitude": 33.8568, "GPSLatitudeRef": "S", "GPSLongitude": 151.2153,
        "GPSLongitudeRef": "W", "GPSAltitude": 12.5, "GPSAltitudeRef": "1"}, id="south west"),
    pytest.param(0.0, 0.0, 0.0, {
        "GPSLatitude": 0.0, "GPSLatitudeRef": "N", "GPSLongitude": 0.0,
        "GPSLongitudeRef": "E", "GPSAltitude": 0.0, "GPSAltitudeRef": "0"}, id="zero"),
    pytest.param(49.2, -0.37, None, {
        "GPSLatitude": 49.2, "GPSLatitudeRef": "N", "GPSLongitude": 0.37,
        "GPSLongitudeRef": "W"}, id="no elevation"),
])
def test_exiftool_gets_unsigned_values_and_utc_time(photo, fake_exiftool, far_east_time_zone,
                                                    lat, lon, ele, expected):
    new_year = datetime(2026, 12, 31, 23, 59, 58, tzinfo=timezone.utc)
    assert new_year.astimezone().year == 2027
    fake = fake_exiftool()
    write_location(photo, lat, lon, ele, new_year, backup=False)
    command = fake.commands[0]
    # Values are numbers, not print values; minor errors must not block writing.
    assert {"-n", "-m"} <= set(command)
    # Deletions ("-Tag=") are left out: clearing a stale altitude would be fine.
    tags = dict(arg[1:].split("=", 1) for arg in map(str, command)
                if arg.startswith("-GPS") and not arg.endswith("="))
    for name in ("GPSLatitude", "GPSLongitude", "GPSAltitude"):
        if name in tags:
            tags[name] = float(tags[name])
    assert tags == {**expected, "GPSDateStamp": "2026:12:31", "GPSTimeStamp": "23:59:58",
                    "GPSMapDatum": "WGS-84"}


def test_access_and_modification_times_are_copied(photo, fake_exiftool):
    atime = 4_102_444_800_123_456_789                # 2100; reading does not update it
    os.utime(photo, ns=(atime, MTIME_NS))
    photo.read_bytes()
    if os.stat(photo).st_atime_ns != atime:
        pytest.skip("the file system updates the access time on every read")
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    for path in (photo, photo.parent / "originals" / "photo.jpg"):
        info = os.stat(path)
        assert (info.st_atime_ns, info.st_mtime_ns) == (atime, MTIME_NS)


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
def test_no_file_descriptor_is_left_open(photo, fake_exiftool):
    fake_exiftool()
    before = len(os.listdir("/proc/self/fd"))
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("output, error, message", [
    pytest.param(lambda d: d[:-3] + bytes([d[-3] ^ 1]) + d[-2:], RuntimeError, REJECTED,
                 id="scan changed"),
    pytest.param(lambda d: make_jpeg(quantization=range(2, 66)), RuntimeError, REJECTED,
                 id="quantization changed"),
    pytest.param(lambda d: d + b"trailer", RuntimeError, REJECTED, id="trailer added"),
    pytest.param(lambda d: d[:-5], RuntimeError, REJECTED, id="truncated scan"),
    pytest.param(lambda d: d[:100], ValueError, "damaged JPEG structure",
                 id="truncated header"),
    pytest.param(lambda d: b"", ValueError, "not a JPEG file", id="empty"),
])
def test_damaged_result_is_rejected(tmp_path, photo, fake_exiftool, output, error, message):
    before = state(photo)
    fake_exiftool(output=output)
    with pytest.raises(error) as raised:
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert str(raised.value) == message
    assert state(photo) == before
    assert os.listdir(tmp_path) == ["photo.jpg"]     # no temp file, no backup directory


@pytest.mark.parametrize("returncode, stderr, create, message", [
    (1, "Error: File format error - photo.jpg\n", False, "Error: File format error - photo.jpg"),
    (1, "  Error: Disk full  \n", True, "Error: Disk full"),
    (1, "", False, "exiftool did not write the file"),
    (0, "", False, "exiftool did not write the file"),
    (0, "Warning: Nothing to write\n", False, "Warning: Nothing to write"),
    (-9, "", True, "exiftool did not write the file"),          # killed by a signal
])
def test_exiftool_failure_is_reported(tmp_path, photo, fake_exiftool,
                                      returncode, stderr, create, message):
    before = state(photo)
    fake_exiftool(returncode=returncode, stderr=stderr, create=create)
    with pytest.raises(RuntimeError) as raised:
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert str(raised.value) == message
    assert state(photo) == before
    assert os.listdir(tmp_path) == ["photo.jpg"]


@pytest.mark.parametrize("error", [
    FileNotFoundError(2, "No such file or directory", "exiftool"),
    KeyboardInterrupt(),
])
def test_interrupted_exiftool_leaves_no_temp_file(tmp_path, photo, fake_exiftool, error):
    before = state(photo)
    fake_exiftool(output=lambda d: d[:50], error=error)
    with pytest.raises(type(error)):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert state(photo) == before
    assert os.listdir(tmp_path) == ["photo.jpg"]


@pytest.mark.parametrize("backup", [False, True])
def test_failed_replace_keeps_original(tmp_path, photo, fake_exiftool, monkeypatch, backup):
    def fail(source, target):
        raise OSError(28, "No space left on device")

    before = state(photo)
    fake_exiftool()
    monkeypatch.setattr(writer.os, "replace", fail)
    with pytest.raises(OSError, match="No space left on device"):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=backup)
    assert state(photo) == before
    assert sorted(os.listdir(tmp_path)) == (["originals", "photo.jpg"] if backup
                                            else ["photo.jpg"])


def test_mode_and_times_are_set_before_the_replacement(photo, fake_exiftool, monkeypatch):
    """The atomic replacement already brings the original permissions and
    modification time; nothing is fixed up afterwards."""
    seen = []
    real = os.replace

    def spy(source, target):
        info = os.stat(source)
        seen.append((stat.S_IMODE(info.st_mode), info.st_mtime_ns))
        real(source, target)

    fake_exiftool()
    monkeypatch.setattr(writer.os, "replace", spy)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert seen == [(0o640, MTIME_NS)]


def test_times_are_those_reported_for_the_photo(photo, fake_exiftool, monkeypatch):
    """Independent of whether the file system updates access times on reads."""
    os.utime(photo, ns=(MTIME_NS - 10**9, MTIME_NS))
    reported, applied = [], []
    real_stat, real_utime = os.stat, os.utime

    def spy_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if os.fspath(path) == os.fspath(photo):
            reported.append((info.st_atime_ns, info.st_mtime_ns))
        return info

    def spy_utime(path, *args, **kwargs):
        applied.append(kwargs.get("ns"))
        return real_utime(path, *args, **kwargs)

    fake_exiftool()
    monkeypatch.setattr(writer.os, "stat", spy_stat)
    monkeypatch.setattr(writer.os, "utime", spy_utime)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    # The last times set are those reported before the photo was read
    assert applied[-1] == reported[0] == (MTIME_NS - 10**9, MTIME_NS)


def test_access_time_from_before_reading_is_kept(tmp_path, photo, fake_exiftool):
    """Reading the photo may update its access time (relatime does when it is
    older than the modification time); the original one is kept."""
    atime = MTIME_NS - 86400 * 10**9
    os.utime(photo, ns=(atime, MTIME_NS))
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    for path in (photo, tmp_path / BACKUP_DIR / "photo.jpg"):
        info = os.stat(path)
        assert (info.st_atime_ns, info.st_mtime_ns) == (atime, MTIME_NS)


def set_xattr(path, name, value):
    try:
        os.setxattr(path, name, value)
    except (AttributeError, OSError):
        pytest.skip("extended attributes are not supported here")


def test_extended_attributes_are_kept(tmp_path, photo, fake_exiftool):
    set_xattr(photo, "user.xdg.comment", b"Hut at the pass")
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    for path in (photo, tmp_path / BACKUP_DIR / "photo.jpg"):
        assert os.getxattr(path, "user.xdg.comment") == b"Hut at the pass"


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0,
                    reason="only root can give a file to another user")
def test_owner_and_group_are_kept(tmp_path, photo, fake_exiftool):
    os.chown(photo, 4321, 8765)
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    for path in (photo, tmp_path / BACKUP_DIR / "photo.jpg"):
        info = os.stat(path)
        assert (info.st_uid, info.st_gid) == (4321, 8765)


def test_owner_that_cannot_be_set_does_not_stop_writing(photo, fake_exiftool, monkeypatch):
    calls = []

    def refuse(path, uid, gid):
        calls.append((uid, gid))
        raise PermissionError(1, "Operation not permitted")

    expected = add_comment(photo.read_bytes())
    fake_exiftool()
    monkeypatch.setattr(writer.os, "chown", refuse)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    info = os.stat(photo)
    assert calls == [(info.st_uid, info.st_gid), (-1, info.st_gid)]
    assert state(photo) == (expected, MTIME_NS, 0o640)


def test_non_jpeg_photo_is_rejected_before_exiftool(tmp_path, fake_exiftool):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"not an image")
    fake = fake_exiftool()
    with pytest.raises(ValueError, match="^not a JPEG file$"):
        write_location(path, 50.0, 19.0, 200.0, TIME, backup=True)
    assert fake.commands == []
    assert path.read_bytes() == b"not an image"
    assert os.listdir(tmp_path) == ["photo.jpg"]


@pytest.mark.parametrize("lat, lon, ele, location", [
    (float("nan"), 19.0, 200.0, "nan, 19.0, 200.0 m"),
    (50.0, float("inf"), None, "50.0, inf"),
    (90.5, 19.0, None, "90.5, 19.0"),
    (50.0, -180.25, None, "50.0, -180.25"),
    (50.0, 19.0, float("nan"), "50.0, 19.0, nan m"),
    (50.0, 19.0, float("-inf"), "50.0, 19.0, -inf m"),
])
def test_invalid_location_is_refused_before_exiftool(tmp_path, photo, fake_exiftool,
                                                     lat, lon, ele, location):
    before = state(photo)
    fake = fake_exiftool()
    with pytest.raises(ValueError) as raised:
        write_location(photo, lat, lon, ele, TIME, backup=True)
    assert str(raised.value) == f"invalid location: {location}"
    assert fake.commands == []
    assert state(photo) == before
    assert os.listdir(tmp_path) == ["photo.jpg"]


@pytest.mark.parametrize("lat, lon", [(90.0, 180.0), (-90.0, -180.0)])
def test_location_at_the_limits_is_written(photo, fake_exiftool, lat, lon):
    fake = fake_exiftool()
    write_location(photo, lat, lon, None, TIME, backup=False)
    assert len(fake.commands) == 1


def test_symbolic_link_leads_to_the_photo(tmp_path, photo, fake_exiftool):
    library = tmp_path / "library"
    library.mkdir()
    real = library / "photo.jpg"
    os.rename(photo, real)
    album = tmp_path / "album"
    album.mkdir()
    link = album / "photo.jpg"
    os.symlink(os.path.join("..", "library", "photo.jpg"), link)
    before = state(real)
    fake_exiftool()
    write_location(link, 50.0, 19.0, 200.0, TIME, backup=True)
    assert os.path.islink(link)
    assert state(real) == (add_comment(before[0]), MTIME_NS, 0o640)
    assert state(library / BACKUP_DIR / "photo.jpg") == before
    assert os.listdir(album) == ["photo.jpg"]


def test_photo_with_several_hard_links_is_refused(tmp_path, photo, fake_exiftool):
    other = tmp_path / "other.jpg"
    os.link(photo, other)
    before = state(photo)
    fake = fake_exiftool()
    with pytest.raises(RuntimeError) as raised:
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert str(raised.value) == "the file has several hard links; writing it would separate them"
    assert fake.commands == []
    assert state(photo) == state(other) == before
    assert os.stat(photo).st_nlink == 2
    assert sorted(os.listdir(tmp_path)) == ["other.jpg", "photo.jpg"]


def test_backup_is_copy_of_original(tmp_path, photo, fake_exiftool):
    before = state(photo)
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert BACKUP_DIR == "originals"
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]
    assert os.listdir(tmp_path / "originals") == ["photo.jpg"]
    assert state(tmp_path / "originals" / "photo.jpg") == before
    assert state(photo) == (add_comment(before[0]), MTIME_NS, 0o640)


def test_backup_into_existing_directory(tmp_path, photo, fake_exiftool):
    other = tmp_path / "originals" / "other.jpg"
    other.parent.mkdir()
    other.write_bytes(b"other")
    original = photo.read_bytes()
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert sorted(os.listdir(tmp_path / "originals")) == ["other.jpg", "photo.jpg"]
    assert (tmp_path / "originals" / "photo.jpg").read_bytes() == original
    assert other.read_bytes() == b"other"


def test_failed_backup_keeps_original(tmp_path, photo, fake_exiftool):
    (tmp_path / "originals").write_bytes(b"a file, not a directory")
    before = state(photo)
    fake_exiftool()
    with pytest.raises(FileExistsError):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert state(photo) == before
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]


def test_existing_backup_is_never_replaced(tmp_path, photo, fake_exiftool):
    backup = tmp_path / "originals" / "photo.jpg"
    backup.parent.mkdir()
    backup.write_bytes(b"the real original")
    expected = add_comment(photo.read_bytes())
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert backup.read_bytes() == b"the real original"
    assert os.listdir(backup.parent) == ["photo.jpg"]
    assert photo.read_bytes() == expected


def test_repeated_writes_keep_the_first_backup(tmp_path, photo, fake_exiftool):
    original = state(photo)
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    write_location(photo, 51.0, 20.0, 300.0, TIME, backup=True)
    assert state(tmp_path / "originals" / "photo.jpg") == original
    assert os.listdir(tmp_path / "originals") == ["photo.jpg"]


def test_interrupted_backup_leaves_no_partial_copy(tmp_path, photo, fake_exiftool, monkeypatch):
    def broken_copy(source, target):
        with open(source, "rb") as f, open(target, "wb") as out:
            out.write(f.read(10))
        raise OSError(28, "No space left on device")

    before = state(photo)
    fake_exiftool()
    monkeypatch.setattr(writer.shutil, "copyfile", broken_copy)
    with pytest.raises(OSError, match="No space left on device"):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert state(photo) == before
    assert os.listdir(tmp_path / "originals") == []
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]


# With the real exiftool

def gps_tags(path):
    tags = read_tags(path, "GPS:all")
    tags.pop("GPSVersionID", None)                   # added by exiftool itself
    return tags


@needs_exiftool
@pytest.mark.parametrize("lat, lon, ele, expected", [
    pytest.param(50.06143217, 19.93658333, 219.4, {
        "GPSLatitudeRef": "N", "GPSLatitude": 50.06143217,
        "GPSLongitudeRef": "E", "GPSLongitude": 19.93658333,
        "GPSAltitudeRef": 0, "GPSAltitude": 219.4}, id="north east"),
    pytest.param(-33.8568, -151.2153, -12.5, {
        "GPSLatitudeRef": "S", "GPSLatitude": 33.8568,
        "GPSLongitudeRef": "W", "GPSLongitude": 151.2153,
        "GPSAltitudeRef": 1, "GPSAltitude": 12.5}, id="south west below sea level"),
    pytest.param(0.0, 0.0, 0.0, {
        "GPSLatitudeRef": "N", "GPSLatitude": 0,
        "GPSLongitudeRef": "E", "GPSLongitude": 0,
        "GPSAltitudeRef": 0, "GPSAltitude": 0}, id="zero"),
    pytest.param(-0.00000001, 179.99999999, 1234.56, {
        "GPSLatitudeRef": "S", "GPSLatitude": 0.00000001,
        "GPSLongitudeRef": "E", "GPSLongitude": 179.99999999,
        "GPSAltitudeRef": 0, "GPSAltitude": 1234.6}, id="extremes"),
    pytest.param(49.2, -0.37, None, {
        "GPSLatitudeRef": "N", "GPSLatitude": 49.2,
        "GPSLongitudeRef": "W", "GPSLongitude": 0.37}, id="no elevation"),
])
def test_gps_tags_are_written(photo, lat, lon, ele, expected):
    write_location(photo, lat, lon, ele, TIME, backup=False)
    tags = gps_tags(photo)
    expected = {name: pytest.approx(value, abs=1e-8) if isinstance(value, float) else value
                for name, value in expected.items()}
    assert tags == {**expected, "GPSDateStamp": "2026:06:01", "GPSTimeStamp": "08:30:15",
                    "GPSMapDatum": "WGS-84"}


@needs_exiftool
def test_time_stamp_is_utc(photo, far_east_time_zone):
    last_second = datetime(2026, 12, 31, 23, 59, 58, tzinfo=timezone.utc)
    assert last_second.astimezone().year == 2027
    write_location(photo, 50.0, 19.0, None, last_second, backup=False)
    tags = read_tags(photo, "GPSDateStamp", "GPSTimeStamp")
    assert tags == {"GPSDateStamp": "2026:12:31", "GPSTimeStamp": "23:59:58"}


@needs_exiftool
@pytest.mark.parametrize("mode", [0o640, 0o600, 0o444])
def test_image_mode_and_mtime_are_preserved(tmp_path, photo, mode):
    os.chmod(photo, mode)
    data = photo.read_bytes()
    checksum = image_checksum(photo)
    write_location(photo, 50.0614, 19.9366, 219.4, TIME, backup=False)
    after = state(photo)
    assert after[0] != data
    assert after[1:] == (MTIME_NS, mode)
    assert image_checksum(photo) == checksum
    assert os.listdir(tmp_path) == ["photo.jpg"]


@needs_exiftool
def test_other_metadata_is_kept(photo):
    set_tags(photo, "-Make=Panasonic", "-Model=DC-S5M2", "-DateTimeOriginal=2026:06:01 10:30:15",
             "-OffsetTimeOriginal=+02:00", "-Comment=hello")
    names = ("Make", "Model", "DateTimeOriginal", "OffsetTimeOriginal", "Comment")
    before = read_tags(photo, *names)
    write_location(photo, 50.0614, 19.9366, 219.4, TIME, backup=False)
    assert read_tags(photo, *names) == before
    assert read_tags(photo, "GPSLatitude")["GPSLatitude"] == pytest.approx(50.0614, abs=1e-8)


@needs_exiftool
def test_old_gps_data_is_removed(photo):
    """An overwritten location must not keep parts of the old one."""
    set_tags(photo, "-GPSLatitude=1", "-GPSLatitudeRef=S", "-GPSLongitude=2",
             "-GPSLongitudeRef=W", "-GPSAltitude=812", "-GPSAltitudeRef=0",
             "-GPSImgDirection=90", "-GPSSpeed=4", "-XMP-exif:GPSLatitude=1",
             "-XMP-exif:GPSLongitude=2", "-XMP-exif:GPSAltitude=812",
             "-XMP-exif:GPSImgDirection=90", "-DateTimeOriginal=2026:06:01 10:30:15",
             "-XMP-exif:DateTimeOriginal=2026:06:01 10:30:15")
    write_location(photo, 50.0614, 19.9366, None, TIME, backup=False)
    assert gps_tags(photo) == {
        "GPSLatitudeRef": "N", "GPSLatitude": 50.0614, "GPSLongitudeRef": "E",
        "GPSLongitude": 19.9366, "GPSDateStamp": "2026:06:01", "GPSTimeStamp": "08:30:15",
        "GPSMapDatum": "WGS-84"}
    # exiftool also updates the GPS position it finds in XMP; the rest is gone
    assert read_tags(photo, "XMP-exif:all") == {
        "GPSLatitude": 50.0614, "GPSLongitude": 19.9366,
        "DateTimeOriginal": "2026:06:01 10:30:15"}
    assert read_tags(photo, "EXIF:DateTimeOriginal") == {
        "DateTimeOriginal": "2026:06:01 10:30:15"}


@needs_exiftool
def test_photo_without_gps_is_written_as_before(photo):
    """Removing old GPS data changes nothing when there is none."""
    copy = photo.parent / "copy.jpg"
    copy.write_bytes(photo.read_bytes())
    plain = photo.parent / "plain.jpg"
    subprocess.run(["exiftool", "-q", "-n", "-m", "-GPSLatitude=50.0614", "-GPSLatitudeRef=N",
                    "-GPSLongitude=19.9366", "-GPSLongitudeRef=E", "-GPSDateStamp=2026:06:01",
                    "-GPSTimeStamp=08:30:15", "-GPSMapDatum=WGS-84", "-GPSAltitude=219.4",
                    "-GPSAltitudeRef=0", "-o", str(plain), "--", str(copy)], check=True)
    write_location(photo, 50.0614, 19.9366, 219.4, TIME, backup=False)
    assert photo.read_bytes() == plain.read_bytes()


@needs_exiftool
@pytest.mark.parametrize("name", ["-photo.jpg", "zdjęcie z wakacji.jpg"])
def test_unusual_file_names(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    with open(name, "wb") as f:
        f.write(make_jpeg())
    write_location(name, -33.8568, -151.2153, None, TIME, backup=False)
    assert read_tags(tmp_path / name, "GPSLatitude", "GPSLongitude") == {
        "GPSLatitude": pytest.approx(-33.8568, abs=1e-8),
        "GPSLongitude": pytest.approx(-151.2153, abs=1e-8)}
    assert os.listdir(tmp_path) == [name]


@needs_exiftool
def test_real_exiftool_error_is_reported(tmp_path, photo):
    photo.write_bytes(with_exif(make_jpeg(), TRUNCATED_IFD))
    before = state(photo)
    with pytest.raises(RuntimeError, match="^Error: Truncated IFD0 directory - "):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert state(photo) == before
    assert os.listdir(tmp_path) == ["photo.jpg"]


@needs_exiftool
def test_file_name_that_is_not_utf8(tmp_path):
    path = latin2_name(tmp_path)
    with open(path, "wb") as f:
        f.write(make_jpeg())
    checksum = image_checksum(path)
    write_location(path, 50.0614, 19.9366, 219.4, TIME, backup=True)
    assert image_checksum(path) == checksum
    assert gps_tags(path)["GPSLatitude"] == 50.0614
    assert os.listdir(os.path.join(tmp_path, BACKUP_DIR)) == [os.path.basename(path)]


@needs_exiftool
def test_exiftool_error_about_file_name_that_is_not_utf8(tmp_path):
    """exiftool names the file in its error, in bytes that are not UTF-8."""
    path = latin2_name(tmp_path)
    with open(path, "wb") as f:
        f.write(with_exif(make_jpeg(), TRUNCATED_IFD))
    before = open(path, "rb").read()
    with pytest.raises(RuntimeError, match="^Error: Truncated IFD0 directory - .*zdj\ufffdcie"):
        write_location(path, 50.0, 19.0, 200.0, TIME, backup=False)
    assert open(path, "rb").read() == before
    assert os.listdir(tmp_path) == [os.path.basename(path)]


@needs_exiftool
def test_minor_exif_error_does_not_block_writing(photo):
    # Without -m exiftool refuses this file: "[minor] Bad IFD0 directory".
    photo.write_bytes(with_exif(make_jpeg(), BAD_IFD_OFFSET))
    checksum = image_checksum(photo)
    write_location(photo, 50.0614, 19.9366, None, TIME, backup=False)
    assert read_tags(photo, "GPSLatitude") == {"GPSLatitude": pytest.approx(50.0614, abs=1e-8)}
    assert image_checksum(photo) == checksum


@needs_exiftool
def test_backup_with_exiftool(tmp_path, photo):
    before = state(photo)
    write_location(photo, 50.0614, 19.9366, 219.4, TIME, backup=True)
    backup = tmp_path / "originals" / "photo.jpg"
    assert state(backup) == before
    assert gps_tags(backup) == {}
    assert gps_tags(photo)["GPSLatitudeRef"] == "N"
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]
