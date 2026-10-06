"""Shared helpers: small synthetic JPEG and GPX files and running the CLI."""
import os
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
GARMIN_EXTENSION_NAMESPACE = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"

needs_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None,
                                    reason="exiftool is not installed")


def _segment(marker, data):
    return bytes([0xFF, marker]) + struct.pack(">H", len(data) + 2) + data


def make_jpeg(width=64, height=48, quantization=None, comment=None):
    """Return a small, valid, uniformly grey baseline JPEG.

    Both Huffman tables hold a single one-bit code, so every 8×8 block is
    coded as "DC difference 0, end of block". The quantization table can
    be changed to get files that differ in image data but not in pixels.
    """
    blocks = ((width + 7) // 8) * ((height + 7) // 8)
    bits = "00" * blocks
    bits += "1" * (-len(bits) % 8)
    scan = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    scan = scan.replace(b"\xff", b"\xff\x00")
    huffman = bytes([1] + [0] * 15) + b"\x00"
    table = bytes(quantization or range(1, 65))
    parts = [
        b"\xff\xd8",
        _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"),
    ]
    if comment is not None:
        parts.append(_segment(0xFE, comment))
    parts += [
        _segment(0xDB, b"\x00" + table),
        _segment(0xC0, b"\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00"),
        _segment(0xC4, b"\x00" + huffman),
        _segment(0xC4, b"\x10" + huffman),
        _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00"),
        scan,
        b"\xff\xd9",
    ]
    return b"".join(parts)


def set_tags(path, *assignments):
    """Write tags with exiftool, e.g. set_tags(p, "-DateTimeOriginal=...")."""
    subprocess.run(["exiftool", "-q", "-overwrite_original", *assignments, "--", str(path)],
                   check=True)


def read_tags(path, *names):
    """Read tags with exiftool as numbers; returns a dict."""
    import json
    process = subprocess.run(["exiftool", "-json", "-n", *("-" + n for n in names),
                              "--", str(path)], check=True, capture_output=True, text=True)
    result = json.loads(process.stdout)[0]
    result.pop("SourceFile", None)
    return result


def write_gpx(path, points, namespace=GPX_NAMESPACE, garmin=False):
    """Write a GPX file with one track segment.

    points is a list of (time_text, lat, lon, elevation) where time_text
    is written as is (None leaves the point without a time) and
    elevation may be None.
    """
    attributes = f' xmlns="{namespace}"' if namespace else ""
    if garmin:
        attributes += f' xmlns:ns3="{GARMIN_EXTENSION_NAMESPACE}"'
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<gpx creator="Garmin Connect" version="1.1"{attributes}>',
             "<trk><name>Test</name><trkseg>"]
    for time_text, lat, lon, ele in points:
        inner = ""
        if ele is not None:
            inner += f"<ele>{ele}</ele>"
        if time_text is not None:
            inner += f"<time>{time_text}</time>"
        if garmin:
            inner += ("<extensions><ns3:TrackPointExtension><ns3:hr>120</ns3:hr>"
                      "</ns3:TrackPointExtension></extensions>")
        lines.append(f'<trkpt lat="{lat}" lon="{lon}">{inner}</trkpt>')
    lines.append("</trkseg></trk></gpx>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def run_cli(*args, cwd=None, env=None):
    """Run "python -m gpxfoto" in English with a fixed time zone."""
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("LC_", "LANG"))}
    environment.update({"LC_ALL": "C.UTF-8", "TZ": "Europe/Warsaw", "PYTHONPATH": ROOT,
                        "COLUMNS": "200"})
    environment.update(env or {})
    return subprocess.run([sys.executable, "-m", "gpxfoto", *map(str, args)],
                          cwd=cwd, env=environment, capture_output=True, text=True)


@pytest.fixture
def jpeg_file(tmp_path):
    """Factory creating a synthetic JPEG file in tmp_path."""
    def create(name="photo.jpg", **kwargs):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_jpeg(**kwargs))
        return path
    return create
