"""The journal of a write (gpxfoto.engine.journal)."""
import os

import pytest

from gpxfoto.engine import journal as journal_module
from gpxfoto.engine.journal import Journal, journal_dir


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(journal_module.sys, "platform", "linux")


def test_entries_come_back_as_they_were_added(tmp_path):
    journal = Journal.create()
    assert os.path.dirname(journal.path) == journal_dir() == str(
        tmp_path / "state" / "gpxfoto" / "journal")
    assert os.stat(journal.path).st_mode & 0o077 == 0
    assert os.stat(journal_dir()).st_mode & 0o077 == 0
    assert journal.entries() == []
    photo = tmp_path / "zdj\udcea.jpg"           # a name that is not UTF-8
    photo.write_bytes(b"x")
    written = os.stat(photo)
    gps = {"head": "/9j/", "sha256": "ab", "had_location": True}
    journal.add(str(photo), gps, written)
    journal.add("/b.jpg", {}, written)
    (path, kept, stat), second = journal.entries()
    assert (path, kept, second[:2]) == (str(photo), gps, ("/b.jpg", {}))
    assert (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_atime_ns) == (
        written.st_ino, written.st_mtime_ns, written.st_ctime_ns, written.st_atime_ns)


def test_a_line_cut_short_is_left_out(tmp_path):
    journal = Journal.create()
    journal.add("/a.jpg", {}, os.stat(tmp_path))
    with open(journal.path, "a", encoding="utf-8") as f:
        f.write('{"path": "/b.jpg", "gps": {}, "st')
    assert [entry[0] for entry in journal.entries()] == ["/a.jpg"]


def test_keeping_some_entries_and_then_none(tmp_path):
    journal = Journal.create()
    for name in ("/a.jpg", "/b.jpg", "/c.jpg"):
        journal.add(name, {}, os.stat(tmp_path))
    journal.keep(journal.entries()[1:2])
    assert [entry[0] for entry in journal.entries()] == ["/b.jpg"]
    assert os.listdir(journal_dir()) == [os.path.basename(journal.path)]
    journal.keep([])
    assert os.listdir(journal_dir()) == [] and journal.entries() == []
