"""The local HTTP server: security checks, the page, its files and events.

The server listens on 127.0.0.1 only, on a free port. The address opened
at start holds a random token in its fragment, which the page keeps and
sends with every request for data; the page's own files hold none and need
no token. Requests must also name this server in Host
and, when the browser sends them, come from this page in Origin and
Sec-Fetch-Site, so that other web pages open in the browser can neither
read nor change anything.
"""
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from gettext import gettext as _
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gpxfoto.server.events import Events

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "web")
# The application icon is the page's icon. Building the package copies it
# among the page files; run from the source tree, it comes from data/icons.
FAVICON = "favicon.svg"
SOURCE_ICON = os.path.join(os.path.dirname(os.path.dirname(WEB_DIR)), "data", "icons",
                           "hicolor", "scalable", "apps", "io.github.tomaszbojanowski.Gpxfoto.svg")
# The program ends this long after the last page closed, unless a page
# connects again in the meantime, as it does when it is reloaded
CLOSE_GRACE = 10.0           # s
# The largest request body accepted, for a GPX file dropped on the page
MAX_BODY = 64 * 1024 * 1024
TILES = "https://tiles.openfreemap.org"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
        f"img-src 'self' data: blob: {TILES}; connect-src 'self' {TILES}; "
        "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",
}


# The header in which the page sends the token
TOKEN_HEADER = "X-Gpxfoto-Token"
# Requests the browser makes itself, without the header: they carry the
# token in the query
TOKEN_IN_QUERY = ("/api/events", "/api/thumbnail")
# Requests that need no token: the translations of the page
PUBLIC = ("/api/i18n",)


def _same(given, token):
    """Whether given is the token, in constant time; any text may be given."""
    return hmac.compare_digest(given.encode("utf-8", "surrogateescape"), token.encode())


class RequestError(Exception):
    """A request that cannot be served; the text is for the page."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Server(ThreadingHTTPServer):
    """The server of one browser interface; api handles the /api/ requests."""

    daemon_threads = True

    def __init__(self, api=None, port=0):
        super().__init__(("127.0.0.1", port), Handler)
        self.port = self.server_address[1]
        self.token = secrets.token_urlsafe(32)
        self.host = f"127.0.0.1:{self.port}"
        self.origin = f"http://{self.host}"
        self.events = Events()
        self.api = api
        self.stopping = threading.Event()

    @property
    def url(self):
        # A fragment never leaves the browser
        return f"{self.origin}/#{self.token}"

    def handle_error(self, request, client_address):
        # A page that went away in the middle of an answer is no error
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)

    def watch_pages(self, grace=CLOSE_GRACE, step=1.0):
        """Set stopping when no page has listened for grace seconds, after
        one did. Runs until stopping is set."""
        quiet_since = None
        while not self.stopping.wait(step):
            if not self.events.ever_listened or self.events.listeners():
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= grace:
                self.stopping.set()

    def close(self):
        """End the event streams and stop serving."""
        self.stopping.set()
        self.events.close()
        self.shutdown()
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gpxfoto"
    sys_version = ""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        try:
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if not self._trusted(method, url.path, query):
                # A body that was not read must not be taken for the next request
                self.close_connection = True
                return
            self._route(method, url.path, query)
        except RequestError as e:
            self.close_connection = True
            self.send_json({"error": str(e)}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # --- security -----------------------------------------------------

    def _trusted(self, method, path, query):
        server = self.server
        if self.headers.get("Host") != server.host:
            self.send_text(HTTPStatus.MISDIRECTED_REQUEST, "wrong Host")
            return False
        origin = self.headers.get("Origin")
        if origin != server.origin and (origin is not None or method != "GET"):
            self.send_text(HTTPStatus.FORBIDDEN, "wrong Origin")
            return False
        # The page holds no data: it may be opened from anywhere, also from
        # the file through which the browser is started
        if method == "GET" and path == "/":
            return True
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            self.send_text(HTTPStatus.FORBIDDEN, "not from this page")
            return False
        if method == "GET" and (path.startswith("/static/") or path in PUBLIC):
            return True
        # The page sends the token, which it got in the fragment of the
        # address, with each request: in a header, or in the query where
        # the browser makes the request itself. Cookies would not do, as
        # browsers send them to every server on 127.0.0.1, whatever its port.
        token = self.headers.get(TOKEN_HEADER)
        if token is None and method == "GET" and path in TOKEN_IN_QUERY:
            token = query.get("token", [""])[0]
        if not _same(token or "", server.token):
            self.send_text(HTTPStatus.FORBIDDEN, self._open_from_terminal())
            return False
        return True

    @staticmethod
    def _open_from_terminal():
        return _("Open the address that gpxfoto showed in the terminal when it started.")

    def _security_headers(self):
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)

    # --- routing ------------------------------------------------------

    def _route(self, method, path, query):
        if method == "GET" and path == "/":
            self.send_file(os.path.join(WEB_DIR, "index.html"))
        elif method == "GET" and path.startswith("/static/"):
            self.send_file(_static_path(path[len("/static/"):]))
        elif method == "GET" and path == "/api/events":
            self._stream_events()
        elif path.startswith("/api/") and self.server.api is not None:
            self.server.api.handle(self, method, path[len("/api/"):], query)
        else:
            raise RequestError(HTTPStatus.NOT_FOUND, "not found")

    def _stream_events(self):
        # Listening before the page hears of it: the page then asks for the
        # state, and nothing published after that may be lost
        listener = self.server.events.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        self.close_connection = True

        def write(data):
            self.wfile.write(data)
            self.wfile.flush()

        self.server.events.stream(write, listener)

    # --- responses ----------------------------------------------------

    def read_json(self, limit=1024 * 1024):
        """The JSON body of a POST request; only application/json is accepted."""
        if self.headers.get_content_type() != "application/json":
            raise RequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON expected")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise RequestError(HTTPStatus.LENGTH_REQUIRED, "length required") from None
        if not 0 <= length <= min(limit, MAX_BODY):
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                               _("The GPX file is too large to be dropped here. Choose it with "
                                 "the Track button."))
        body = self.rfile.read(length)
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise RequestError(HTTPStatus.BAD_REQUEST, "not valid JSON") from None

    def send_body(self, body, content_type, status=HTTPStatus.OK, cache="no-store",
                  headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in headers:
            self.send_header(name, value)
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=HTTPStatus.OK):
        # File names that are not UTF-8 hold lone surrogates, which only
        # escapes can carry
        body = json.dumps(data, separators=(",", ":")).encode("ascii")
        self.send_body(body, "application/json", status)

    def send_text(self, status, text):
        self.send_body(text.encode("utf-8"), "text/plain; charset=utf-8", status)

    def send_file(self, path):
        content_type = CONTENT_TYPES.get(os.path.splitext(path)[1].lower()) if path else None
        try:
            if content_type is None:
                raise FileNotFoundError(path)
            with open(path, "rb") as f:
                info = os.fstat(f.fileno())
                tag = f'"{info.st_mtime_ns:x}-{info.st_size:x}"'
                if self.headers.get("If-None-Match") == tag:
                    self.send_body(b"", content_type, HTTPStatus.NOT_MODIFIED, "no-cache",
                                   [("ETag", tag)])
                    return
                body = f.read()
        except OSError:
            raise RequestError(HTTPStatus.NOT_FOUND, "not found") from None
        self.send_body(body, content_type, cache="no-cache", headers=[("ETag", tag)])


def _static_path(relative):
    """The file under WEB_DIR that a /static/ path names, or None."""
    relative = urllib.parse.unquote(relative)
    parts = relative.split("/")
    if (not relative or "\\" in relative or "\0" in relative
            or any(part in ("", ".", "..") or part.startswith(".") for part in parts)):
        return None
    path = os.path.realpath(os.path.join(WEB_DIR, *parts))
    if os.path.commonpath([path, os.path.realpath(WEB_DIR)]) != os.path.realpath(WEB_DIR):
        return None
    if relative == FAVICON and not os.path.isfile(path) and os.path.isfile(SOURCE_ICON):
        return SOURCE_ICON
    return path if os.path.isfile(path) else None

