"""The journal of a write: what each photo was before, so that the write
can be undone.

Each write has a file of its own, with a line for each photo written: its
path, what it was before (a dict, see Journal.add) and its os.stat() just
after the write. A photo is undone only when it has not changed since.
"""
import json
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

# The parts of os.stat() kept for each photo
STAT_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_atime_ns")


def journal_dir():
    """Where the journals are kept."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/gpxfoto/journal")
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "gpxfoto", "journal")


class Journal:
    """The journal file at path; any thread may add to it."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    @classmethod
    def create(cls):
        """A new, empty journal, readable only by its owner. Raises OSError."""
        directory = journal_dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), suffix=".jsonl",
                                    dir=directory)
        os.close(fd)
        return cls(path)

    def add(self, path, before, written):
        """Note that the photo at path was written and then had os.stat()
        written; before is a dict of what it was before, as JSON holds it.
        It is on the disk when this returns."""
        line = json.dumps({"path": path, "before": before,
                           "stat": {name: getattr(written, name) for name in STAT_FIELDS}})
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def entries(self):
        """[(path, before, os.stat()-like of the photo as written)] of the journal.

        Lines that cannot be read, as one cut short by a power cut, are left out.
        """
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as f:
                    lines = f.readlines()
            except FileNotFoundError:
                return []
        result = []
        for line in lines:
            try:
                entry = json.loads(line)
                stat = SimpleNamespace(**{name: int(entry["stat"][name])
                                          for name in STAT_FIELDS})
                path, before = entry["path"], entry["before"]
            except (ValueError, TypeError, KeyError):
                continue
            if isinstance(path, str) and isinstance(before, dict):
                result.append((path, before, stat))
        return result

    def keep(self, entries):
        """Leave only entries, as entries() gives them, in the journal; without
        any, the journal is removed."""
        with self._lock:
            if not entries:
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    pass
                return
            fd, temp = tempfile.mkstemp(prefix=".", suffix=".jsonl",
                                        dir=os.path.dirname(self.path))
            try:
                with open(fd, "w", encoding="utf-8") as f:
                    for path, before, stat in entries:
                        f.write(json.dumps({"path": path, "before": before, "stat": {
                            name: getattr(stat, name) for name in STAT_FIELDS}}) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp, self.path)
            except BaseException:
                os.unlink(temp)
                raise
