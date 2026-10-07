"""Writing the location with exiftool while checking that the image is untouched."""
import hashlib
import math
import os
import shutil
import subprocess
import tempfile
from gettext import gettext as _

# Subdirectory for copies of the original files
BACKUP_DIR = "originals"
# Name prefix of temporary files, which a crash could leave behind
TEMP_PREFIX = ".gpxfoto-"


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


def write_location(path, lat, lon, ele, time_utc, backup):
    # Comparisons with NaN are false, so this also rules out NaN and infinity
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and (ele is None or math.isfinite(ele))):
        location = f"{lat}, {lon}" if ele is None else f"{lat}, {lon}, {ele} m"
        raise ValueError(_("invalid location: {location}").format(location=location))
    # Through a symbolic link, the file it points to is written
    path = os.path.realpath(path)
    # Taken before anything reads the photo, which may update its access time
    original = os.stat(path)
    if original.st_nlink > 1:
        # A new file replaces the photo, which would separate the links
        raise RuntimeError(_("the file has several hard links; writing it would separate them"))
    before = image_checksum(path)
    directory = os.path.dirname(path)
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".jpg", dir=directory)
    os.close(fd)
    os.unlink(temp)                  # exiftool -o requires that the file does not exist
    try:
        command = [
            "exiftool", "-q", "-n", "-m",
            # Old GPS data, also in XMP, must not stay next to the new location
            "-GPS:all=", "-XMP-exif:GPS*=",
            f"-GPSLatitude={abs(lat):.8f}", f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-GPSLongitude={abs(lon):.8f}", f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
            f"-GPSDateStamp={time_utc:%Y:%m:%d}", f"-GPSTimeStamp={time_utc:%H:%M:%S}",
            "-GPSMapDatum=WGS-84",
        ]
        if ele is not None:
            command += [f"-GPSAltitude={abs(ele):.1f}", f"-GPSAltitudeRef={0 if ele >= 0 else 1}"]
        command += ["-o", temp, "--", path]
        process = subprocess.run(command, capture_output=True, text=True, errors="replace")
        if process.returncode != 0 or not os.path.exists(temp):
            raise RuntimeError(process.stderr.strip() or _("exiftool did not write the file"))
        if image_checksum(temp) != before:
            raise RuntimeError(_("exiftool changed the image data; the result was discarded"))
        _copy_attributes(path, original, temp)
        if backup:
            _back_up(path, directory, original)
        os.replace(temp, path)       # atomic replacement
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _copy_attributes(path, original, target):
    """Give target the owner, permissions, extended attributes and times of the photo.

    Extended attributes include access control lists. The owner and group
    are copied as far as the system allows.
    """
    try:
        os.chown(target, original.st_uid, original.st_gid)
    except OSError:
        try:
            os.chown(target, -1, original.st_gid)    # allowed for members of the group
        except OSError:
            pass
    shutil.copystat(path, target)
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))


def _back_up(path, directory, original):
    """Copy the photo into BACKUP_DIR unless a copy is already there.

    The first copy is the true original, so it is never replaced. The copy
    gets its final name only once complete, so an interrupted copy never
    looks like a finished one.
    """
    target_dir = os.path.join(directory, BACKUP_DIR)
    target = os.path.join(target_dir, os.path.basename(path))
    if os.path.lexists(target):
        return
    os.makedirs(target_dir, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".jpg", dir=target_dir)
    os.close(fd)
    try:
        shutil.copyfile(path, temp)
        _copy_attributes(path, original, temp)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
