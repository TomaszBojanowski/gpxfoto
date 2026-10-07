"""Writing the location with exiftool while checking that the image is untouched."""
import hashlib
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
            data = f.read(length - 2)
            if not metadata:
                digest.update(header)
                digest.update(data)


def write_location(path, lat, lon, ele, time_utc, backup):
    before = image_checksum(path)
    directory = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".jpg", dir=directory)
    os.close(fd)
    os.unlink(temp)                  # exiftool -o requires that the file does not exist
    try:
        command = [
            "exiftool", "-q", "-n", "-m",
            f"-GPSLatitude={abs(lat):.8f}", f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-GPSLongitude={abs(lon):.8f}", f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
            f"-GPSDateStamp={time_utc:%Y:%m:%d}", f"-GPSTimeStamp={time_utc:%H:%M:%S}",
            "-GPSMapDatum=WGS-84",
        ]
        if ele is not None:
            command += [f"-GPSAltitude={abs(ele):.1f}", f"-GPSAltitudeRef={0 if ele >= 0 else 1}"]
        command += ["-o", temp, "--", path]
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode != 0 or not os.path.exists(temp):
            raise RuntimeError(process.stderr.strip() or _("exiftool did not write the file"))
        if image_checksum(temp) != before:
            raise RuntimeError(_("exiftool changed the image data; the result was discarded"))
        stat = os.stat(path)
        shutil.copymode(path, temp)
        os.utime(temp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        if backup:
            _back_up(path, directory)
        os.replace(temp, path)       # atomic replacement
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _back_up(path, directory):
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
        shutil.copy2(path, temp)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
