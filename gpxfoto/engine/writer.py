"""Writing the location with exiftool while checking that the image is untouched."""
import errno
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
# The GPS fields of the direction of travel, never of the camera
TRAVEL_TAGS = ("GPSTrack", "GPSTrackRef")


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


def write_location(path, lat, lon, ele, time_utc, backup, replace=False, seen=None,
                   direction=None, clear_direction=False):
    """Write a location into the photo at path, changing nothing else.

    replace means the photo already has a location. All its GPS data, also
    in XMP, then belongs to the old location and is removed. Otherwise only
    the fields of a location are written anew, and other GPS data, such as
    a compass direction, is kept.

    direction is the direction of travel in whole degrees from true north,
    written as GPSTrack with GPSTrackRef T. When direction is given or
    clear_direction is set, any direction of travel the photo had, also in
    XMP, is removed first: it belonged to another location or another
    program. Otherwise it is left as it is, unless replace removes it with
    the rest of the old GPS data.

    seen is os.stat() of the photo from before its metadata was read: the
    photo must not have changed since, and its access time is kept.

    time_utc is the time of the location, written as GPSDateStamp and
    GPSTimeStamp. It is None for a location placed by hand on a photo
    without a capture time: these fields are then removed, like the rest
    of the old location, and not written. A GPS time in XMP is then
    removed too, as it would be taken for the time of the new location.

    Returns os.stat() of the written photo, or None when another program
    changed it right after it was replaced.
    """
    # Comparisons with NaN are false, so this also rules out NaN and infinity
    if not (-90 <= lat <= 90 and -180 <= lon <= 180
            and (ele is None or abs(ele) <= MAX_ELEVATION)):
        location = f"{lat}, {lon}" if ele is None else f"{lat}, {lon}, {ele} m"
        raise ValueError(_("invalid location: {location}").format(location=location))
    # exiftool would write 360 or infinity as given, and for -1 only warn
    if direction is not None and not (type(direction) is int and 0 <= direction < 360):
        # Translators: {direction} is the value that cannot be written
        raise ValueError(_("invalid direction of travel: {direction}").format(
            direction=direction))
    if replace:
        arguments = ["-GPS:all=", "-XMP-exif:GPS*="]
    else:
        arguments = [f"-GPS:{tag}=" for tag in LOCATION_TAGS]
    # The group is named, so that exiftool does not also write these
    # values, without their N/S and E/W, into GPS tags found in XMP.
    arguments += [
        f"-GPS:GPSLatitude={abs(lat):.8f}", f"-GPS:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
        f"-GPS:GPSLongitude={abs(lon):.8f}", f"-GPS:GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
        "-GPS:GPSMapDatum=WGS-84",
    ]
    if time_utc is not None:
        arguments += [f"-GPS:GPSDateStamp={time_utc.year:04}:{time_utc:%m:%d}",
                      f"-GPS:GPSTimeStamp={time_utc:%H:%M:%S}"]
    elif not replace:
        arguments += ["-XMP-exif:GPSDateTime="]
    if ele is not None:
        arguments += [f"-GPS:GPSAltitude={abs(ele):.1f}",
                      f"-GPS:GPSAltitudeRef={0 if ele >= 0 else 1}"]
    if (direction is not None or clear_direction) and not replace:
        arguments += [f"-{group}:{tag}=" for group in ("GPS", "XMP-exif")
                      for tag in TRAVEL_TAGS]
    if direction is not None:
        arguments += [f"-GPS:GPSTrack={direction}", "-GPS:GPSTrackRef=T"]
    # Through a symbolic link, the file it points to is written
    path = os.path.realpath(path)
    return _rewrite(path, _exiftool(arguments, path), backup, seen)


def metadata_head(path):
    """What undoing a write of the photo at path needs: (head, digest).

    head is the start of the file up to its image data, which holds all
    its metadata; digest is the SHA-256 of the whole file. A write changes
    only the head, so the head and the image data of the written photo
    make up the photo as it is now, to the byte.
    """
    with open(path, "rb") as f:
        head = f.read(_image_start(f))
        digest = hashlib.sha256(head)
        while block := f.read(1 << 20):
            digest.update(block)
    return head, digest.hexdigest()


def restore_head(path, head, digest, seen=None):
    """Put back the head that metadata_head() gave, before the photo at path was written.

    seen is os.stat() of the photo as the write left it; it must not have
    changed since, and its access time is kept. The result must be the
    photo from before the write to the byte, with the SHA-256 digest, or
    nothing is changed. It replaces the photo and returns as write_location() does.
    """
    path = os.path.realpath(path)

    def make(temp):
        result = hashlib.sha256(head)
        with open(path, "rb") as source, \
                open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            source.seek(_image_start(source))
            f.write(head)
            while block := source.read(1 << 20):
                result.update(block)
                f.write(block)
        if result.hexdigest() != digest:
            raise RuntimeError(_("the photo cannot be restored to the byte; it was left "
                                 "as it is"))

    return _rewrite(path, make, False, seen)


def _image_start(f):
    """Where the image data of the JPEG file f starts: its SOS segment."""
    f.seek(0)
    if f.read(2) != b"\xff\xd8":
        raise ValueError(_("not a JPEG file"))
    while True:
        header = f.read(4)
        if len(header) < 4 or header[0] != 0xFF:
            raise ValueError(_("damaged JPEG structure"))
        if header[1] == 0xDA:
            position = f.tell() - 4
            f.seek(0)
            return position
        length = int.from_bytes(header[2:4], "big")
        if length < 2:
            raise ValueError(_("damaged JPEG structure"))
        f.seek(length - 2, os.SEEK_CUR)


def _exiftool(arguments, path):
    """make() for _rewrite(): exiftool with arguments writes the result."""
    def make(temp):
        command = ["exiftool", "-q", "-n", "-m", *arguments, "-o", temp, "--", path]
        # Also readable only by its owner until it gets the photo's permissions
        process = subprocess.run(command, capture_output=True, text=True, errors="replace",
                                 umask=0o077)
        if process.returncode != 0 or not os.path.exists(temp):
            raise RuntimeError(process.stderr.strip() or _("exiftool did not write the file"))
    return make


def _rewrite(path, make, backup, seen):
    """Replace the photo at path with what make(temp) writes to the file temp.

    The result must hold the same image; it gets the photo's attributes
    and replaces it atomically. Returns os.stat() of the new photo, or None
    when another program changed it right after the replacement.
    """
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
    # The result is made in a directory only its owner can enter: in a
    # directory with a default access control list, a new file can be
    # readable by others whatever its permissions
    temp_dir = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=directory)
    try:
        # Such a list can also keep the owner out of a new directory
        os.chmod(temp_dir, 0o700)
        temp = os.path.join(temp_dir, os.path.basename(path))
        make(temp)
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
        result = os.stat(temp)
        os.replace(temp, path)       # atomic replacement
        _sync_directory(directory)
        written = os.stat(path)
        # A change by another program right after would be lost by undoing this
        if (written.st_ino, written.st_size, written.st_mtime_ns) != (
                result.st_ino, result.st_size, result.st_mtime_ns):
            return None
        return written
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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


# Errors meaning that a directory cannot be synced on this system or file
# system, or by this user, rather than that syncing it failed
CANNOT_SYNC_DIRECTORY = {errno.EACCES, errno.EPERM, errno.EBADF, errno.EINVAL, errno.ENOTSUP,
                         errno.EOPNOTSUPP, errno.ENOSYS}


def _sync_directory(directory):
    """Wait until the names in directory are on the disk, where the system allows it."""
    try:
        _sync(directory)
    except OSError as e:
        if e.errno not in CANNOT_SYNC_DIRECTORY:
            raise


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
            # A default access control list of directory can keep the owner
            # out of a new directory; nobody else loses access by this
            os.chmod(temp, stat.S_IMODE(os.stat(temp).st_mode) | stat.S_IRWXU)
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
