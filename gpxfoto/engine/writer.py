"""Writing the location with exiftool while checking that the image is untouched."""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from gettext import gettext as _

from gpxfoto.engine.track import MAX_ELEVATION

# Subdirectory for copies of the original files
BACKUP_DIR = "originals"
# File that marks a BACKUP_DIR as made by gpxfoto
BACKUP_MARKER = ".gpxfoto"
# Name prefix of temporary files, which a crash could leave behind
TEMP_PREFIX = ".gpxfoto-"
# The EXIF GPS fields of a location, which are always written anew
LOCATION_TAGS = ("GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef",
                 "GPSAltitude", "GPSAltitudeRef", "GPSDateStamp", "GPSTimeStamp", "GPSMapDatum")


def is_backup_dir(directory):
    """Whether directory is a backup directory made by gpxfoto."""
    return os.path.isfile(os.path.join(directory, BACKUP_MARKER))


def image_checksum(path):
    """SHA-256 of everything except metadata segments (APPn, COM).

    Covers the quantization and Huffman tables, the frame headers and the
    whole compressed image stream up to the end of the file.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            raise ValueError(_("not a JPEG file"))
        while True:
            header = f.read(4)
            if len(header) < 4 or header[0] != 0xFF:
                raise ValueError(_("damaged JPEG structure"))
            marker = header[1]
            length = int.from_bytes(header[2:4], "big")
            metadata = 0xE0 <= marker <= 0xEF or marker == 0xFE
            if marker == 0xDA:                      # start of image data
                digest.update(header)
                while True:
                    block = f.read(1 << 20)
                    if not block:
                        return digest.hexdigest()
                    digest.update(block)
            # The length includes its own two bytes
            if length < 2:
                raise ValueError(_("damaged JPEG structure"))
            data = f.read(length - 2)
            if len(data) != length - 2:
                raise ValueError(_("damaged JPEG structure"))
            if not metadata:
                digest.update(header)
                digest.update(data)


def write_location(path, lat, lon, ele, time_utc, backup, replace=False, seen=None):
    """Write a location into the photo at path, changing nothing else.

    replace means the photo already has a location. All its GPS data, also
    in XMP, then belongs to the old location and is removed. Otherwise only
    the fields of a location are written anew, and other GPS data, such as
    a compass direction, is kept.

    seen is os.stat() of the photo from before its metadata was read: the
    photo must not have changed since, and its access time is kept.
    """
    # Comparisons with NaN are false, so this also rules out NaN and infinity
    if not (-90 <= lat <= 90 and -180 <= lon <= 180
            and (ele is None or abs(ele) <= MAX_ELEVATION)):
        location = f"{lat}, {lon}" if ele is None else f"{lat}, {lon}, {ele} m"
        raise ValueError(_("invalid location: {location}").format(location=location))
    # Through a symbolic link, the file it points to is written
    path = os.path.realpath(path)
    if is_backup_dir(os.path.dirname(path)):
        raise RuntimeError(_("this file is a backup copy made by gpxfoto, "
                             "which is never changed"))
    # Taken before anything reads the photo, which may update its access time
    original = os.stat(path)
    if original.st_nlink > 1:
        # A new file replaces the photo, which would separate the links
        raise RuntimeError(_("the file has several hard links; writing it would separate them"))
    if seen is not None and _changed(seen, original):
        raise RuntimeError(_("another program changed the photo in the meantime"))
    access_ns = (seen or original).st_atime_ns
    before = image_checksum(path)
    directory = os.path.dirname(path)
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".jpg", dir=directory)
    try:
        os.close(fd)
        os.unlink(temp)              # exiftool -o requires that the file does not exist
        command = ["exiftool", "-q", "-n", "-m"]
        if replace:
            command += ["-GPS:all=", "-XMP-exif:GPS*="]
        else:
            command += [f"-GPS:{tag}=" for tag in LOCATION_TAGS]
        # The group is named, so that exiftool does not also write these
        # values, without their N/S and E/W, into GPS tags found in XMP.
        command += [
            f"-GPS:GPSLatitude={abs(lat):.8f}", f"-GPS:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-GPS:GPSLongitude={abs(lon):.8f}", f"-GPS:GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
            f"-GPS:GPSDateStamp={time_utc:%Y:%m:%d}", f"-GPS:GPSTimeStamp={time_utc:%H:%M:%S}",
            "-GPS:GPSMapDatum=WGS-84",
        ]
        if ele is not None:
            command += [f"-GPS:GPSAltitude={abs(ele):.1f}",
                        f"-GPS:GPSAltitudeRef={0 if ele >= 0 else 1}"]
        command += ["-o", temp, "--", path]
        # The result is readable only by its owner until it gets the photo's permissions
        process = subprocess.run(command, capture_output=True, text=True, errors="replace",
                                 umask=0o077)
        if process.returncode != 0 or not os.path.exists(temp):
            raise RuntimeError(process.stderr.strip() or _("exiftool did not write the file"))
        if image_checksum(temp) != before:
            raise RuntimeError(_("exiftool changed the image data; the result was discarded"))
        _copy_attributes(path, original, access_ns, temp)
        # After a power cut, the photo must be either the old or the new one
        _sync(temp)
        if backup:
            _back_up(path, directory, original, access_ns, before)
        if _changed(original, os.stat(path)):
            # Replacing the photo would undo that change
            raise RuntimeError(_("another program changed the photo in the meantime"))
        os.replace(temp, path)       # atomic replacement
        _sync_directory(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _changed(before, now):
    """Whether os.stat() results show that a file changed in between."""
    return ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns, now.st_ctime_ns))


def _sync(path):
    """Wait until what was written to the file at path is on the disk."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_directory(directory):
    """Wait until the names in directory are on the disk, where the system allows it."""
    try:
        _sync(directory)
    except OSError:
        pass                    # not every system and file system can sync a directory


def _copy_attributes(path, original, access_ns, target):
    """Give target the owner, permissions, extended attributes and times of the photo.

    Extended attributes include access control lists. Those the photo
    lacks, such as a list target inherited from its directory, are
    removed. The owner, group and attributes are copied as far as the
    system allows.
    """
    _copy_owner(original, target)
    _remove_extra_attributes(path, target)
    shutil.copystat(path, target)
    os.utime(target, ns=(access_ns, original.st_mtime_ns))


def _remove_extra_attributes(path, target):
    """Remove the extended attributes of target that path lacks, as far as the system allows."""
    try:
        extra = set(os.listxattr(target)) - set(os.listxattr(path))
    except (AttributeError, OSError):        # not supported by the system or file system
        return
    for name in extra:
        try:
            os.removexattr(target, name)
        except OSError:
            pass


def _copy_owner(original, target):
    """Give target the owner and group of original, as far as the system allows."""
    try:
        os.chown(target, original.st_uid, original.st_gid)
    except OSError:
        try:
            os.chown(target, -1, original.st_gid)    # allowed for members of the group
        except OSError:
            pass


def _backup_dir(directory):
    """Return BACKUP_DIR in directory, made and marked unless it exists.

    A new one gets the owner, group and permissions of directory. It is
    complete with its marker before it gets its name.
    """
    target_dir = os.path.join(directory, BACKUP_DIR)
    if not os.path.lexists(target_dir):
        temp = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=directory)
        try:
            with open(os.path.join(temp, BACKUP_MARKER), "w", encoding="utf-8") as f:
                f.write(_("This directory holds copies of the photos next to it as they were "
                          "before gpxfoto added locations to them. gpxfoto never changes or "
                          "replaces these copies.") + "\n")
            parent = os.stat(directory)
            _copy_owner(parent, temp)
            os.chmod(temp, stat.S_IMODE(parent.st_mode))
            _sync_directory(temp)
            try:
                os.rename(temp, target_dir)
            except OSError:
                pass                        # made meanwhile by another run; checked below
            else:
                _sync_directory(directory)
        finally:
            if os.path.lexists(temp):
                shutil.rmtree(temp, ignore_errors=True)
    if not is_backup_dir(target_dir):
        raise RuntimeError(_("“{path}” already exists and is not a backup directory of "
                             "gpxfoto").format(path=target_dir))
    return target_dir


def _back_up(path, directory, original, access_ns, checksum):
    """Make sure BACKUP_DIR holds a copy of the photo as it is now.

    The first copy is the true original, so it is never replaced. As
    gpxfoto changes only metadata, an earlier copy holds the same image;
    anything else in its place stops the write. A new copy is checked
    against the photo and gets its final name only once complete, so an
    interrupted copy never looks like a finished one.
    """
    target_dir = _backup_dir(directory)
    target = os.path.join(target_dir, os.path.basename(path))
    if os.path.lexists(target):
        _check_backup(target, checksum)
        return
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".jpg", dir=target_dir)
    try:
        os.close(fd)
        shutil.copyfile(path, temp)
        if image_checksum(temp) != checksum:
            raise RuntimeError(_("the backup copy differs from the photo"))
        _copy_attributes(path, original, access_ns, temp)
        _sync(temp)
        try:
            os.link(temp, target)       # unlike a rename, never replaces a file
        except FileExistsError:
            _check_backup(target, checksum)          # made meanwhile by another run
        except OSError:
            # No hard links on this file system, as on FAT memory cards
            if os.path.lexists(target):
                _check_backup(target, checksum)
            else:
                os.replace(temp, target)
        _sync_directory(target_dir)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _check_backup(target, checksum):
    """An existing backup must be a regular file holding the photo's image."""
    try:
        same = (not os.path.islink(target) and os.path.isfile(target)
                and image_checksum(target) == checksum)
    except (OSError, ValueError):
        same = False
    if not same:
        raise RuntimeError(_("“{path}” already exists and is not a copy of this photo").format(
            path=target))
