"""Changing photos in the background: writing the locations the page shows,
or undoing a write.

A few photos are changed at once, each by its own exiftool. Cancelling
stops taking new photos; those being changed are finished, so every photo
ends up either as it was or changed, as the writer makes sure of.
"""
import base64
import os
import threading
import time
from collections import namedtuple
from gettext import gettext as _

from gpxfoto.engine.writer import metadata_head, restore_head, write_location

# Photos changed at the same time; most of the time of each goes to
# starting exiftool and to the disk
WRITE_THREADS = min(4, os.cpu_count() or 1)
# The page hears of the progress at most this often, in seconds
PROGRESS_EVERY = 0.1

# One photo to write: id is its index among the photos of the page, seen
# its os.stat() from before its metadata was read
Job = namedtuple("Job", "id path lat lon ele time_utc replace seen")
# One photo to undo: id is its place in the journal, before and written
# as the journal holds them
UndoJob = namedtuple("UndoJob", "id path before written")


class Writing:
    """Changes the photos of jobs in background threads; publish(name, data)
    tells the page.

    kind is "write", with Jobs, each noted in journal with what undoing it
    needs, or "undo", with UndoJobs. finished(writing) is called once all threads
    have ended. written then holds (id, os.stat() of the new photo) and
    failed (id, message).
    """

    def __init__(self, kind, jobs, publish, finished, journal=None, threads=None):
        self.kind = kind
        self.jobs = list(jobs)
        self.total = len(self.jobs)
        self.publish = publish
        self.finished = finished
        self.journal = journal
        self.written = []
        self.failed = []
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._next = 0
        self._published = 0.0
        self._threads = threads or WRITE_THREADS
        self._ended = threading.Event()

    @property
    def done(self):
        return len(self.written) + len(self.failed)

    def progress(self):
        """What the page shows while the photos are changed."""
        with self._lock:
            return {"kind": self.kind, "total": self.total, "done": self.done,
                    "written": len(self.written), "failed": len(self.failed),
                    "cancelled": self.cancelled.is_set()}

    def summary(self):
        """What the page shows once the work has ended."""
        with self._lock:
            return {"kind": self.kind, "total": self.total, "written": len(self.written),
                    "failed": [{"id": i, "message": message} for i, message in sorted(self.failed)],
                    "cancelled": self.cancelled.is_set(),
                    "not_written": self.total - self.done}

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled.set()

    def wait(self, timeout=None):
        """Wait until the photos being changed are finished and finished() was called."""
        return self._ended.wait(timeout)

    def _run(self):
        try:
            # Not daemon threads: the program waits for the photos being changed
            workers = [threading.Thread(target=self._work)
                       for _ in range(max(1, min(self._threads, self.total)))]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            try:
                self.finished(self)
            finally:
                self._ended.set()

    def _take(self):
        with self._lock:
            if self.cancelled.is_set() or self._next >= self.total:
                return None
            job = self.jobs[self._next]
            self._next += 1
            return job

    def _change(self, job):
        """Change the photo of job; returns its os.stat() afterwards."""
        if self.kind == "undo":
            restored = restore_head(job.path, base64.b64decode(job.before["head"]),
                                    job.before["sha256"], seen=job.written)
            return restored or os.stat(job.path)
        # Taken before the photo is read: the write checks that it is still so
        seen = job.seen or os.stat(job.path)
        head, digest = metadata_head(job.path)
        written = write_location(job.path, job.lat, job.lon, job.ele, job.time_utc, False,
                                 replace=job.replace, seen=seen)
        before = {"head": base64.b64encode(head).decode("ascii"), "sha256": digest,
                  "had_location": job.replace}
        try:
            if written is None:
                # Undoing it would lose what another program did
                raise OSError(_("another program changed the photo in the meantime"))
            self.journal.add(os.path.realpath(job.path), before, written)
        except OSError as e:
            # The photo is written all the same
            self.publish("failure", {"message": _(
                "{name} was written, but the write of it cannot be undone: {error}").format(
                    name=os.path.basename(job.path), error=e.strerror or e)})
        return written or os.stat(job.path)

    def _work(self):
        while (job := self._take()) is not None:
            try:
                written = self._change(job)
            except Exception as e:      # one photo that cannot be changed stops no other
                self._failed(job, str(e) or type(e).__name__)
            else:
                with self._lock:
                    self.written.append((job.id, written))
                self._tell()

    def _failed(self, job, message):
        with self._lock:
            self.failed.append((job.id, message))
        self._tell()

    def _tell(self):
        with self._lock:
            now = time.monotonic()
            if now - self._published < PROGRESS_EVERY:
                return
            self._published = now
        self.publish("write-progress", self.progress())
