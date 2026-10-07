"""image_checksum: SHA-256 of a JPEG without its metadata segments."""
import hashlib
import struct

import pytest

from conftest import make_jpeg, needs_exiftool, read_tags, set_tags
from gpxfoto.engine.writer import image_checksum

ORIGINAL = make_jpeg()
SOS = ORIGINAL.index(b"\xff\xda")
SCAN = SOS + 10                                 # first byte after the scan header


@pytest.fixture(autouse=True)
def english(monkeypatch):
    # Error texts are compared exactly; make sure no catalogue translates them.
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def checksum(tmp_path):
    """Checksum of the given bytes, written to a file first."""
    def compute(data):
        path = tmp_path / "photo.jpg"
        path.write_bytes(data)
        return image_checksum(path)
    return compute


def segment(marker, data):
    return bytes([0xFF, marker]) + struct.pack(">H", len(data) + 2) + data


def insert(data, position, extra):
    return data[:position] + extra + data[position:]


def flip(data, position):
    return data[:position] + bytes([data[position] ^ 0x01]) + data[position + 1:]


def test_hashes_everything_after_soi_except_metadata(checksum):
    data = make_jpeg(comment=b"note")
    # APP0 (JFIF) and COM come first; the image starts at the quantization table.
    image = data[data.index(b"\xff\xdb"):]
    assert checksum(data) == hashlib.sha256(image).hexdigest()


# Metadata segments are ignored

@pytest.mark.parametrize("marker", [*range(0xE0, 0xF0), 0xFE], ids=hex)
def test_added_metadata_segment_is_ignored(checksum, marker):
    data = insert(ORIGINAL, 2, segment(marker, b"metadata"))
    assert checksum(data) == checksum(ORIGINAL)


def test_metadata_segment_between_image_segments_is_ignored(checksum):
    data = insert(ORIGINAL, SOS, segment(0xE1, b"Exif\x00\x00late"))
    assert checksum(data) == checksum(ORIGINAL)


def test_metadata_segment_is_skipped_by_its_length(checksum):
    # Marker-like bytes inside a segment must not be taken for markers.
    fake_markers = b"\xff\xd9\xff\xda\x00\x08\xff\xd8\xff"
    data = insert(ORIGINAL, 2, segment(0xE1, fake_markers))
    assert checksum(data) == checksum(ORIGINAL)


def test_largest_possible_metadata_segment_is_ignored(checksum):
    data = insert(ORIGINAL, 2, segment(0xE2, b"\xff" * 65533))
    assert checksum(data) == checksum(ORIGINAL)


def test_removed_metadata_segment_is_ignored(checksum):
    without_jfif = ORIGINAL[:2] + ORIGINAL[ORIGINAL.index(b"\xff\xdb"):]
    assert checksum(without_jfif) == checksum(ORIGINAL)


def test_changed_metadata_segment_is_ignored(checksum):
    changed = ORIGINAL.replace(b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01",
                               b"JFIF\x00\x01\x02\x01\x01\x2c\x01\x2c")
    assert changed != ORIGINAL
    assert checksum(changed) == checksum(ORIGINAL)


def test_comments_are_ignored(checksum):
    assert (checksum(make_jpeg(comment=b"first"))
            == checksum(make_jpeg(comment=b"a different, longer comment"))
            == checksum(make_jpeg()))


@needs_exiftool
def test_exiftool_metadata_writes_are_ignored(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(ORIGINAL)
    before = image_checksum(path)
    set_tags(path, "-Make=Panasonic", "-Model=DC-S5M2",
             "-DateTimeOriginal=2026:06:01 12:00:00", "-GPSLatitude=50.06",
             "-GPSLatitudeRef=N", "-XMP:Title=Kraków", "-IPTC:Keywords=test",
             "-Comment=hello")
    assert read_tags(path, "Make", "Title", "Comment") == {
        "Make": "Panasonic", "Title": "Kraków", "Comment": "hello"}
    assert path.read_bytes() != ORIGINAL
    assert image_checksum(path) == before

    set_tags(path, "-all=")
    assert image_checksum(path) == before


# Everything else is covered

@pytest.mark.parametrize("change", [
    pytest.param(lambda d: flip(d, d.index(b"\xff\xdb") + 68), id="quantization value"),
    pytest.param(lambda d: flip(d, d.index(b"\xff\xdb") + 4), id="quantization table id"),
    pytest.param(lambda d: flip(d, d.index(b"\xff\xc0") + 1), id="frame type"),
    pytest.param(lambda d: flip(d, d.index(b"\xff\xc0") + 6), id="frame height"),
    pytest.param(lambda d: flip(d, d.index(b"\xff\xc4") + 21), id="dc huffman symbol"),
    pytest.param(lambda d: flip(d, d.rindex(b"\xff\xc4") + 4), id="ac huffman class"),
    pytest.param(lambda d: flip(d, SOS + 9), id="scan header"),
    pytest.param(lambda d: flip(d, SCAN), id="first scan byte"),
    pytest.param(lambda d: flip(d, len(d) - 3), id="last scan byte"),
    pytest.param(lambda d: d[:-3] + d[-2:], id="scan byte removed"),
    pytest.param(lambda d: flip(d, len(d) - 1), id="end of image marker"),
    pytest.param(lambda d: d + b"trailer", id="bytes after eoi"),
    pytest.param(lambda d: d.replace(segment(0xDB, b"\x00" + bytes(range(1, 65))), b""),
                 id="quantization table removed"),
])
def test_change_in_image_data_is_detected(checksum, change):
    changed = change(ORIGINAL)
    assert changed != ORIGINAL
    assert checksum(changed) != checksum(ORIGINAL)


def test_different_quantization_table_is_detected(checksum):
    assert checksum(make_jpeg(quantization=range(2, 66))) != checksum(make_jpeg())


def test_different_frame_header_is_detected(checksum):
    # 47 and 48 rows need the same number of blocks, so only the SOF differs.
    shorter = make_jpeg(height=47)
    assert len(shorter) == len(ORIGINAL)
    assert checksum(shorter) != checksum(ORIGINAL)


def test_trailer_after_eoi_is_covered(checksum):
    assert checksum(ORIGINAL + b"one") != checksum(ORIGINAL + b"two")


@pytest.mark.parametrize("marker", [0xDD, 0xDF, 0xF0, 0xFD], ids=hex)
def test_non_metadata_segment_is_covered(checksum, marker):
    data = insert(ORIGINAL, 2, segment(marker, b"\x00\x10"))
    assert checksum(data) != checksum(ORIGINAL)


# Invalid files

@pytest.mark.parametrize("data", [
    pytest.param(b"", id="empty"),
    pytest.param(b"\xff", id="one byte"),
    pytest.param(b"\x89PNG\r\n\x1a\n" + bytes(32), id="png"),
    pytest.param(b"GIF89a" + bytes(32), id="gif"),
    pytest.param(b"\xd8\xff" + ORIGINAL[2:], id="swapped soi"),
    pytest.param(b"\x00" + ORIGINAL, id="leading byte"),
])
def test_non_jpeg_file_raises(checksum, data):
    with pytest.raises(ValueError) as error:
        checksum(data)
    assert str(error.value) == "not a JPEG file"


@pytest.mark.parametrize("data", [
    pytest.param(ORIGINAL[:2], id="soi only"),
    pytest.param(b"\xff\xd8\xff\xd9", id="soi eoi"),
    pytest.param(ORIGINAL[:30], id="cut in quantization table"),
    pytest.param(ORIGINAL[:SOS], id="cut before scan"),
    pytest.param(ORIGINAL[:SOS + 3], id="cut in segment header"),
    pytest.param(ORIGINAL[:20] + b"\x00" + ORIGINAL[21:], id="marker without ff"),
    pytest.param(ORIGINAL[:2] + b"\xff\xe0\x00\x11" + ORIGINAL[6:], id="length too long"),
    pytest.param(ORIGINAL[:2] + b"\xff\xe0\x00\x0f" + ORIGINAL[6:], id="length too short"),
    pytest.param(ORIGINAL[:2] + b"\xff\xe1\xff\xff" + bytes(10), id="length past end"),
])
def test_damaged_structure_raises(checksum, data):
    with pytest.raises(ValueError) as error:
        checksum(data)
    assert str(error.value) == "damaged JPEG structure"


@pytest.mark.parametrize("marker", [b"\xff\xe0", b"\xff\xdb"])
@pytest.mark.parametrize("length", [0, 1])
def test_segment_length_below_two_raises(checksum, marker, length):
    with pytest.raises(ValueError) as error:
        checksum(ORIGINAL[:2] + marker + struct.pack(">H", length) + ORIGINAL[6:])
    assert str(error.value) == "damaged JPEG structure"


def test_segment_longer_than_the_file_raises(checksum):
    with pytest.raises(ValueError) as error:
        checksum(ORIGINAL[:2] + b"\xff\xe1" + struct.pack(">H", 60000) + b"Exif")
    assert str(error.value) == "damaged JPEG structure"


# Scans larger than one read block

CHUNK = 1 << 20
LARGE = ORIGINAL[:SCAN] + bytes(range(256)) * (10 * 1024) + ORIGINAL[-2:]   # 2.5 MiB scan


def test_large_scan_is_hashed_completely(checksum):
    image = LARGE[LARGE.index(b"\xff\xdb"):]
    assert checksum(LARGE) == hashlib.sha256(image).hexdigest()


@pytest.mark.parametrize("position", [
    pytest.param(SCAN, id="first byte"),
    pytest.param(SCAN + CHUNK - 1, id="end of first block"),
    pytest.param(SCAN + CHUNK, id="start of second block"),
    pytest.param(SCAN + 2 * CHUNK + 1, id="third block"),
    pytest.param(len(LARGE) - 3, id="last scan byte"),
])
def test_change_in_large_scan_is_detected(checksum, position):
    assert checksum(flip(LARGE, position)) != checksum(LARGE)
