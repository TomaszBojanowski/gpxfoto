"""The state of the browser interface and its work in the background
(gpxfoto.server.session), with the page's requests (gpxfoto.server.api)."""
import base64
import json
import math
import os
import threading

import pytest

from conftest import make_jpeg, needs_exiftool, set_tags, write_gpx
from gpxfoto.server import session as session_module
from gpxfoto.server.session import Session, SessionError

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
                     "tz": "camera", "reason": None, "has_location": False, "orientation": 1,
                     "thumbnail": False}
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
    errors = [data["message"] for name, data in events.published if name == "error"]
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
    assert events.wait("error")["message"].startswith("Cannot read the GPX file ")
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
