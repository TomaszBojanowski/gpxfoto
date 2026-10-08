"""The local server of the browser interface: security, files and events.

The server runs in-process on a free port; requests go through
http.client, so no browser is needed.
"""
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse

import pytest

from conftest import ROOT, needs_exiftool, run_cli
from gpxfoto.server import events
from gpxfoto.server.api import Api
from gpxfoto.server import app
from gpxfoto.server.app import Server


class EchoApi(Api):
    """Answers POST /api/echo with the JSON it got."""

    def __init__(self, events):
        super().__init__(events)
        self.routes[("POST", "echo")] = lambda handler, query: handler.send_json(
            {"got": handler.read_json(limit=100)})


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """The settings of each test in a folder of its own."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def server():
    server = Server()
    server.api = EchoApi(server.events)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield server
    server.api.close()
    server.close()
    thread.join(5)


def request(server, method, path, headers=None, body=None, token=True, host=None):
    """Send a request as the page would, with headers changed by headers."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    sent = {"Host": host or server.host}
    if token:
        sent["X-Gpxfoto-Token"] = server.token
    if method == "POST":
        sent["Origin"] = server.origin
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in sent.items():
        if value is not None:
            connection.putheader(name, value)
    data = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
    if data is not None:
        connection.putheader("Content-Length", str(len(data)))
    connection.endheaders(data)
    response = connection.getresponse()
    response.body = response.read()
    connection.close()
    return response


def test_listens_only_on_this_computer(server):
    assert server.server_address[0] == "127.0.0.1"
    # In the fragment, which the browser never sends to any server
    assert server.url == f"http://127.0.0.1:{server.port}/#{server.token}"
    assert len(server.token) >= 40


@pytest.mark.parametrize("site", [None, "none", "cross-site"])
def test_the_page_itself_needs_no_token(server, site):
    # It holds no data, and the browser may come to it from the file that
    # started it
    page = request(server, "GET", "/", token=False, headers={"Sec-Fetch-Site": site})
    assert page.status == 200
    assert page.getheader("Content-Type") == "text/html; charset=utf-8"
    assert b"<title>gpxfoto</title>" in page.body
    assert page.getheader("Set-Cookie") is None
    assert request(server, "GET", "/static/app.css", token=False).status == 200
    assert request(server, "GET", "/api/i18n", token=False).status == 200


@pytest.mark.parametrize("method, path", [("GET", "/api/state"), ("GET", "/api/events"),
                                          ("GET", "/api/thumbnail?generation=0&id=0"),
                                          ("GET", "/api/browse"), ("POST", "/api/echo")])
# Header values are bytes: "\xc4\x85" is "ą" in UTF-8, "\xb3" no UTF-8 at all
@pytest.mark.parametrize("token", [None, "wrong", "", "\xc4\x85", "\xb3"])
def test_every_request_for_data_needs_the_token(server, method, path, token):
    server.api = EchoApi(server.events)
    headers = {} if token is None else {"X-Gpxfoto-Token": token}
    response = request(server, method, path, headers=headers, token=False, body={})
    assert response.status == 403
    assert b"Open the address that gpxfoto showed in the terminal" in response.body


def test_cookies_do_not_stand_for_the_token(server):
    # Browsers send cookies to every server on 127.0.0.1, whatever its port
    headers = {"Cookie": f"gpxfoto-{server.port}={server.token}"}
    assert request(server, "GET", "/api/state", headers=headers, token=False).status == 403


def test_the_token_in_the_query_only_where_the_browser_asks_by_itself(server):
    server.api = Api(server.events)
    query = urllib.parse.urlencode({"token": server.token})
    assert request(server, "GET", f"/api/state?{query}", token=False).status == 403
    response = request(server, "GET", f"/api/thumbnail?generation=0&id=0&{query}", token=False)
    assert response.status == 404              # no such photo, but let in


@pytest.mark.parametrize("host", ["localhost:{port}", "evil.example", "127.0.0.1",
                                  "127.0.0.1:{other}", "evil.example:{port}"])
def test_requests_must_name_this_server_in_host(server, host):
    # A web page can make the browser send requests here under its own
    # name (DNS rebinding): Host gives it away
    host = host.format(port=server.port, other=server.port + 1)
    assert request(server, "GET", "/", host=host).status == 421


@pytest.mark.parametrize("origin", ["http://evil.example", "null",
                                    "http://localhost:{port}", "https://127.0.0.1:{port}"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_requests_from_other_pages_are_refused(server, origin, method):
    origin = origin.format(port=server.port)
    response = request(server, method, "/api/echo", headers={"Origin": origin}, body={"a": 1})
    assert response.status == 403


def test_changes_need_an_origin(server):
    assert request(server, "POST", "/api/echo", headers={"Origin": None}, body={}).status == 403
    response = request(server, "POST", "/api/echo", body={"a": 1})
    assert (response.status, json.loads(response.body)) == (200, {"got": {"a": 1}})


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
@pytest.mark.parametrize("path", ["/api/i18n", "/static/app.css", "/api/echo"])
def test_requests_the_browser_marks_as_from_elsewhere_are_refused(server, site, path):
    method = "POST" if path == "/api/echo" else "GET"
    headers = {"Sec-Fetch-Site": site}
    assert request(server, method, path, headers=headers, body={}).status == 403
    headers = {"Sec-Fetch-Site": "same-origin"}
    assert request(server, method, path, headers=headers, body={}).status == 200


@pytest.mark.parametrize("content_type, body, status", [
    ("text/plain", {"a": 1}, 415),           # a form can send this from any page
    ("application/x-www-form-urlencoded", b"a=1", 415),
    ("application/json", b"{not json", 400),
    ("application/json", {"long": "x" * 200}, 413),
])
def test_only_small_json_bodies_are_accepted(server, content_type, body, status):
    response = request(server, "POST", "/api/echo", headers={"Content-Type": content_type},
                       body=body)
    assert response.status == status


def test_static_files_and_their_headers(server):
    response = request(server, "GET", "/static/vendor/maplibre-gl/maplibre-gl.mjs")
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("X-Frame-Options") == "DENY"
    policy = response.getheader("Content-Security-Policy")
    assert "default-src 'self'" in policy and "script-src 'self';" in policy
    assert "frame-ancestors 'none'" in policy and "unsafe" not in policy
    tag = response.getheader("ETag")
    again = request(server, "GET", "/static/vendor/maplibre-gl/maplibre-gl.mjs",
                    headers={"If-None-Match": tag})
    assert (again.status, again.body) == (304, b"")


@pytest.mark.parametrize("path", [
    "/static/../server/app.py", "/static/%2e%2e/server/app.py", "/static/vendor/../../cli.py",
    "/static//etc/passwd", "/static/vendor/%2F..%2F..%2Fcli.py", "/static/.hidden",
    "/static/vendor\\..\\..\\cli.py", "/static/", "/static/vendor", "/static/index.html%00.js",
    "/static/vendor/README.md", "/nothing", "/api/nothing",
])
def test_nothing_outside_the_page_files_is_served(server, path):
    assert request(server, "GET", path).status == 404


def test_events_reach_every_page(server):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    connection.request("GET", f"/api/events?token={server.token}", headers={"Host": server.host})
    response = connection.getresponse()
    assert response.getheader("Content-Type") == "text/event-stream; charset=utf-8"
    assert response.fp.readline() == b": connected\n"
    assert response.fp.readline() == b"\n"
    assert server.events.listeners() == 1
    server.events.publish("photos", {"count": 2, "name": "zdjęcie"})
    assert response.fp.readline() == b"event: photos\n"
    assert json.loads(response.fp.readline()[len(b"data: "):]) == {"count": 2, "name": "zdjęcie"}
    connection.close()


def test_the_program_ends_when_the_last_page_has_gone(server, monkeypatch):
    monkeypatch.setattr(events, "HEARTBEAT", 0.1)
    watcher = threading.Thread(target=server.watch_pages, kwargs={"grace": 0.3, "step": 0.05},
                               daemon=True)
    watcher.start()
    time.sleep(0.2)
    assert not server.stopping.is_set()          # no page yet: keep waiting for it
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    connection.request("GET", f"/api/events?token={server.token}", headers={"Host": server.host})
    response = connection.getresponse()
    assert response.fp.readline() == b": connected\n"
    time.sleep(0.5)
    assert not server.stopping.is_set()          # a page listens
    response.close()
    connection.close()
    # Noticed at the next heartbeat at the latest, then after the grace
    assert server.stopping.wait(10)
    watcher.join(5)


# --- gpxfoto --ui -----------------------------------------------------------

def start_ui(tmp_path):
    """Run gpxfoto --ui with a browser that only notes the address it gets."""
    browser = tmp_path / "browser"
    browser.write_text(f"#!/bin/sh\necho \"$1\" > {tmp_path / 'opened'}\n")
    browser.chmod(0o755)
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG"))}
    environment.update({"LC_ALL": "C.UTF-8", "PYTHONPATH": ROOT, "BROWSER": str(browser),
                        "PYTHONUNBUFFERED": "1"})
    environment.pop("DISPLAY", None)
    environment.pop("WAYLAND_DISPLAY", None)
    process = subprocess.Popen([sys.executable, "-m", "gpxfoto", "--ui"], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = process.stdout.readline()
    second = process.stdout.readline()
    return process, first, second


@needs_exiftool
def test_ui_shows_its_address_opens_the_browser_and_ends_with_ctrl_c(tmp_path):
    process, first, second = start_ui(tmp_path)
    try:
        url = first.strip().split(" runs at ")[1]
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+/#[\w-]{40,}", url)
        assert second == "If no browser opens, open that address. Press Ctrl+C to quit.\n"
        for _ in range(100):
            if (tmp_path / "opened").exists():
                break
            time.sleep(0.05)
        # The browser gets a file only its user can read, which leads to the
        # address: the token is never in a command line
        opened = (tmp_path / "opened").read_text().strip()
        assert opened.startswith("file:///") and url not in opened
        page = urllib.parse.urlsplit(opened).path
        assert os.stat(page).st_mode & 0o777 == 0o600
        assert os.stat(os.path.dirname(page)).st_mode & 0o777 == 0o700
        with open(page, encoding="utf-8") as f:
            assert f'<meta http-equiv="refresh" content="0;url={url}">' in f.read()
        parts = urllib.parse.urlsplit(url)
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        connection.request("GET", "/api/state", headers={"Host": parts.netloc,
                                                         "X-Gpxfoto-Token": parts.fragment})
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=10)
    assert process.returncode == 0
    assert out == "" and err == ""
    assert not os.path.exists(page)          # removed at the end


@needs_exiftool
def test_ui_ends_when_the_terminal_closes(tmp_path):
    process, first, _second = start_ui(tmp_path)
    process.send_signal(signal.SIGHUP)
    out, err = process.communicate(timeout=10)
    assert (process.returncode, out, err) == (0, "", "")


def test_ui_takes_no_other_arguments(tmp_path):
    result = run_cli("--ui", "-g", "track.gpx", "photos", cwd=tmp_path)
    assert result.returncode == 2
    assert result.stderr.startswith("usage: gpxfoto --ui\n")
    assert "unrecognized arguments: -g track.gpx photos" in result.stderr


def test_ui_is_in_the_help(tmp_path):
    result = run_cli("--help", cwd=tmp_path)
    assert "--ui" in result.stdout
    assert "open the interface in the browser instead" in " ".join(result.stdout.split())


def test_a_photo_named_like_the_option_after_two_dashes(tmp_path):
    result = run_cli("-g", "track.gpx", "--", "--ui", cwd=tmp_path)
    assert "Cannot read the GPX file track.gpx" in result.stderr


def test_the_page_gets_its_translations(server):
    server.api = Api(server.events)
    response = request(server, "GET", "/api/i18n")
    assert response.status == 200
    catalog = json.loads(response.body)
    assert set(catalog) == {"language", "locale", "messages", "plural"}


def test_the_page_browses_folders(server, tmp_path):
    server.api = Api(server.events)
    (tmp_path / "Wakacje").mkdir()
    (tmp_path / "Wakacje" / "a.jpg").write_bytes(b"")
    response = request(server, "GET", "/api/browse?path=" + urllib.parse.quote(str(tmp_path)))
    assert response.status == 200
    assert json.loads(response.body)["folders"] == [{"name": "Wakacje", "photos": 1, "tracks": 0}]
    missing = tmp_path / "missing"
    response = request(server, "GET", "/api/browse?path=" + urllib.parse.quote(str(missing)))
    assert response.status == 400
    assert json.loads(response.body) == {"error": f"No such folder: {missing}"}
    places = json.loads(request(server, "GET", "/api/places").body)
    assert set(places) == {"places", "recent"}


@pytest.mark.parametrize("path, body, message", [
    ("/api/photos", {}, "folder expected"),
    ("/api/photos", {"folder": 5}, "folder: wrong type"),
    ("/api/photos", {"folder": "/x", "recursive": "yes"}, "recursive: wrong type"),
    ("/api/photos", [], "an object expected"),
    ("/api/tracks", {"files": ["a", 1]}, "files: wrong type"),
    ("/api/tracks", {"files": "a.gpx"}, "files: wrong type"),
    ("/api/tracks/drop", {"files": [{"name": "a.gpx"}]}, "data expected"),
    ("/api/correction", {"seconds": "60"}, "seconds: wrong type"),
    ("/api/correction", {"seconds": True}, "seconds: wrong type"),
    ("/api/options", {"overwrite": 1}, "overwrite: wrong type"),
])
def test_requests_of_the_wrong_form_are_refused(server, path, body, message):
    server.api = Api(server.events)
    response = request(server, "POST", path, body=body)
    assert (response.status, json.loads(response.body)) == (400, {"error": message})


def test_choices_that_cannot_be_used_are_explained(server, tmp_path):
    server.api = Api(server.events)
    response = request(server, "POST", "/api/photos", body={"folder": str(tmp_path / "x")})
    assert response.status == 400
    assert json.loads(response.body) == {"error": f"Not a folder: {tmp_path / 'x'}"}
    response = request(server, "POST", "/api/correction", body={"seconds": 1e9})
    assert response.status == 400


@pytest.mark.parametrize("query", ["", "?generation=1", "?generation=x&id=0", "?generation=1&id=0"])
def test_thumbnails_only_of_the_photos_of_the_page(server, query):
    server.api = Api(server.events)
    assert request(server, "GET", "/api/thumbnail" + query).status in (400, 404)


def test_state_of_a_new_page(server):
    server.api = Api(server.events)
    state = json.loads(request(server, "GET", "/api/state").body)
    assert state["photos"] == {"generation": 0, "folder": None, "loading": False, "photos": []}
    assert (state["correction"], state["overwrite"], state["stops"]) == (0.0, False, True)


def test_the_map_style_is_kept_and_other_pages_follow(server):
    server.api = Api(server.events)
    published = []
    server.events.publish = lambda name, data: published.append((name, data))
    state = json.loads(request(server, "GET", "/api/state").body)
    assert state["preferences"] == {"map_style": "light"}
    response = request(server, "POST", "/api/preferences", body={"map_style": "dark"})
    assert (response.status, json.loads(response.body)) == (200, {"map_style": "dark"})
    assert published == [("preferences", {"map_style": "dark"})]
    state = json.loads(request(server, "GET", "/api/state").body)
    assert state["preferences"] == {"map_style": "dark"}


@pytest.mark.parametrize("body", [{"map_style": "blue"}, {"map_style": 1}, {"colour": "dark"},
                                  {}, ["dark"], "dark"])
def test_unknown_preferences_are_refused(server, body):
    server.api = Api(server.events)
    assert request(server, "POST", "/api/preferences", body=body).status == 400
    state = json.loads(request(server, "GET", "/api/state").body)
    assert state["preferences"] == {"map_style": "light"}


def test_the_application_icon_is_the_icon_of_the_page(server):
    with open(os.path.join(ROOT, "data", "icons", "hicolor", "scalable", "apps",
                           "io.github.tomaszbojanowski.Gpxfoto.svg"), "rb") as f:
        icon = f.read()
    response = request(server, "GET", "/static/favicon.svg")
    assert (response.status, response.getheader("Content-Type")) == (200, "image/svg+xml")
    assert response.body == icon
    page = request(server, "GET", "/").body.decode()
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in page
    assert "<title>gpxfoto</title>" in page


def test_the_icon_copied_into_the_package_comes_first(server, tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "favicon.svg").write_bytes(b"<svg/>")
    monkeypatch.setattr(app, "WEB_DIR", str(web))
    assert request(server, "GET", "/static/favicon.svg").body == b"<svg/>"
