"""gpxfoto --ui: start the local server and open the page in the browser."""
import html
import os
import pathlib
import shutil
import signal
import sys
import tempfile
import threading
import webbrowser
from gettext import gettext as _

from gpxfoto.server.app import Server


def run(open_browser=True):
    """Serve the browser interface until its page is closed or Ctrl+C."""
    if shutil.which("exiftool") is None:
        sys.exit(_("exiftool is not installed. On Fedora, install it with: {command}").format(
            command="sudo dnf install perl-Image-ExifTool"))
    from gpxfoto.server.api import Api     # the engine, loaded once the page is coming
    server = Server()
    server.api = Api(server.events)
    # Ended like Ctrl+C when the terminal closes or the system asks
    for signum in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if signum is not None:
            signal.signal(signum, lambda *args: server.stopping.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=server.watch_pages, daemon=True).start()
    # Translators: {url} is the address of the page of the browser interface
    print(_("gpxfoto runs at {url}").format(url=server.url))
    print(_("If no browser opens, open that address. Press Ctrl+C to quit."))
    sys.stdout.flush()
    # The browser gets a file that leads to the page, never the address
    # itself, which other users could read in the list of processes
    opener = tempfile.mkdtemp(prefix="gpxfoto-")
    try:
        if open_browser:
            page = os.path.join(opener, "open.html")
            address = html.escape(server.url)
            with open(os.open(page, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w",
                      encoding="utf-8") as f:
                # The link, for a browser that does not follow the refresh
                f.write('<!DOCTYPE html><meta charset="utf-8"><title>gpxfoto</title>'
                        f'<meta http-equiv="refresh" content="0;url={address}">'
                        f'<a href="{address}">gpxfoto</a>')
            threading.Thread(target=webbrowser.open, args=(pathlib.Path(page).as_uri(),),
                             daemon=True).start()
        while not server.stopping.wait(0.5):
            # The file is not needed once the page is open
            if server.events.ever_listened and os.path.isdir(opener):
                shutil.rmtree(opener, ignore_errors=True)
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(opener, ignore_errors=True)
    server.api.close()
    server.close()
