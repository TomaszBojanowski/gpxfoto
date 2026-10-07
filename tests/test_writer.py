"""write_location: GPS tags through exiftool, verified and written atomically."""
import os
import shutil
import stat
import struct
import subprocess
import time
import types
from datetime import datetime, timezone

import pytest

from conftest import latin2_name, make_jpeg, needs_exiftool, read_tags, set_tags
from gpxfoto.engine import writer
from gpxfoto.engine.writer import BACKUP_DIR, BACKUP_MARKER, image_checksum, write_location

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


def backups(directory):
    """Names in the backup directory, without its marker."""
    return sorted(set(os.listdir(directory / BACKUP_DIR)) - {BACKUP_MARKER})


def backup_dir(directory):
    """A backup directory, marked as gpxfoto marks it."""
    (directory / BACKUP_DIR).mkdir()
    (directory / BACKUP_DIR / BACKUP_MARKER).write_text("")
    return directory / BACKUP_DIR


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
        self.commands, self.temp_existed, self.umasks = [], [], []

    def __call__(self, command, capture_output=False, text=False, check=False, errors=None,
                 umask=-1):
        self.commands.append(command)
        self.umasks.append(umask)
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


def test_exiftool_writes_a_private_file(photo, fake_exiftool):
    fake = fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert fake.umasks == [0o077]


@needs_exiftool
def test_result_is_private_while_it_is_checked(tmp_path, photo, monkeypatch):
    os.chmod(photo, 0o600)
    modes = []
    real = writer.image_checksum

    def checksum(path):
        if os.path.basename(path).startswith(".gpxfoto-"):
            modes.append(stat.S_IMODE(os.stat(path).st_mode))
        return real(path)

    monkeypatch.setattr(writer, "image_checksum", checksum)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert modes == [0o600]
    assert stat.S_IMODE(os.stat(photo).st_mode) == 0o600


def test_interruption_right_after_the_temporary_file_leaves_nothing(tmp_path, photo,
                                                                    fake_exiftool, monkeypatch):
    real_close = os.close

    def close_then_fail(fd):
        real_close(fd)
        monkeypatch.setattr(writer.os, "close", real_close)
        raise KeyboardInterrupt

    fake_exiftool()
    monkeypatch.setattr(writer.os, "close", close_then_fail)
    with pytest.raises(KeyboardInterrupt):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert os.listdir(tmp_path) == ["photo.jpg"]


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
    # Every GPS value names the EXIF GPS group, so exiftool writes it nowhere else
    assert not [arg for arg in command if arg.startswith("-GPS") and not arg.startswith("-GPS:")]
    # Deletions ("-GPS:Tag=") are left out
    tags = dict(arg[len("-GPS:"):].split("=", 1) for arg in map(str, command)
                if arg.startswith("-GPS:GPS") and not arg.endswith("="))
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


def test_extended_attributes_the_photo_lacks_are_removed(tmp_path, photo, fake_exiftool,
                                                         monkeypatch):
    set_xattr(photo, "user.xdg.comment", b"Hut at the pass")
    fake = fake_exiftool()

    def run_and_add_attribute(command, **kwargs):
        result = fake(command, **kwargs)
        os.setxattr(command[command.index("-o") + 1], "user.added", b"1")
        return result

    monkeypatch.setattr(writer.subprocess, "run", run_and_add_attribute)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert os.listxattr(photo) == ["user.xdg.comment"]


def posix_acl(*entries):
    """A POSIX access control list in the form Linux keeps it in extended attributes."""
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)


def test_access_control_list_of_the_directory_is_not_inherited(tmp_path, photo, fake_exiftool):
    # New files in the directory give user 4242 access, which the photo does not
    anyone = 0xFFFFFFFF
    set_xattr(tmp_path, "system.posix_acl_default", posix_acl(
        (0x01, 6, anyone), (0x02, 6, 4242), (0x04, 4, anyone), (0x10, 6, anyone),
        (0x20, 0, anyone)))
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    for path in (photo, tmp_path / BACKUP_DIR / "photo.jpg"):
        assert "system.posix_acl_access" not in os.listxattr(path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


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


@pytest.fixture
def disk_events(tmp_path, monkeypatch):
    """Records fsync, link and replace calls, with temporary names shown as TEMP."""
    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("needs /proc to tell which file is synced")
    events = []
    real_fsync, real_link, real_replace = os.fsync, os.link, os.replace

    def name(path):
        parts = os.path.relpath(path, tmp_path).split(os.sep)
        return "/".join("TEMP" if part.startswith(".gpxfoto-") else part for part in parts)

    def fsync(fd):
        events.append(("sync", name(os.readlink(f"/proc/self/fd/{fd}"))))
        real_fsync(fd)

    def link(source, target):
        events.append(("link", name(source), name(target)))
        real_link(source, target)

    def replace(source, target):
        events.append(("replace", name(source), name(target)))
        real_replace(source, target)

    monkeypatch.setattr(writer.os, "fsync", fsync)
    monkeypatch.setattr(writer.os, "link", link)
    monkeypatch.setattr(writer.os, "replace", replace)
    return events


def test_everything_reaches_the_disk_before_the_photo_is_replaced(photo, fake_exiftool,
                                                                  disk_events):
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert disk_events == [
        ("sync", "TEMP"),                       # the result
        ("sync", "TEMP"),                       # the new backup directory with its marker
        ("sync", "."),                          # its name
        ("sync", "originals/TEMP"),             # the backup copy
        ("link", "originals/TEMP", "originals/photo.jpg"),
        ("sync", "originals"),                  # its name
        ("replace", "TEMP", "photo.jpg"),
        ("sync", "."),                          # the photo's new file
    ]


def test_without_backup_the_result_is_synced_before_the_replacement(photo, fake_exiftool,
                                                                    disk_events):
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert disk_events == [("sync", "TEMP"), ("replace", "TEMP", "photo.jpg"), ("sync", ".")]


def test_failed_sync_of_the_result_keeps_the_photo(tmp_path, photo, fake_exiftool, monkeypatch):
    def fail(fd):
        raise OSError(5, "Input/output error")

    before = state(photo)
    fake_exiftool()
    monkeypatch.setattr(writer.os, "fsync", fail)
    with pytest.raises(OSError, match="Input/output error"):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=False)
    assert state(photo) == before
    assert os.listdir(tmp_path) == ["photo.jpg"]


def test_directory_that_cannot_be_synced_does_not_stop_writing(photo, fake_exiftool,
                                                               monkeypatch):
    real = os.fsync

    def files_only(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(22, "Invalid argument")
        real(fd)

    expected = add_comment(photo.read_bytes())
    fake_exiftool()
    monkeypatch.setattr(writer.os, "fsync", files_only)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
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
    assert backups(tmp_path) == ["photo.jpg"]
    assert state(tmp_path / "originals" / "photo.jpg") == before
    assert state(photo) == (add_comment(before[0]), MTIME_NS, 0o640)


def test_backup_into_existing_directory(tmp_path, photo, fake_exiftool):
    other = backup_dir(tmp_path) / "other.jpg"
    other.write_bytes(b"other")
    original = photo.read_bytes()
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert backups(tmp_path) == ["other.jpg", "photo.jpg"]
    assert (tmp_path / "originals" / "photo.jpg").read_bytes() == original
    assert other.read_bytes() == b"other"


@pytest.mark.parametrize("make", [
    pytest.param(lambda p: p.write_bytes(b"a file, not a directory"), id="file"),
    pytest.param(lambda p: (p.mkdir(), (p / "x.jpg").write_bytes(b"")), id="unmarked directory"),
    pytest.param(lambda p: os.symlink("elsewhere", p), id="broken link"),
])
def test_failed_backup_keeps_original(tmp_path, photo, fake_exiftool, make):
    """Something not made by gpxfoto in place of the backup directory."""
    make(tmp_path / "originals")
    before = state(photo)
    fake_exiftool()
    with pytest.raises(RuntimeError) as raised:
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert str(raised.value) == (f"“{tmp_path / 'originals'}” already exists and is not a "
                                 "backup directory of gpxfoto")
    assert state(photo) == before
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]


def test_new_backup_directory_is_marked_and_owned_like_its_parent(tmp_path, photo, fake_exiftool,
                                                                   monkeypatch):
    album = tmp_path / "album"
    album.mkdir()
    album.chmod(0o750)
    os.rename(photo, album / "photo.jpg")
    owners = []
    real_chown = os.chown

    def chown(path, uid, gid):
        owners.append((os.path.basename(path), uid, gid))
        return real_chown(path, uid, gid)

    fake_exiftool()
    monkeypatch.setattr(writer.os, "chown", chown)
    write_location(album / "photo.jpg", 50.0, 19.0, 200.0, TIME, backup=True)
    backups_dir = album / BACKUP_DIR
    assert stat.S_IMODE(os.stat(backups_dir).st_mode) == 0o750
    parent = os.stat(album)
    assert (os.path.basename(owners[0][0]).startswith(".gpxfoto-"), owners[0][1:]) == (
        True, (parent.st_uid, parent.st_gid))
    assert "gpxfoto never changes or replaces these copies." in (
        backups_dir / BACKUP_MARKER).read_text()
    assert sorted(os.listdir(album)) == ["originals", "photo.jpg"]


def test_file_in_a_backup_directory_is_never_written(tmp_path, photo, fake_exiftool):
    backups_dir = backup_dir(tmp_path)
    copy = backups_dir / "photo.jpg"
    copy.write_bytes(photo.read_bytes())
    link = tmp_path / "link.jpg"
    os.symlink(copy, link)
    before = state(copy)
    fake = fake_exiftool()
    for path in (copy, link):
        with pytest.raises(RuntimeError, match="^this file is a backup copy made by gpxfoto, "
                                               "which is never changed$"):
            write_location(path, 50.0, 19.0, 200.0, TIME, backup=True)
    assert state(copy) == before
    assert fake.commands == []


def test_own_directory_named_like_the_backups_is_written_normally(tmp_path, fake_exiftool):
    """Only directories gpxfoto marked are protected."""
    own = tmp_path / "originals"
    own.mkdir()
    path = own / "photo.jpg"
    path.write_bytes(make_jpeg())
    fake_exiftool()
    write_location(path, 50.0, 19.0, 200.0, TIME, backup=False)
    assert path.read_bytes() == add_comment(make_jpeg())


def test_existing_backup_is_never_replaced(tmp_path, photo, fake_exiftool):
    """An earlier copy has the same image but other metadata."""
    backup = backup_dir(tmp_path) / "photo.jpg"
    backup.write_bytes(make_jpeg(comment=b"the real original"))
    expected = add_comment(photo.read_bytes())
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert backup.read_bytes() == make_jpeg(comment=b"the real original")
    assert backups(tmp_path) == ["photo.jpg"]
    assert photo.read_bytes() == expected


@pytest.mark.parametrize("make", [
    pytest.param(lambda p, photo: p.write_bytes(make_jpeg(quantization=range(2, 66))),
                 id="other image"),
    pytest.param(lambda p, photo: p.write_bytes(b"the real original"), id="not a JPEG"),
    pytest.param(lambda p, photo: p.write_bytes(make_jpeg()[:40]), id="cut off"),
    pytest.param(lambda p, photo: p.mkdir(), id="directory"),
    pytest.param(lambda p, photo: os.symlink("missing.jpg", p), id="broken link"),
    pytest.param(lambda p, photo: os.symlink(photo, p), id="link to the photo"),
])
def test_something_else_in_place_of_the_backup_stops_the_write(tmp_path, photo, fake_exiftool,
                                                               make):
    backup = backup_dir(tmp_path) / "photo.jpg"
    make(backup, photo)
    before = state(photo)
    fake = fake_exiftool()
    with pytest.raises(RuntimeError) as raised:
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert str(raised.value) == f"“{backup}” already exists and is not a copy of this photo"
    assert state(photo) == before
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]
    assert backups(tmp_path) == ["photo.jpg"]
    assert len(fake.commands) == 1


def test_new_backup_is_checked_against_the_photo(tmp_path, photo, fake_exiftool, monkeypatch):
    def wrong_copy(source, target):
        with open(target, "wb") as f:
            f.write(make_jpeg(quantization=range(2, 66)))

    before = state(photo)
    fake_exiftool()
    monkeypatch.setattr(writer.shutil, "copyfile", wrong_copy)
    with pytest.raises(RuntimeError, match="^the backup copy differs from the photo$"):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert state(photo) == before
    assert backups(tmp_path) == []


def test_repeated_writes_keep_the_first_backup(tmp_path, photo, fake_exiftool):
    original = state(photo)
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    write_location(photo, 51.0, 20.0, 300.0, TIME, backup=True)
    assert state(tmp_path / "originals" / "photo.jpg") == original
    assert backups(tmp_path) == ["photo.jpg"]


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
    assert backups(tmp_path) == []
    assert sorted(os.listdir(tmp_path)) == ["originals", "photo.jpg"]


@pytest.mark.parametrize("other_image, accepted", [(False, True), (True, False)])
def test_backup_made_meanwhile_by_another_run_is_kept(tmp_path, photo, fake_exiftool, monkeypatch,
                                                      other_image, accepted):
    """Another run puts its backup in place while this one is copying."""
    real_copy = shutil.copyfile
    theirs = make_jpeg(quantization=range(2, 66) if other_image else None, comment=b"theirs")

    def copy_while_another_run_finishes(source, target):
        real_copy(source, target)
        (tmp_path / BACKUP_DIR / "photo.jpg").write_bytes(theirs)

    before = state(photo)
    fake_exiftool()
    monkeypatch.setattr(writer.shutil, "copyfile", copy_while_another_run_finishes)
    if accepted:
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
        assert state(photo)[0] == add_comment(before[0])
    else:
        with pytest.raises(RuntimeError, match="is not a copy of this photo"):
            write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
        assert state(photo) == before
    assert (tmp_path / BACKUP_DIR / "photo.jpg").read_bytes() == theirs
    assert backups(tmp_path) == ["photo.jpg"]



@pytest.mark.parametrize("backup", [False, True])
def test_change_by_another_program_during_writing_is_not_undone(tmp_path, photo, fake_exiftool,
                                                               monkeypatch, backup):
    fake = fake_exiftool()
    edited = make_jpeg(quantization=range(2, 66))
    run = fake.__call__

    def edit_while_exiftool_runs(command, **kwargs):
        result = run(command, **kwargs)
        photo.write_bytes(edited)
        return result

    monkeypatch.setattr(writer.subprocess, "run", edit_while_exiftool_runs)
    with pytest.raises(RuntimeError, match="^(another program changed the photo in the meantime|"
                                           "the backup copy differs from the photo)$"):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=backup)
    assert photo.read_bytes() == edited
    assert [n for n in os.listdir(tmp_path) if n.startswith(".gpxfoto-")] == []


def test_photo_changed_since_its_metadata_was_read_is_refused(photo, fake_exiftool):
    seen = os.stat(photo)
    photo.write_bytes(add_comment(photo.read_bytes()))      # e.g. another program adds GPS
    changed = state(photo)
    fake = fake_exiftool()
    with pytest.raises(RuntimeError, match="^another program changed the photo in the meantime$"):
        write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True, seen=seen)
    assert state(photo) == changed
    assert fake.commands == []


def test_access_time_from_before_the_metadata_was_read_is_kept(tmp_path, photo, fake_exiftool):
    # Reading updates only the access time; os.utime would also change ctime
    now = os.stat(photo)
    seen = types.SimpleNamespace(
        st_dev=now.st_dev, st_ino=now.st_ino, st_size=now.st_size, st_mtime_ns=now.st_mtime_ns,
        st_ctime_ns=now.st_ctime_ns, st_atime_ns=now.st_atime_ns - 86400 * 10**9)
    fake_exiftool()
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True, seen=seen)
    for path in (photo, tmp_path / BACKUP_DIR / "photo.jpg"):
        info = os.stat(path)
        assert (info.st_atime_ns, info.st_mtime_ns) == (seen.st_atime_ns, seen.st_mtime_ns)

def test_backup_without_hard_links(tmp_path, photo, fake_exiftool, monkeypatch):
    def no_links(source, target):
        raise PermissionError(1, "Operation not permitted")

    before = state(photo)
    fake_exiftool()
    monkeypatch.setattr(writer.os, "link", no_links)
    write_location(photo, 50.0, 19.0, 200.0, TIME, backup=True)
    assert state(tmp_path / BACKUP_DIR / "photo.jpg") == before
    assert backups(tmp_path) == ["photo.jpg"]


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
    write_location(photo, -22.95, -43.21, None, TIME, backup=False, replace=True)
    assert gps_tags(photo) == {
        "GPSLatitudeRef": "S", "GPSLatitude": 22.95, "GPSLongitudeRef": "W",
        "GPSLongitude": 43.21, "GPSDateStamp": "2026:06:01", "GPSTimeStamp": "08:30:15",
        "GPSMapDatum": "WGS-84"}
    # XMP has no N/S or E/W fields, so its old GPS data must go, not be rewritten
    assert read_tags(photo, "XMP-exif:all") == {"DateTimeOriginal": "2026:06:01 10:30:15"}
    assert read_tags(photo, "EXIF:DateTimeOriginal") == {
        "DateTimeOriginal": "2026:06:01 10:30:15"}


@needs_exiftool
def test_other_gps_data_is_kept_when_there_was_no_location(photo):
    """A compass direction recorded without a position stays; only the
    fields of a location, such as a stray altitude, are written anew."""
    set_tags(photo, "-GPSImgDirection=123.4", "-GPSImgDirectionRef=M", "-GPSDestBearing=45",
             "-GPSDestBearingRef=T", "-GPSAltitude=812", "-GPSAltitudeRef=0",
             "-XMP-exif:GPSImgDirection=123.4")
    write_location(photo, -22.95, -43.21, None, TIME, backup=False)
    assert gps_tags(photo) == {
        "GPSLatitudeRef": "S", "GPSLatitude": 22.95, "GPSLongitudeRef": "W",
        "GPSLongitude": 43.21, "GPSDateStamp": "2026:06:01", "GPSTimeStamp": "08:30:15",
        "GPSMapDatum": "WGS-84", "GPSImgDirectionRef": "M", "GPSImgDirection": 123.4,
        "GPSDestBearingRef": "T", "GPSDestBearing": 45}
    assert read_tags(photo, "XMP-exif:all") == {"GPSImgDirection": 123.4}


@needs_exiftool
def test_xmp_gps_data_is_left_alone_without_replace(photo):
    """Without replace, XMP is not touched at all, so a value without its
    N/S or E/W can never end up there."""
    set_tags(photo, "-XMP-exif:GPSLatitude=1", "-XMP-exif:GPSLongitude=2")
    write_location(photo, -22.95, -43.21, -12.5, TIME, backup=False)
    assert read_tags(photo, "XMP-exif:all") == {"GPSLatitude": 1, "GPSLongitude": 2}
    tags = gps_tags(photo)
    assert (tags["GPSLatitudeRef"], tags["GPSLongitudeRef"], tags["GPSAltitudeRef"]) == (
        "S", "W", 1)


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
    assert backups(tmp_path) == [os.path.basename(path)]


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
