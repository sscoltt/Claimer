#!/usr/bin/env python3
"""
Chrome fleet launcher and dashboard server.

Opens N Chrome windows, each in its own profile (separate cookies and storage),
staggered by a random delay, all pointing at one queue URL. Serves a local
dashboard that ranks the windows by queue progress and flags any window that
is redirected away from the queue site.

Usage:
    python launcher.py              start the server and open the dashboard
    python launcher.py --self-test  check the host-matching rule and exit
"""

# ============================ SETTINGS: EDIT THESE ============================

# Defaults shown in the dashboard form. The dashboard remembers what you last typed.
TARGET_URL = "http://localhost:8000/client"
WINDOW_COUNT = 50
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 7.0

# Colour of the queue page's progress bar, and how far each of red, green and
# blue may differ from it and still count. A fuller bar means further along.
BAR_COLOR = "#1CBD81"
BAR_TOLERANCE = 25

# How strictly to judge "this is a different website".
#   "base"  -> www.example.com and example.com count as the same site
#   "exact" -> any hostname change counts as a redirect
MATCH_MODE = "base"

# Dashboard address, and the port Chrome's debugging interface listens on.
PORT = 8765
DEBUG_PORT = 9222

# Leave None to auto-detect Chrome, or set a full path to the executable.
CHROME_PATH = None

# =============================================================================

import json
import os
import platform
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "chrome-data"   # the fleet's own Chrome data folder, one profile per slot
SERVER = f"http://127.0.0.1:{PORT}"
DEBUG = f"http://127.0.0.1:{DEBUG_PORT}"
MAX_WINDOWS = 200


def find_chrome():
    if CHROME_PATH:
        return CHROME_PATH

    system = platform.system()
    candidates = []

    if system == "Windows":
        for root in [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]:
            if root:
                candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    elif system == "Darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        candidates.append(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


def base_domain(host):
    labels = host.split(".")
    if len(labels) <= 2 or labels[-1].isdigit():  # short name or bare IP
        return host
    return ".".join(labels[-2:])


def same_site(host, allowed_host):
    if not host or not allowed_host:
        return False
    if MATCH_MODE == "exact":
        return host == allowed_host
    return base_domain(host) == base_domain(allowed_host)


# ------------------------------------------------------------------ state ----
# One entry per launched window, keyed by slot number. Guarded by `lock`.

lock = threading.Lock()
stop_launching = threading.Event()
slots = {}
run = {"launching": False, "target": TARGET_URL}


def debug_get(path):
    with urllib.request.urlopen(f"{DEBUG}{path}", timeout=3) as res:
        return res.read()


def launch(chrome, target, count, min_delay, max_delay):
    try:
        for slot in range(1, count + 1):
            if slot > 1 and stop_launching.wait(random.uniform(min_delay, max_delay)):
                break
            with lock:
                slots[slot] = {"slot": slot, "status": "launching", "id": None, "url": "", "host": ""}
            subprocess.Popen(
                [chrome, f"--user-data-dir={DATA_DIR}", f"--remote-debugging-port={DEBUG_PORT}",
                 f"--remote-allow-origins={SERVER}", "--no-first-run", "--no-default-browser-check",                 f"--profile-directory=Slot {slot}", "--new-window", f"{SERVER}/start?slot={slot}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"  slot {slot:>3} launched")
    finally:
        with lock:
            run["launching"] = False


def watch():
    """Once a second, match Chrome's tab list to slots and spot redirects."""
    while True:
        time.sleep(1)
        with lock:
            if not slots:
                continue
        try:
            pages = {t["id"]: t["url"] for t in json.loads(debug_get("/json/list")) if t.get("type") == "page"}
        except (OSError, ValueError):
            continue  # Chrome is not up yet, or has been closed

        with lock:
            allowed = (urlparse(run["target"]).hostname or "").lower()
            # A window on the handshake page is claimed by the slot number in its URL.
            for target_id, url in pages.items():
                if url.startswith(f"{SERVER}/start?"):
                    number = (parse_qs(urlparse(url).query).get("slot") or [""])[0]
                    entry = slots.get(int(number)) if number.isdigit() else None
                    if entry and entry["id"] is None:
                        entry["id"] = target_id

            for entry in slots.values():
                if entry["id"] is None:
                    continue
                url = pages.get(entry["id"])
                if url is None:
                    entry["status"] = "closed"
                    continue
                entry["url"] = url
                if not url.startswith(("http://", "https://")) or url.startswith(SERVER):
                    continue
                host = (urlparse(url).hostname or "").lower()
                if same_site(host, allowed):
                    entry["status"], entry["host"] = "queue", ""
                elif entry["host"] != host:  # tell the console once per new host
                    entry["status"], entry["host"] = "redirected", host
                    print(f"[{time.strftime('%H:%M:%S')}] redirect  slot {entry['slot']:>3}  ->  {url}")


START_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Slot {slot}</title>
<style>
  body {{ font: 15px/1.6 system-ui, sans-serif; margin: 15vh auto; max-width: 30rem;
          color: #222; text-align: center; }}
  .n {{ font-size: 4rem; font-weight: 600; letter-spacing: -.03em; }}
  .m {{ color: #666; }}
</style></head>
<body>
  <div class="n">{slot}</div>
  <p class="m">Waiting for the launcher to pick up this window...</p>
  <script>
    setInterval(async function () {{
      const res = await fetch('/claimed?slot={slot}');
      if (await res.json()) location.replace({target});
    }}, 300);
  </script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the console clean

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, value, code=200):
        self._send(code, json.dumps(value), "application/json")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/":
            self._send(200, (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            return

        if parsed.path == "/state":
            # The dashboard paces itself with this wait, because Chrome slows a hidden page's own timers.
            try:
                time.sleep(min(float((query.get("wait") or ["0"])[0]), 5000) / 1000)
            except ValueError:
                pass
            with lock:
                rows = [
                    {"slot": e["slot"], "status": e["status"], "url": e["url"], "host": e["host"],
                     "ws": f"ws://127.0.0.1:{DEBUG_PORT}/devtools/page/{e['id']}" if e["id"] else None}
                    for e in slots.values()
                ]
                self._json({
                    "launching": run["launching"], "target": run["target"], "slots": rows,
                    "bar": [int(BAR_COLOR[i:i + 2], 16) for i in (1, 3, 5)], "tolerance": BAR_TOLERANCE,
                    "defaults": {"url": TARGET_URL, "count": WINDOW_COUNT,
                                 "min": MIN_DELAY_SECONDS, "max": MAX_DELAY_SECONDS},
                })
            return

        if parsed.path == "/start":
            slot = (query.get("slot") or [""])[0]
            if not slot.isdigit():
                self._send(400, "bad slot")
                return
            with lock:
                target = run["target"]
            self._send(200, START_PAGE.format(slot=slot, target=json.dumps(target)), "text/html; charset=utf-8")
            return

        if parsed.path == "/claimed":
            slot = (query.get("slot") or [""])[0]
            with lock:
                entry = slots.get(int(slot)) if slot.isdigit() else None
                self._json(bool(entry and entry["id"]))
            return

        self._send(404, "not found")

    def do_POST(self):
        # Only the dashboard page may give orders; any other website's request carries its own Origin.
        if self.headers.get("Origin") != SERVER:
            self._send(403, "forbidden")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        path = urlparse(self.path).path

        if path == "/launch":
            try:
                target = str(data["url"]).strip()
                count, low, high = int(data["count"]), float(data["min"]), float(data["max"])
                if urlparse(target).scheme not in ("http", "https") or not urlparse(target).hostname:
                    raise ValueError("Enter an http or https queue URL.")
                if not 1 <= count <= MAX_WINDOWS:
                    raise ValueError(f"Windows must be between 1 and {MAX_WINDOWS}.")
                if not 0 <= low <= high <= 600:
                    raise ValueError("Delays must be between 0 and 600 seconds, minimum first.")
            except (KeyError, TypeError, ValueError) as err:
                self._json({"error": str(err) or "Check the settings."}, 400)
                return
            chrome = find_chrome()
            if not chrome:
                self._json({"error": "Could not find Chrome. Set CHROME_PATH at the top of launcher.py."}, 400)
                return
            with lock:
                if slots:
                    self._json({"error": "Windows are already open. Close them first."}, 400)
                    return
                run.update(launching=True, target=target)
                stop_launching.clear()
            print(f"\nLaunching {count} windows at {target}, {low}-{high}s apart.")
            threading.Thread(target=launch, args=(chrome, target, count, low, high), daemon=True).start()
            self._json({"ok": True})
            return

        if path == "/stop":
            stop_launching.set()
            self._json({"ok": True})
            return

        if path == "/close":
            stop_launching.set()
            with lock:
                ids = [e["id"] for e in slots.values() if e["id"] and e["status"] != "closed"]
                slots.clear()
            for target_id in ids:
                try:
                    debug_get(f"/json/close/{target_id}")
                except OSError:
                    pass
            self._json({"ok": True})
            return

        self._send(404, "not found")


def self_test():
    global MATCH_MODE
    MATCH_MODE = "base"
    assert same_site("www.example.com", "example.com")
    assert same_site("localhost", "localhost")
    assert not same_site("example.org", "example.com")
    assert not same_site("127.0.0.2", "127.0.0.1")
    assert not same_site("", "example.com")
    MATCH_MODE = "exact"
    assert not same_site("www.example.com", "example.com")
    print("self-test passed")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is in use. The launcher is probably already running: open {SERVER}")
        sys.exit(1)
    threading.Thread(target=watch, daemon=True).start()

    print(f"Dashboard: {SERVER}")
    print("Leave this window open while you use the dashboard; press Ctrl+C to stop.")
    print("Closing this window does not close Chrome.\n")
    if "--no-browser" not in sys.argv:
        webbrowser.open(SERVER)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
