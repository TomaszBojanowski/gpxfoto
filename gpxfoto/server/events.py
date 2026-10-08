"""Events for the page, sent as server-sent events (SSE)."""
import json
import queue
import threading

# A comment line is sent this often, so that a closed page is noticed
HEARTBEAT = 5.0          # s


class Events:
    """Sends events to every page that listens, each in its own queue.

    Thread-safe: any thread may publish. listeners() counts the pages
    that listen right now.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queues = []
        self._closed = False
        self.ever_listened = False

    def publish(self, name, data):
        message = f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        with self._lock:
            for q in self._queues:
                q.put(message)

    def listeners(self):
        with self._lock:
            return len(self._queues)

    def subscribe(self):
        """Start keeping the events for a page that is about to listen; the
        result goes to stream()."""
        q = queue.Queue()
        with self._lock:
            if self._closed:
                q.put(None)
            else:
                self._queues.append(q)
                self.ever_listened = True
        return q

    def stream(self, write, q=None):
        """Send the events to one page with write(bytes) until it goes away
        or close() is called. write raises OSError for a closed page."""
        if q is None:
            q = self.subscribe()
        try:
            write(b": connected\n\n")
            while True:
                try:
                    message = q.get(timeout=HEARTBEAT)
                except queue.Empty:
                    message = ": ping\n\n"
                if message is None:
                    return
                write(message.encode("utf-8"))
        except OSError:
            pass
        finally:
            with self._lock:
                if q in self._queues:
                    self._queues.remove(q)

    def close(self):
        """End every stream."""
        with self._lock:
            self._closed = True
            for q in self._queues:
                q.put(None)
