"""Folders for the page to choose from (gpxfoto.server.files)."""
import json
import os

import pytest

from gpxfoto.server import files
from gpxfoto.server.files import FolderError, listing


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "anna"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(files.sys, "platform", "linux")
    return home


def make(root, *names):
    for name in names:
        path = root / name
        if name.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")


def test_listing_shows_folders_tracks_and_counts(tmp_path):
    make(tmp_path, "Wakacje/a.JPG", "Wakacje/b.jpeg", "Wakacje/trasa.gpx", "Wakacje/c.png",
         "alpy/", "Zima/x.jpg", "zz.GPX", "a.gpx", "photo.jpg", ".hidden/", ".old.gpx",
         "Wakacje/.d.jpg", "notes.txt")
    result = listing(str(tmp_path))
    assert result == {
        "path": str(tmp_path), "parent": str(tmp_path.parent),
        "folders": [{"name": "alpy", "photos": 0, "tracks": 0},
                    {"name": "Wakacje", "photos": 2, "tracks": 1},
                    {"name": "Zima", "photos": 1, "tracks": 0}],
        "tracks": ["a.gpx", "zz.GPX"], "photos": 1}


def test_listing_of_the_home_folder_and_of_the_root(home):
    assert listing("")["path"] == str(home)
    assert listing("~")["path"] == str(home)
    root = listing("/")
    assert root["path"] == "/" and root["parent"] is None


def test_counting_stops_in_huge_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "COUNT_LIMIT", 10)
    make(tmp_path, *(f"big/{k:03}.jpg" for k in range(30)))
    assert listing(str(tmp_path))["folders"] == [{"name": "big", "photos": 10, "tracks": 0}]


def test_symbolic_links_to_folders_are_folders(tmp_path):
    make(tmp_path, "real/a.jpg", "track.gpx")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    result = listing(str(tmp_path))
    assert [f["name"] for f in result["folders"]] == ["link", "real"]


@pytest.mark.parametrize("name, message", [
    ("missing", "No such folder: {path}"),
    ("file.gpx", "Not a folder: {path}"),
])
def test_listing_errors(tmp_path, name, message):
    make(tmp_path, "file.gpx")
    with pytest.raises(FolderError) as raised:
        listing(str(tmp_path / name))
    assert str(raised.value) == message.format(path=tmp_path / name)


def test_places(home, tmp_path, monkeypatch):
    make(home, "Obrazy/", "Biurko/", "Pictures/")
    make(home, ".config/user-dirs.dirs")
    (home / ".config" / "user-dirs.dirs").write_text(
        '# written by xdg-user-dirs-update\nXDG_DESKTOP_DIR="$HOME/Biurko"\n'
        'XDG_PICTURES_DIR="$HOME/Obrazy"\n')
    media = tmp_path / "media"
    make(media, "KARTA SD/", "Dysk/", ".hidden/")
    real_listdir = os.listdir
    monkeypatch.setattr(files.os, "listdir", lambda path: real_listdir(
        media if path in ("/run/media/anna",) else path))
    monkeypatch.setattr(files.os.path, "isdir", lambda path, isdir=os.path.isdir: isdir(
        str(path).replace("/run/media/anna", str(media))))
    names = [(p["name"], p["path"]) for p in files.places()]
    assert names[:3] == [("Home folder", str(home)), ("Obrazy", str(home / "Obrazy")),
                         ("Biurko", str(home / "Biurko"))]
    assert names[3:] == [("Dysk", "/run/media/anna/Dysk"),
                         ("KARTA SD", "/run/media/anna/KARTA SD")]


def test_places_without_user_dirs(home):
    make(home, "Pictures/")
    assert [p["name"] for p in files.places()] == ["Home folder", "Pictures"]


def test_recent_folders_are_kept_newest_first(home, tmp_path):
    folders = [tmp_path / f"f{k}" for k in range(10)]
    for folder in folders:
        folder.mkdir()
    assert files.recent() == {"photos": [], "tracks": []}
    for folder in folders:
        files.remember("photos", str(folder))
    files.remember("photos", str(folders[3]))
    files.remember("tracks", str(folders[0]))
    expected = [str(folders[k]) for k in (3, 9, 8, 7, 6, 5, 4, 2)]
    assert files.recent() == {"photos": expected, "tracks": [str(folders[0])]}
    saved = home / ".config" / "gpxfoto" / "recent.json"
    assert json.loads(saved.read_text())["photos"] == expected
    assert [p.name for p in saved.parent.iterdir()] == ["recent.json"]
    # A folder that is gone is left out
    folders[9].rmdir()
    assert str(folders[9]) not in files.recent()["photos"]


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"photos": "x", "tracks": [1]}', ""])
def test_a_damaged_list_of_recent_folders_is_ignored(home, content):
    saved = home / ".config" / "gpxfoto" / "recent.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(content)
    assert files.recent() == {"photos": [], "tracks": []}
    files.remember("tracks", str(home))
    assert files.recent()["tracks"] == [str(home)]


def test_settings_on_macos(home, monkeypatch):
    monkeypatch.setattr(files.sys, "platform", "darwin")
    assert files.config_dir() == str(home / "Library" / "Application Support" / "gpxfoto")


def test_the_map_style_is_kept_between_runs(home):
    assert files.preferences() == {"map_style": "light"}
    files.set_preference("map_style", "dark")
    assert files.preferences() == {"map_style": "dark"}
    saved = home / ".config" / "gpxfoto" / "preferences.json"
    assert json.loads(saved.read_text()) == {"map_style": "dark"}
    for name, value in [("map_style", "blue"), ("map_style", None), ("colour", "dark")]:
        with pytest.raises(ValueError):
            files.set_preference(name, value)
    assert files.preferences() == {"map_style": "dark"}
    assert sorted(p.name for p in saved.parent.iterdir()) == ["preferences.json"]


@pytest.mark.parametrize("content", ["not json", "[1]", '{"map_style": "blue"}', ""])
def test_damaged_preferences_are_ignored(home, content):
    saved = home / ".config" / "gpxfoto" / "preferences.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(content)
    assert files.preferences() == {"map_style": "light"}
