"""The state of the browser interface and its work in the background
(gpxfoto.server.session), with the page's requests (gpxfoto.server.api)."""
import base64
import json
from datetime import datetime, timedelta, timezone
import math
import os
import threading
import time

import pytest

from conftest import (
    hike_gpx, make_jpeg, needs_exiftool, read_tags, set_panasonic_time_stamp, set_tags, write_gpx)
from gpxfoto.engine.writer import image_checksum
from gpxfoto.server import session as session_module
from gpxfoto.server import writing as writing_module
from gpxfoto.server.session import Session, SessionError
from test_checks import STOP_MINUTES, stops_hike
from test_stops import T0 as HIKE_T0

TRACK = [("2024-05-01T10:00:00Z", 50.0, 20.0, 200.0),
         ("2024-05-01T10:01:40Z", 50.001, 20.002, 210.0)]


class Recorder:
    """Takes the place of Events: keeps what is published, for waiting on."""

    def __init__(self):
        self.published = []
        self.condition = threading.Condition()

    def publish(self, name, data):
        with self.condition:
            self.published.append((name, json.loads(json.dumps(data))))
            self.condition.notify_all()

    def wait(self, name, test=lambda data: True, timeout=20):
        """The data of the first event called name that passes test."""
        seen = 0
        with self.condition:
            while True:
                for event, data in self.published[seen:]:
                    if event == name and test(data):
                        return data
                seen = len(self.published)
                if not self.condition.wait(timeout):
                    raise AssertionError(f"no {name} event: {self.published}")

    def names(self):
        with self.condition:
            return [name for name, _data in self.published]


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    """Recent folders go to tmp_path, never into the real settings."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(session_module.files.sys, "platform", "linux")


@pytest.fixture
def events():
    return Recorder()


@pytest.fixture
def session(events):
    session = Session(events)
    yield session
    session.close()


def photo(directory, name, local_time="12:00:50", offset="+02:00", *tags):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_jpeg())
    set_tags(path, f"-DateTimeOriginal=2024:05:01 {local_time}",
             *([f"-OffsetTimeOriginal={offset}"] if offset else []), *tags)
    return path


def matches_for(events, correction=0.0, generation=None, tracks=None):
    return events.wait("matches", lambda d: d["correction"] == correction
                       and (generation is None or d["generation"] == generation)
                       and (tracks is None or d["tracks"] == tracks)
                       and d["results"])


@needs_exiftool
def test_photos_come_in_batches_as_they_are_read(session, events, tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "PHOTO_BATCH", 2)
    for k in range(5):
        photo(tmp_path / "photos", f"p{k}.jpg", f"12:00:{10 + k}")
    photo(tmp_path / "photos" / "sub", "deep.jpg")
    generation = session.choose_photos(str(tmp_path / "photos"))
    assert events.wait("photos-reset")["folder"] == str(tmp_path / "photos")
    assert events.wait("photos-found")["total"] == 5
    batches = []
    done = events.wait("photos-done")
    assert done == {"generation": generation, "count": 5}
    batches = [data for name, data in events.published if name == "photos"]
    assert [len(b["photos"]) for b in batches] == [2, 2, 1]
    assert [b["done"] for b in batches] == [2, 4, 5]
    first = batches[0]["photos"][0]
    assert first == {"id": 0, "name": "p0.jpg", "taken": "2024-05-01T12:00:10.000+02:00",
                     "tz": "camera", "tz_note": None, "reason": None, "has_location": False,
                     "orientation": 1, "thumbnail": False}
    state = session.state()["photos"]
    assert (state["loading"], len(state["photos"])) == (False, 5)


@needs_exiftool
def test_photos_in_subfolders_too(session, events, tmp_path):
    photo(tmp_path / "photos", "a.jpg")
    photo(tmp_path / "photos" / "sub", "b.jpg")
    session.choose_photos(str(tmp_path / "photos"), recursive=True)
    assert events.wait("photos-done")["count"] == 2


def test_a_folder_that_does_not_exist(session, tmp_path):
    with pytest.raises(SessionError) as raised:
        session.choose_photos(str(tmp_path / "missing"))
    assert str(raised.value) == f"Not a folder: {tmp_path / 'missing'}"


@needs_exiftool
def test_photos_are_matched_on_the_track_and_follow_the_correction(session, events, tmp_path):
    photo(tmp_path / "photos", "a.jpg", "12:00:50")
    photo(tmp_path / "photos", "b.jpg", "12:30:00")
    photo(tmp_path / "photos", "nodate.jpg", "12:00:00", None, "-DateTimeOriginal=")
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(gpx)])
    track = events.wait("track")["track"]
    assert track["files"] == ["track.gpx"] and track["points"] == 2
    assert track["lines"] == [[[20.0, 50.0], [20.002, 50.001]]]
    events.wait("photos-done")
    match = events.wait("matches", lambda d: len(d["results"]) == 3)
    a, b, nodate = match["results"]
    assert (a["state"], a["lat"], a["lon"], a["ele"]) == ("matched", 50.0005, 20.001, 205.0)
    assert a["time"] == "2024-05-01T12:00:50.000+02:00" and a["files"] == ["track.gpx"]
    assert (b["state"], b["reason"]) == ("skipped", "28 min after the end of the track")
    assert (nodate["state"], nodate["reason"]) == ("skipped", "no capture time in EXIF")
    assert (match["matched"], match["skipped"]) == (1, 2)
    session.set_correction(-45)
    moved = matches_for(events, -45)["results"][0]
    assert (moved["lat"], moved["time"]) == (50.00005, "2024-05-01T12:00:05.000+02:00")
    assert session.state()["matches"]["correction"] == -45


@pytest.mark.parametrize("seconds", [math.nan, math.inf, 86401, -1e12])
def test_corrections_beyond_a_day_are_refused(session, seconds):
    with pytest.raises(SessionError):
        session.set_correction(seconds)


@needs_exiftool
def test_photos_with_a_location_are_skipped_unless_overwritten(session, events, tmp_path):
    photo(tmp_path / "photos", "a.jpg", "12:00:50", "+02:00", "-GPSLatitude=1",
          "-GPSLatitudeRef=N", "-GPSLongitude=2", "-GPSLongitudeRef=E")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    first = events.wait("matches", lambda d: d["results"] and d["tracks"] == 1)["results"][0]
    assert (first["state"], first["reason"]) == ("has_location", "already has a location")
    assert session.state()["photos"]["photos"][0]["has_location"] is True
    session.set_options(overwrite=True)
    second = events.wait("matches", lambda d: d["overwrite"])["results"][0]
    assert (second["state"], second["overwrites"]) == ("matched", True)


@needs_exiftool
def test_a_folder_of_tracks_reads_only_the_files_needed(session, events, tmp_path):
    tracks = tmp_path / "tracks"
    write_gpx(tracks.mkdir() or tracks / "day1.gpx", TRACK)
    write_gpx(tracks / "day2.gpx", [("2024-05-02T10:00:00Z", 51.0, 21.0, None),
                                    ("2024-05-02T10:01:40Z", 51.0, 21.001, None)])
    (tracks / "broken.gpx").write_text("<!DOCTYPE gpx><gpx>")
    photo(tmp_path / "photos", "a.jpg", "12:00:50")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_folder(str(tracks))
    assert events.wait("tracks-found")["total"] == 3
    result = events.wait("matches", lambda d: d["results"] and "lat" in d["results"][0])[
        "results"][0]
    assert result["files"] == ["day1.gpx"]
    loaded = [data["track"]["files"] for name, data in events.published if name == "track"]
    assert loaded == [["day1.gpx"]]
    errors = [data["message"] for name, data in events.published if name == "failure"]
    assert len(errors) == 1 and errors[0].startswith("Cannot read the GPX file ")
    session.set_correction(86400)
    matches_for(events, 86400)
    loaded = [data["track"]["files"] for name, data in events.published if name == "track"]
    assert loaded == [["day1.gpx"], ["day2.gpx"]]


@needs_exiftool
def test_dropped_track_files(session, events, tmp_path):
    text = write_gpx(tmp_path / "x.gpx", TRACK).read_bytes()
    session.drop_tracks([("../../etc/passwd", base64.b64encode(text).decode()),
                         ("Wycieczka 2024.GPX", base64.b64encode(text).decode())])
    track = events.wait("track")["track"]
    assert track["files"] == ["passwd.gpx", "Wycieczka 2024.GPX"]
    directory = session._upload_dir
    assert sorted(os.listdir(directory)) == ["Wycieczka 2024.GPX", "passwd.gpx"]
    assert os.stat(directory).st_mode & 0o077 == 0
    session.close()
    assert not os.path.exists(directory)


def test_dropped_files_must_be_base64(session):
    with pytest.raises(SessionError):
        session.drop_tracks([("a.gpx", "not base64!")])


def test_track_files_that_do_not_exist(session, tmp_path):
    with pytest.raises(SessionError) as raised:
        session.choose_track_files([str(tmp_path / "missing.gpx")])
    assert str(raised.value).startswith("No such file or directory: ")
    with pytest.raises(SessionError):
        session.choose_track_files([])


def test_a_track_file_that_cannot_be_read(session, events, tmp_path):
    (tmp_path / "broken.gpx").write_text("<gpx>")
    session.choose_track_files([str(tmp_path / "broken.gpx")])
    assert events.wait("failure")["message"].startswith("Cannot read the GPX file ")
    assert events.wait("tracks-done")
    assert session.state()["tracks"]["loading"] is False


@needs_exiftool
def test_new_photos_replace_the_old_ones(session, events, tmp_path):
    photo(tmp_path / "one", "a.jpg")
    photo(tmp_path / "two", "b.jpg")
    photo(tmp_path / "two", "c.jpg")
    session.choose_photos(str(tmp_path / "one"))
    second = session.choose_photos(str(tmp_path / "two"))
    events.wait("photos-done", lambda d: d["generation"] == second)
    state = session.state()["photos"]
    assert state["generation"] == second
    assert [p["name"] for p in state["photos"]] == ["b.jpg", "c.jpg"]


@needs_exiftool
def test_thumbnails(session, events, tmp_path):
    path = photo(tmp_path / "photos", "a.jpg")
    thumbnail = make_jpeg(16, 12)
    (tmp_path / "t.jpg").write_bytes(thumbnail)
    set_tags(path, f"-ThumbnailImage<={tmp_path / 't.jpg'}")
    photo(tmp_path / "photos", "b.jpg")
    generation = session.choose_photos(str(tmp_path / "photos"))
    events.wait("photos-done")
    assert [p["thumbnail"] for p in session.state()["photos"]["photos"]] == [True, False]
    assert session.thumbnail(generation, 0) == thumbnail
    assert session.thumbnail(generation, 0) == thumbnail       # from memory
    assert session.thumbnail(generation, 1) is None
    assert session.thumbnail(generation, 2) is None
    assert session.thumbnail(generation + 1, 0) is None


@needs_exiftool
def test_chosen_folders_are_remembered(session, events, tmp_path):
    (tmp_path / "photos").mkdir()
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(gpx)])
    assert session_module.files.recent() == {"photos": [str(tmp_path / "photos")],
                                             "tracks": [str(gpx)]}


@needs_exiftool
def test_turning_stops_off_reads_the_track_again(session, events, tmp_path):
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    events.wait("tracks-done")
    session.set_options(stops=False)
    assert events.names().count("tracks-reset") == 2
    assert session.state()["stops"] is False


# --- writing the locations ---------------------------------------------------

def ready_to_write(session, events, tmp_path, count=1):
    """Photos matched on the track; returns their paths and the match."""
    paths = [photo(tmp_path / "photos", f"p{k}.jpg", f"12:00:{10 + k}") for k in range(count)]
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    events.wait("photos-done")
    events.wait("tracks-done")
    match = events.wait("matches", lambda d: d["matched"] == count)
    return paths, match


class Gate:
    """Takes the place of write_location: each call waits until let_through()."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.condition = threading.Condition()
        self.allowed = 0
        real = writing_module.write_location

        def write(path, *args, **kwargs):
            with self.condition:
                self.calls.append(path)
                self.condition.notify_all()
                while not self.allowed:
                    self.condition.wait()
                self.allowed -= 1
            real(path, *args, **kwargs)

        monkeypatch.setattr(writing_module, "write_location", write)

    def wait_for(self, count):
        with self.condition:
            assert self.condition.wait_for(lambda: len(self.calls) >= count, 20)

    def let_through(self, count=1):
        with self.condition:
            self.allowed += count
            self.condition.notify_all()


@needs_exiftool
def test_the_locations_are_written_and_the_photos_then_have_them(session, events, tmp_path):
    paths, match = ready_to_write(session, events, tmp_path, 3)
    checksums = [image_checksum(path) for path in paths]
    assert session.write(match["version"]) == 3
    assert events.wait("write-start") == {"kind": "write", "total": 3, "done": 0, "written": 0,
                                          "failed": 0, "cancelled": False}
    done = events.wait("write-done")
    assert done == {"kind": "write", "total": 3, "written": 3, "failed": [],
                    "cancelled": False, "not_written": 0}
    for path, checksum in zip(paths, checksums):
        assert image_checksum(path) == checksum
        tags = read_tags(path, "GPSLatitude", "GPSLongitude")
        assert tags["GPSLatitude"] == pytest.approx(50.0, abs=0.001)
    changed = events.wait("photos-changed")["photos"]
    assert [(p["id"], p["has_location"]) for p in changed] == [(0, True), (1, True), (2, True)]
    after = events.wait("matches", lambda d: d["version"] > match["version"])
    assert [r["state"] for r in after["results"]] == ["has_location"] * 3
    assert session.state()["writing"] is None


@needs_exiftool
def test_only_the_match_the_page_shows_is_written(session, events, tmp_path):
    paths, match = ready_to_write(session, events, tmp_path)
    session.set_correction(-5)
    newer = matches_for(events, -5)
    with pytest.raises(SessionError) as raised:
        session.write(match["version"])
    assert str(raised.value).startswith("The locations changed in the meantime.")
    session.set_correction(-6)
    with pytest.raises(SessionError):
        session.write(newer["version"])         # computed with another correction
    assert "write-start" not in events.names()
    assert "GPSLatitude" not in read_tags(paths[0], "GPSLatitude")


@needs_exiftool
def test_nothing_to_write(session, events, tmp_path):
    photo(tmp_path / "photos", "late.jpg", "15:00:00")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    events.wait("photos-done")
    match = events.wait("matches", lambda d: d["results"] and d["tracks"] == 1)
    with pytest.raises(SessionError) as raised:
        session.write(match["version"])
    assert str(raised.value) == "No photo has a location to write."


@needs_exiftool
def test_nothing_changes_while_writing_and_cancelling_finishes_the_photos_begun(
        session, events, tmp_path, monkeypatch):
    monkeypatch.setattr(writing_module, "PROGRESS_EVERY", 0)
    gate = Gate(monkeypatch)
    paths, match = ready_to_write(session, events, tmp_path, 6)
    writing = session.write(match["version"]) and session.writing
    threads = writing._threads
    gate.wait_for(threads)
    for change in (lambda: session.choose_photos(str(tmp_path / "photos")),
                   lambda: session.choose_track_files([str(tmp_path / "track.gpx")]),
                   lambda: session.set_correction(10),
                   lambda: session.set_options(overwrite=True),
                   lambda: session.write(match["version"])):
        with pytest.raises(SessionError) as raised:
            change()
        assert str(raised.value) == ("Wait until the locations are written, or cancel the "
                                     "writing.")
    assert session.state()["writing"]["done"] == 0
    session.cancel_write()
    assert events.wait("write-progress", lambda d: d["cancelled"])["done"] == 0
    gate.let_through(threads)
    done = events.wait("write-done")
    assert done == {"kind": "write", "total": 6, "written": threads, "failed": [],
                    "cancelled": True, "not_written": 6 - threads}
    written = [path for path in paths if "GPSLatitude" in read_tags(path, "GPSLatitude")]
    assert written == paths[:threads]
    assert sorted(gate.calls) == [str(p) for p in paths[:threads]]
    session.set_correction(10)                  # allowed again


@needs_exiftool
def test_closing_waits_for_the_photos_being_written(session, events, tmp_path, monkeypatch):
    monkeypatch.setattr(writing_module, "WRITE_THREADS", 1)
    gate = Gate(monkeypatch)
    paths, match = ready_to_write(session, events, tmp_path, 2)
    session.write(match["version"])
    gate.wait_for(1)
    closing = threading.Thread(target=session.close)
    closing.start()
    closing.join(0.5)
    assert closing.is_alive()
    gate.let_through(2)
    closing.join(20)
    assert not closing.is_alive()
    assert "GPSLatitude" in read_tags(paths[0], "GPSLatitude")
    assert "GPSLatitude" not in read_tags(paths[1], "GPSLatitude")
    assert events.wait("write-done")["not_written"] == 1


@needs_exiftool
def test_a_photo_changed_after_it_was_read_is_not_written(session, events, tmp_path):
    paths, match = ready_to_write(session, events, tmp_path, 2)
    set_tags(paths[1], "-Artist=someone else")
    session.write(match["version"])
    done = events.wait("write-done")
    assert done["written"] == 1
    assert done["failed"] == [{"id": 1, "name": "p1.jpg",
                               "message": "another program changed the photo in the meantime"}]
    assert "GPSLatitude" not in read_tags(paths[1], "GPSLatitude")
    changed = events.wait("photos-changed")["photos"]
    assert [p["id"] for p in changed] == [0]


# --- placing photos by hand ----------------------------------------------------

@needs_exiftool
def test_photos_placed_by_hand_are_written_there(session, events, tmp_path):
    photo(tmp_path / "photos", "a.jpg", "12:00:50")
    photo(tmp_path / "photos", "late.jpg", "15:00:00")
    photo(tmp_path / "photos", "nodate.jpg", "12:00:00", None, "-DateTimeOriginal=")
    photo(tmp_path / "photos", "located.jpg", "12:00:50", "+02:00", "-GPSLatitude=1",
          "-GPSLatitudeRef=N", "-GPSLongitude=2", "-GPSLongitudeRef=E",
          "-GPSDateStamp=2020:01:01", "-GPSTimeStamp=01:02:03")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    events.wait("photos-done")
    first = events.wait("matches", lambda d: d["results"] and d["tracks"] == 1 and d["matched"])
    generation = first["generation"]
    names = [p["name"] for p in session.state()["photos"]["photos"]]
    index = {name: names.index(name) for name in names}
    for name, lat in (("a.jpg", 51.5), ("late.jpg", 52.5), ("nodate.jpg", -33.25),
                      ("located.jpg", 10.0)):
        session.place(generation, index[name], lat, 21.25)
    match = events.wait("matches", lambda d: d["matched"] == 4)
    results = {names[r["id"]]: r for r in match["results"]}
    assert {name: (r["state"], r["lat"], r["ele"], r["overwrites"])
            for name, r in results.items()} == {
        "a.jpg": ("manual", 51.5, None, False), "late.jpg": ("manual", 52.5, None, False),
        "nodate.jpg": ("manual", -33.25, None, False),
        "located.jpg": ("manual", 10.0, None, True)}
    assert results["nodate.jpg"]["time"] is None
    # Back on the track
    session.place(generation, index["a.jpg"])
    match = events.wait("matches", lambda d: d["results"]
                        and d["results"][index["a.jpg"]]["state"] == "matched"
                        and d["matched"] == 4)
    assert match["results"][index["a.jpg"]]["lat"] == 50.0005
    session.write(match["version"])
    assert events.wait("write-done")["written"] == 4
    folder = tmp_path / "photos"
    assert read_tags(folder / "late.jpg", "GPSLatitude", "GPSDateStamp", "GPSTimeStamp") == {
        "GPSLatitude": 52.5, "GPSDateStamp": "2024:05:01", "GPSTimeStamp": "13:00:00"}
    assert read_tags(folder / "nodate.jpg", "GPSLatitude", "GPSLatitudeRef", "GPSDateStamp") == {
        "GPSLatitude": -33.25, "GPSLatitudeRef": "S"}
    assert read_tags(folder / "located.jpg", "GPSLatitude", "GPSDateStamp") == {
        "GPSLatitude": 10.0, "GPSDateStamp": "2024:05:01"}
    assert read_tags(folder / "a.jpg", "GPSLatitude") == {"GPSLatitude": 50.0005}
    # Written, the photos have their own locations, no longer placed by hand
    after = events.wait("matches", lambda d: d["version"] > match["version"])
    assert [r["state"] for r in after["results"]] == ["has_location"] * 4
    assert session.placed == {}


@pytest.mark.parametrize("lat, lon", [(91, 0), (0, 180.5), (math.nan, 0), (0, math.inf),
                                      (1, None), (None, 1)])
def test_places_off_the_earth_are_refused(session, lat, lon):
    with pytest.raises(SessionError):
        session.place(0, 0, lat, lon)


def test_only_photos_of_the_page_can_be_placed(session):
    with pytest.raises(SessionError) as raised:
        session.place(0, 0, 50, 20)
    assert str(raised.value) == "This photo is no longer among the chosen ones."


# --- undoing a write -------------------------------------------------------------

@needs_exiftool
def test_a_write_is_undone_to_the_byte(session, events, tmp_path):
    folder = tmp_path / "photos"
    photo(folder, "a.jpg", "12:00:10")
    photo(folder, "b.jpg", "12:00:20", "+02:00", "-GPSImgDirection=123.4",
          "-XMP-exif:GPSDateTime=2015:05:05 10:00:00Z")
    photo(folder, "c.jpg", "12:00:30", "+02:00", "-GPSLatitude=1", "-GPSLatitudeRef=S",
          "-GPSLongitude=2", "-GPSLongitudeRef=W", "-XMP-exif:GPSLatitude=1")
    paths = [folder / name for name in ("a.jpg", "b.jpg", "c.jpg")]
    before = [p.read_bytes() for p in paths]
    session.set_options(overwrite=True)
    session.choose_photos(str(folder))
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    events.wait("photos-done")
    match = events.wait("matches", lambda d: d["matched"] == 3 and d["overwrite"])
    with pytest.raises(SessionError) as raised:
        session.undo()
    assert str(raised.value) == "There is no write to undo."
    session.write(match["version"])
    assert events.wait("write-done")["written"] == 3
    assert events.wait("undo") == {"count": 3} and session.state()["undo"] == 3
    journals = list((tmp_path / "state" / "gpxfoto" / "journal").iterdir())
    assert len(journals) == 1
    assert os.stat(journals[0]).st_mode & 0o077 == 0
    assert os.stat(journals[0].parent).st_mode & 0o077 == 0
    assert [p.read_bytes() for p in paths] != before
    events.published.clear()
    assert session.undo() == 3
    assert events.wait("write-start")["kind"] == "undo"
    done = events.wait("write-done")
    assert done == {"kind": "undo", "total": 3, "written": 3, "failed": [], "cancelled": False,
                    "not_written": 0}
    assert [p.read_bytes() for p in paths] == before
    changed = events.wait("photos-changed")["photos"]
    assert [p["has_location"] for p in changed] == [False, False, True]
    assert events.wait("undo") == {"count": 0}
    assert list(journals[0].parent.iterdir()) == []
    after = events.wait("matches", lambda d: d["results"])
    assert [r["state"] for r in after["results"]] == ["matched", "matched", "matched"]


@needs_exiftool
def test_a_photo_changed_after_the_write_is_not_undone(session, events, tmp_path):
    paths, match = ready_to_write(session, events, tmp_path, 2)
    session.write(match["version"])
    events.wait("write-done")
    set_tags(paths[1], "-Artist=someone else")
    events.published.clear()
    session.undo()
    done = events.wait("write-done")
    assert done["written"] == 1
    # Undo ids follow the journal, in the order the photos were written
    assert [(f["name"], f["message"]) for f in done["failed"]] == [
        ("p1.jpg", "another program changed the photo in the meantime")]
    assert "GPSLatitude" not in read_tags(paths[0], "GPSLatitude")
    assert "GPSLatitude" in read_tags(paths[1], "GPSLatitude")
    # It can be tried again
    assert events.wait("undo") == {"count": 1}


# The track is at once 111 km further north: two photos ten seconds apart
# cannot have been taken that far from each other
LEAP = [(f"2024-05-01T10:{second // 60:02}:{second % 60:02}Z", 50.0 if second < 125 else 51.0,
         20.0, 200.0) for second in range(0, 300, 5)]


def leaping(session, events, tmp_path):
    """Two photos on either side of the leap, and the warnings about them."""
    paths = [photo(tmp_path / "photos", "a.jpg", "12:02:00"),
             photo(tmp_path / "photos", "b.jpg", "12:02:10")]
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(write_gpx(tmp_path / "leap.gpx", LEAP))])
    events.wait("photos-done")
    return paths, events.wait("warnings", lambda d: d["warnings"])


@needs_exiftool
def test_photos_placed_implausibly_far_apart_are_warned_of(session, events, tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("LANGUAGE", "C")
    _paths, warnings = leaping(session, events, tmp_path)
    match = events.wait("matches", lambda d: d["version"] == warnings["version"])
    assert match["matched"] == 2
    assert (warnings["generation"], warnings["tracks"]) == (match["generation"], match["tracks"])
    [card] = warnings["warnings"]
    assert card["kind"] == "jumps" and sorted(card["notes"]) == ["0", "1"]
    assert card["title"] == "Position jumps (1 pair)"
    assert [(item["photos"], item["text"]) for item in card["items"]] == [
        ([0, 1], "a → b · 10 s · 111.2 km")]
    # As the page gets them: the ids of the photos with notes are texts in JSON
    assert json.loads(json.dumps(session.state()["warnings"])) == warnings
    # With both photos before the leap, there is nothing to warn of
    session.set_correction(-10)
    calm = events.wait("warnings", lambda d: d["version"] > warnings["version"])
    assert calm["warnings"] == [] and session.state()["warnings"] == calm


@needs_exiftool
def test_a_photo_placed_by_hand_is_not_warned_of(session, events, tmp_path):
    _paths, warnings = leaping(session, events, tmp_path)
    session.place(warnings["generation"], 1, 10.0, 21.25)
    after = events.wait("warnings", lambda d: d["version"] > warnings["version"])
    assert after["warnings"] == []


@needs_exiftool
def test_warnings_of_other_photos_or_tracks_are_dropped(session, events, tmp_path):
    _paths, warnings = leaping(session, events, tmp_path)
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    assert session.state()["warnings"] is None or session.state()["warnings"]["tracks"] == 2
    calm = events.wait("warnings", lambda d: d["tracks"] == 2)
    assert calm["warnings"] == []
    session.choose_photos(str(tmp_path / "photos"))
    state = session.state()["warnings"]
    assert state is None or state["generation"] == session.photo_generation


@needs_exiftool
def test_warnings_do_not_stop_the_locations_from_being_written(session, events, tmp_path):
    paths, warnings = leaping(session, events, tmp_path)
    assert warnings["warnings"]
    assert session.write(warnings["version"]) == 2
    done = events.wait("write-done")
    assert (done["written"], done["failed"]) == (2, [])
    assert [read_tags(path, "GPSLatitude")["GPSLatitude"] for path in paths] == [
        pytest.approx(50.0), pytest.approx(51.0)]


@needs_exiftool
def test_a_clock_an_hour_behind_is_warned_of_and_the_shift_can_be_applied(session, events,
                                                                          tmp_path, monkeypatch):
    monkeypatch.setenv("LANGUAGE", "C")
    gpx = hike_gpx(tmp_path / "hike.gpx", stops_hike())
    for n, (minute, second) in enumerate((m, s) for m in STOP_MINUTES for s in (40, 80)):
        # The camera shows 11:20 when the watch shows 12:20
        taken = datetime.fromtimestamp(HIKE_T0 + minute * 60 + second - 3600,
                                       timezone(timedelta(hours=2)))
        photo(tmp_path / "photos", f"{n:02}.jpg", f"{taken:%H:%M:%S}", "+02:00",
              f"-DateTimeOriginal={taken:%Y:%m:%d %H:%M:%S}")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(gpx)])
    events.wait("photos-done")
    warnings = events.wait("warnings", lambda d: d["warnings"])
    [card] = warnings["warnings"]
    assert (card["kind"], card["title"]) == ("shift", "Shift the photo times by +1 h?")
    assert card["action"] == {"label": "Apply +1 h", "correction": 3600}
    # The button of the card sets the correction, as the slider does
    session.set_correction(card["action"]["correction"])
    match = matches_for(events, 3600)
    assert (match["matched"], match["at_stops"]) == (12, 12)
    calm = events.wait("warnings", lambda d: d["version"] == match["version"])
    assert calm["warnings"] == []


@pytest.fixture
def warsaw(monkeypatch):
    """The computer is in Europe/Warsaw, at UTC+02:00 in May."""
    monkeypatch.setenv("TZ", "Europe/Warsaw")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@needs_exiftool
def test_a_time_that_differs_from_the_camera_s_utc_time_is_warned_of(session, events, tmp_path,
                                                                     monkeypatch, warsaw):
    monkeypatch.setenv("LANGUAGE", "C")
    # No time zone in EXIF; the camera, at UTC+03:00, recorded 10:00:50 UTC
    path = photo(tmp_path / "photos", "a.jpg", "13:00:50", None)
    set_panasonic_time_stamp(path, "2024:05:01 10:00:50")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_files([str(write_gpx(tmp_path / "track.gpx", TRACK))])
    events.wait("photos-done")
    warnings = events.wait("warnings", lambda d: d["warnings"])
    [card] = warnings["warnings"]
    assert (card["kind"], card["title"]) == ("utc-system", "Camera’s UTC time differs (1)")
    assert card["notes"] == {"0": ["camera’s UTC time suggests +03:00"]}
    assert card["action"] == {"label": "Apply −1 h", "correction": -3600}
    match = events.wait("matches", lambda d: d["version"] == warnings["version"])
    assert match["results"][0]["state"] == "skipped"
    # The photo also says where its time zone comes from
    [summary] = session.state()["photos"]["photos"]
    assert (summary["tz"], summary["tz_note"]) == ("system", "computer’s time zone (not in EXIF)")
    session.set_correction(card["action"]["correction"])
    match = matches_for(events, -3600)
    assert (match["results"][0]["state"], match["results"][0]["lat"]) == ("matched", 50.0005)
    calm = events.wait("warnings", lambda d: d["version"] == match["version"])
    assert calm["warnings"] == []


@needs_exiftool
def test_a_photo_no_track_of_the_folder_covers_names_the_nearest(session, events, tmp_path,
                                                                 monkeypatch):
    monkeypatch.setenv("LANGUAGE", "C")
    tracks = tmp_path / "tracks"
    write_gpx(tracks.mkdir() or tracks / "day1.gpx", TRACK)
    write_gpx(tracks / "day2.gpx", [("2024-05-02T10:00:00Z", 51.0, 21.0, None),
                                    ("2024-05-02T10:01:40Z", 51.0, 21.001, None)])
    (tracks / "broken.gpx").write_text(
        "<gpx><trk><trkseg><trkpt lat='50' lon='20'><time>2024-05-02T09:55:00Z</time>")
    photo(tmp_path / "photos", "a.jpg", "12:00:50")
    photo(tmp_path / "photos", "b.jpg", "12:30:00")
    photo(tmp_path / "photos", "c.jpg", "11:50:00", "+02:00",
          "-DateTimeOriginal=2024:05:02 11:50:00")
    photo(tmp_path / "photos", "d.jpg", "12:00:00", "+02:00",
          "-DateTimeOriginal=2024:05:09 12:00:00")
    session.choose_photos(str(tmp_path / "photos"))
    session.choose_track_folder(str(tracks))
    events.wait("photos-done")
    match = events.wait("matches", lambda d: len(d["results"]) == 4)
    a, b, c, d = match["results"]
    assert (a["state"], a["files"]) == ("matched", ["day1.gpx"])
    assert (b["state"], b["reason"], b["files"]) == (
        "skipped", "28 min after the end of the nearest track", ["day1.gpx"])
    assert (c["state"], c["reason"], c["files"]) == (
        "skipped", "10 min before the start of the nearest track", ["day2.gpx"])
    # No track within a day: none is named
    assert d["state"] == "skipped" and d["files"] == [] and "nearest" not in d["reason"]
    # The track read to name it is shown like the others; the file that
    # cannot be read is told of once, however often the photos are matched
    session.set_correction(1)
    again = matches_for(events, 1)
    assert again["results"][2]["files"] == ["day2.gpx"]
    loaded = [data["track"]["files"] for name, data in events.published if name == "track"]
    assert sorted(loaded) == [["day1.gpx"], ["day2.gpx"]]
    errors = [data["message"] for name, data in events.published if name == "failure"]
    assert len(errors) == 1 and "broken.gpx" in errors[0]
    assert sorted(t["files"] for t in session.state()["tracks"]["tracks"]) == [
        ["day1.gpx"], ["day2.gpx"]]
