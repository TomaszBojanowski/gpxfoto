"""The third-party files in gpxfoto/web/vendor are unchanged from upstream."""
import hashlib
import os
import re

from conftest import ROOT

VENDOR = os.path.join(ROOT, "gpxfoto", "web", "vendor")


def test_vendored_files_match_their_checksums():
    with open(os.path.join(VENDOR, "README.md"), encoding="utf-8") as f:
        listed = re.findall(r"`([\w.-]+)`: ([0-9a-f]{64})", f.read())
    assert len(listed) == 3
    for name, checksum in listed:
        with open(os.path.join(VENDOR, "maplibre-gl", name), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == checksum, name


def test_vendored_code_comes_with_its_license():
    with open(os.path.join(VENDOR, "maplibre-gl", "LICENSE.txt"), encoding="utf-8") as f:
        assert f.read().startswith("Copyright (c) 2023, MapLibre contributors")
