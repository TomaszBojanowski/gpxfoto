"""Writing the location with exiftool while checking that the image is untouched."""
import hashlib
import os
import shutil
import subprocess
import tempfile


def image_checksum(path):
    """SHA-256 of everything except metadata segments (APPn, COM).

    Covers the quantization and Huffman tables, the frame headers and the
    whole compressed image stream up to the end of the file.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            raise ValueError("to nie jest plik JPEG")
        while True:
            header = f.read(4)
            if len(header) < 4 or header[0] != 0xFF:
                raise ValueError("uszkodzona struktura JPEG")
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
    fd, temp = tempfile.mkstemp(prefix=".gpxfoto-", suffix=".jpg", dir=directory)
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
            raise RuntimeError(process.stderr.strip() or "exiftool nie zapisał pliku")
        if image_checksum(temp) != before:
            raise RuntimeError("dane obrazu różnią się po zapisie — zmiana odrzucona")
        stat = os.stat(path)
        shutil.copymode(path, temp)
        os.utime(temp, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        if backup:
            target = os.path.join(directory, "oryginaly")
            os.makedirs(target, exist_ok=True)
            shutil.copy2(path, os.path.join(target, os.path.basename(path)))
        os.replace(temp, path)       # atomic replacement
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
