"""Writing the locations the page shows into the photos, in the background.

A few photos are written at once, each by its own exiftool. Cancelling
stops taking new photos; those being written are finished, so every photo
ends up either as it was or with its new location, as the writer makes
sure of.
"""
import os
import threading
import time
from collections import namedtuple

from gpxfoto.engine.writer import write_location

# Photos written at the same time; most of the time of each goes to
# starting exiftool and to the disk
WRITE_THREADS = min(4, os.cpu_count() or 1)
# The page hears of the progress at most this often, in seconds
PROGRESS_EVERY = 0.1

# One photo to write: id is its index among the photos of the page, seen
# its os.stat() from before its metadata was read
Job = namedtuple("Job", "id path lat lon ele time_utc replace seen")


class Writing:
    """Writes jobs in background threads; publish(name, data) tells the page.

    finished(writing) is called once all threads have ended. written then
    holds (id, os.stat() of the new photo) and failed (id, message).
    """

    def __init__(self, jobs, publish, finished, threads=None):
        self.jobs = list(jobs)
        self.total = len(self.jobs)
        self.publish = publish
        self.finished = finished
        self.written = []
        self.failed = []
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._next = 0
        self._published = 0.0
        # Not daemon threads: the program waits for the photos being written
        self._threads = [threading.Thread(target=self._work)
                         for _ in range(max(1, min(threads or WRITE_THREADS, self.total)))]
        self._ended = threading.Event()

    @property
    def done(self):
        return len(self.written) + len(self.failed)

    def progress(self):
        """What the page shows while the photos are written."""
        with self._lock:
            return {"total": self.total, "done": self.done, "written": len(self.written),
                    "failed": len(self.failed), "cancelled": self.cancelled.is_set()}

    def summary(self):
        """What the page shows once the writing has ended."""
        with self._lock:
            return {"total": self.total, "written": len(self.written),
                    "failed": [{"id": i, "message": message} for i, message in sorted(self.failed)],
                    "cancelled": self.cancelled.is_set(),
                    "not_written": self.total - self.done}

    def start(self):
        for thread in self._threads:
            thread.start()
        threading.Thread(target=self._end, daemon=True).start()

    def cancel(self):
        self.cancelled.set()

    def wait(self, timeout=None):
        """Wait until the photos being written are finished and finished() was called."""
        return self._ended.wait(timeout)

    def _take(self):
        with self._lock:
            if self.cancelled.is_set() or self._next >= self.total:
                return None
            job = self.jobs[self._next]
            self._next += 1
            return job

    def _work(self):
        while (job := self._take()) is not None:
            try:
                write_location(job.path, job.lat, job.lon, job.ele, job.time_utc, False,
                               replace=job.replace, seen=job.seen)
                outcome = os.stat(job.path)
            except Exception as e:      # one photo that cannot be written stops no other
                outcome = str(e) or type(e).__name__
            with self._lock:
                if isinstance(outcome, str):
                    self.failed.append((job.id, outcome))
                else:
                    self.written.append((job.id, outcome))
                now = time.monotonic()
                due = now - self._published >= PROGRESS_EVERY
                if due:
                    self._published = now
            if due:
                self.publish("write-progress", self.progress())

    def _end(self):
        for thread in self._threads:
            thread.join()
        try:
            self.finished(self)
        finally:
            self._ended.set()
