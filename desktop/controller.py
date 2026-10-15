#!/usr/bin/env python3
"""ValleyMind Companion background controller.

A tiny, dependency-free (standard-library only) local helper that lets the
ValleyMind website turn the floating desktop companion on and off **without a
Desktop shortcut**.

Why this exists
---------------
A normal web page cannot start a Windows executable. When the user flips the
"Show companion outside ValleyMind" toggle, *something* must already be
listening on the loopback port to receive it. This controller is that
something.

It is deliberately small and light: it holds no AI, no secrets and no model.
It only:

  * owns the single stable loopback endpoint the website talks to
    (http://127.0.0.1:8737), and
  * launches / signals the heavier Electron + Flask companion on demand.

The companion (desktop/main.js) polls this controller for the desired
visibility and reports its REAL window visibility back, so the two always
agree. `/state` therefore reports what the window is ACTUALLY doing (from the
companion's report), never merely what was requested — so the website toggle
can never claim the character is on screen when it is not. The controller binds
to 127.0.0.1 only and answers browser requests only from loopback origins or an
explicit allow-list — see VM_COMPANION_ALLOWED_ORIGINS.

Endpoints
---------
  GET  /state       -> {"running", "visible", "desired"}   (website; visible = ACTUAL)
  POST /visibility  -> {"visible": bool}                   (website -> set intent / launch)
  GET  /desired     -> {"visible": bool, "running": true}  (companion poll + heartbeat)
  POST /sync        -> {"visible": bool}                   (companion reports ACTUAL visibility)
  POST /shutdown    -> stop the controller (used by the uninstaller)

Usage
-----
  pythonw.exe desktop\\controller.py     # normal (no console window)
  python     desktop\\controller.py      # debugging (prints to console)
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
_ELECTRON_NAME = "electron.exe" if os.name == "nt" else "electron"
ELECTRON_EXE = HERE / "node_modules" / "electron" / "dist" / _ELECTRON_NAME

HOST = "127.0.0.1"
PORT = int(os.getenv("VM_COMPANION_PORT", "8737"))
BEAT_TIMEOUT = 3.0          # seconds without a companion heartbeat => not running
MAX_BODY = 4096

# Browser origins allowed to drive the controller. Anything on loopback is
# always allowed; the list below covers the hosted ValleyMind site. Extra
# origins can be added without code changes via VM_COMPANION_ALLOWED_ORIGINS
# (comma or whitespace separated).
_DEFAULT_ALLOWED = (
    "https://valleymind-ai.vercel.app",
    "https://valleymind-ai-opms.onrender.com",
    "https://valleymind.ai",
    "https://www.valleymind.ai",
)


def _allowed_origins():
    out = set(_DEFAULT_ALLOWED)
    for item in re.split(r"[,\s]+", os.getenv("VM_COMPANION_ALLOWED_ORIGINS", "")):
        item = item.strip().rstrip("/")
        if item:
            out.add(item)
    return out


ALLOWED_ORIGINS = _allowed_origins()


def origin_allowed(origin):
    """Allow loopback (any port), the configured hosts, and non-browser clients."""
    if not origin:
        return True  # same-origin / non-browser client (the companion itself)
    try:
        host = (urlparse(origin).hostname or "").lower()
    except Exception:
        return False
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    return origin.strip().rstrip("/") in ALLOWED_ORIGINS


def _log(message):
    """Best-effort logging (pythonw has no console). Never raises."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(HERE / ".controller.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


class _State:
    """Shared, lock-guarded controller state."""

    def __init__(self):
        self.lock = threading.Lock()
        self.desired_visible = False   # intent from the website / companion
        self.actual_visible = False    # window's REAL visibility, reported by the companion
        self.actual_at = 0.0           # when the actual visibility was last reported
        self.last_beat = 0.0           # last companion heartbeat (time.time())
        self.child = None              # Popen of the Electron companion we spawned

    # -- liveness ----------------------------------------------------------
    def reap(self):
        if self.child is not None and self.child.poll() is not None:
            self.child = None

    def child_alive(self):
        self.reap()
        return self.child is not None

    def recent_beat(self):
        return (time.time() - self.last_beat) < BEAT_TIMEOUT

    def running(self):
        return self.child_alive() or self.recent_beat()

    # -- lifecycle ---------------------------------------------------------
    def spawn(self):
        """Launch the Electron companion once. Returns True if we started it."""
        if self.running():
            return False
        if not ELECTRON_EXE.exists():
            raise FileNotFoundError(
                "Electron is not installed in this project. Run `npm install` in desktop/ once."
            )
        _log(f"launching companion: {ELECTRON_EXE}")
        self.child = subprocess.Popen(
            [str(ELECTRON_EXE), "."],
            cwd=str(HERE),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    def terminate(self):
        child = self.child
        self.child = None
        if child is None or child.poll() is not None:
            return
        _log(f"stopping companion (pid {child.pid})")
        try:
            if os.name == "nt":
                # Kill the whole tree: Electron's helpers AND the Flask backend
                # it spawned, so nothing is left orphaned.
                subprocess.run(
                    ["taskkill", "/pid", str(child.pid), "/t", "/f"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                child.terminate()
        except Exception:
            try:
                child.terminate()
            except Exception:
                pass


STATE = _State()


class Handler(BaseHTTPRequestHandler):
    server_version = "ValleyMindCompanionController/1.0"
    protocol_version = "HTTP/1.1"

    # Keep quiet on stderr; we log deliberately instead.
    def log_message(self, *args):  # noqa: D401 - BaseHTTPRequestHandler hook
        return

    # -- helpers -----------------------------------------------------------
    def _origin(self):
        return self.headers.get("Origin")

    def _cors(self, origin):
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def _send(self, code, payload, origin=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if origin:
            self._cors(origin)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(min(n, MAX_BODY))
        try:
            return json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            return {}

    def _guard(self, origin):
        if origin_allowed(origin):
            return True
        self._send(403, {"ok": False, "error": "origin_not_allowed"}, None)
        return False

    # -- methods -----------------------------------------------------------
    def do_OPTIONS(self):
        origin = self._origin()
        if not self._guard(origin):
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        if origin:
            self._cors(origin)
        self.end_headers()

    def do_GET(self):
        origin = self._origin()
        if not self._guard(origin):
            return
        path = urlparse(self.path).path
        if path == "/state":
            with STATE.lock:
                running = STATE.running()
                # Report ACTUAL visibility reported by the companion, never the
                # merely-requested value, so the UI cannot show a false ON.
                visible = bool(STATE.actual_visible) if running else False
                desired = bool(STATE.desired_visible)
            return self._send(200, {"running": running, "visible": visible, "desired": desired}, origin)
        if path == "/desired":
            with STATE.lock:
                STATE.last_beat = time.time()
                STATE.reap()
                visible = bool(STATE.desired_visible)
            return self._send(200, {"visible": visible, "running": True}, origin)
        if path in ("/health", "/ping"):
            return self._send(200, {"ok": True, "running": STATE.running()}, origin)
        return self._send(404, {"ok": False, "error": "not_found"}, origin)

    def do_POST(self):
        origin = self._origin()
        if not self._guard(origin):
            return
        path = urlparse(self.path).path
        data = self._read_json()

        if path == "/visibility":
            visible = data.get("visible", True) is not False
            with STATE.lock:
                STATE.desired_visible = bool(visible)
                started = False
                if visible and not STATE.running():
                    try:
                        started = STATE.spawn()
                    except FileNotFoundError as exc:
                        return self._send(
                            500,
                            {"ok": False, "error": str(exc), "running": False, "visible": False},
                            origin,
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        _log(f"spawn failed: {exc}")
                        return self._send(
                            500,
                            {"ok": False, "error": "could not launch the companion", "running": False, "visible": False},
                            origin,
                        )
                running = STATE.running()
                desired = STATE.desired_visible
            return self._send(
                200, {"ok": True, "running": running, "visible": desired, "starting": started}, origin
            )

        if path == "/sync":
            # The companion reports its REAL window visibility here. This is the
            # value /state returns, so the two can never disagree silently.
            visible = data.get("visible", True) is not False
            with STATE.lock:
                STATE.actual_visible = bool(visible)
                STATE.actual_at = time.time()
                actual = STATE.actual_visible
            return self._send(200, {"ok": True, "visible": actual}, origin)

        if path == "/shutdown":
            self._send(200, {"ok": True, "stopping": True}, origin)
            threading.Thread(target=_shutdown, daemon=True).start()
            return

        return self._send(404, {"ok": False, "error": "not_found"}, origin)


def _shutdown():
    time.sleep(0.2)
    with STATE.lock:
        STATE.terminate()
    _log("controller shutting down")
    os._exit(0)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--stop", "stop"):
        # Ask a running controller to stop (used by the uninstaller).
        import urllib.request

        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/shutdown", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=3).read()
            print("controller stopped")
        except Exception as exc:  # already stopped
            print(f"controller not running ({exc})")
        return

    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        # Port already taken => another controller instance owns it. That's fine.
        _log(f"controller already running or port busy ({exc}); exiting")
        return
    server.daemon_threads = True
    _log(f"ValleyMind companion controller listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        with STATE.lock:
            STATE.terminate()


if __name__ == "__main__":
    main()
