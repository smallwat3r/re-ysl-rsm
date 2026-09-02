#!/usr/bin/env python3
"""Local control page: battery, connection, cartridges, dispense.

    make web            # or: .venv/bin/python app.py  -> http://127.0.0.1:8765

Stdlib HTTP server bound to localhost only (it drives real pumps, do not
expose it). It serves the static page and pushes device state to it over
Server-Sent Events, the BLE session lives in `device.py`.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import favourites
from device import BleLink, Device
from fake_device import FakeLink
from protocol import (
    CARTRIDGE_COLOURS,
    CARTRIDGE_LABELS,
    MAX_TOTAL,
    StateFragment,
    build_dispense,
)

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("rsm")

PORT = 8765
SSE_KEEPALIVE = 15.0  # s of silence before a comment-line ping keeps the link open
MAX_BODY = 4096  # POST bodies are tiny JSON, cap to avoid a memory-DoS

# By default the page is reachable from this machine only. RSM_LAN=1 binds all
# interfaces so a Pi can serve it to the home LAN or its own hotspot. Either
# way a request is accepted only when it is addressed to us (Handler._ours),
# which blocks cross-site fetches and DNS rebinding: these endpoints drive the
# hardware. LAN is the trust boundary, there is no auth, add a token if the
# network isn't yours to trust.
LAN = os.environ.get("RSM_LAN") == "1"
BIND = "0.0.0.0" if LAN else "127.0.0.1"
HOSTNAME = socket.gethostname()
# The names a genuine request can carry in Host. IPs are not listed, they are
# checked live against the address the connection arrived on, so a new DHCP
# lease or the hotspot address needs no configuration.
ALLOWED_NAMES = frozenset({"127.0.0.1", "localhost", HOSTNAME, f"{HOSTNAME}.local"})

# Static files, read once at startup: route -> (bytes, content type).
ASSETS: dict[str, tuple[bytes, str]] = {
    route: ((STATIC_DIR / filename).read_bytes(), ctype)
    for route, filename, ctype in (
        ("/", "index.html", "text/html; charset=utf-8"),
        ("/app.css", "app.css", "text/css"),
        ("/app.js", "app.js", "application/javascript"),
        ("/util.js", "util.js", "application/javascript"),
        ("/store.js", "store.js", "application/javascript"),
        ("/mixer.js", "mixer.js", "application/javascript"),
        ("/favs.js", "favs.js", "application/javascript"),
    )
}

# RSM_MOCK=1 runs the page against a simulated device (`make mock`), no radio needed.
DEV = Device(FakeLink() if os.environ.get("RSM_MOCK") == "1" else BleLink())

# Hardware actions: path -> coroutine factory taking the parsed JSON body.
# Paths in NEEDS_LINK also require an open BLE connection.
ACTIONS = {
    "/connect": lambda _body: DEV.connect(),
    "/disconnect": lambda _body: DEV.disconnect(),
    "/refresh": lambda _body: DEV.refresh(),
    "/dispense": lambda body: DEV.dispense(build_dispense(body["amounts"])),
}
NEEDS_LINK = frozenset({"/refresh", "/dispense"})


def snapshot() -> StateFragment:
    """The full page state: device state plus the static tables the page needs."""
    return DEV.state.snapshot(
        colours=CARTRIDGE_COLOURS, labels=CARTRIDGE_LABELS, max_total=MAX_TOTAL
    )


class Handler(BaseHTTPRequestHandler):
    """HTTP routes.

    GET  / /app.css /*.js /state /events /favourites
    POST /connect /disconnect /refresh /dispense /favourites /favourites/delete
    """

    def _reply(
        self, code: int, body: bytes | StateFragment, ctype: str = "application/json"
    ) -> None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path in ASSETS:
            body, ctype = ASSETS[self.path]
            self._reply(200, body, ctype)
        elif self.path == "/state":
            self._reply(200, snapshot())
        elif self.path == "/favourites":
            self._reply(200, {"favourites": favourites.list_all()})
        elif self.path == "/events":
            self._stream_events()
        else:
            self._reply(404, {"error": "not found"})

    def _stream_events(self) -> None:
        """Server-Sent Events: push full state on change, keepalive when idle."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        ev = DEV.state.subscribe()
        ev.set()  # first pass sends the current state
        try:
            while True:
                if ev.wait(SSE_KEEPALIVE):
                    ev.clear()  # before the snapshot, so a change mid-send is not lost
                    self.wfile.write(f"data: {json.dumps(snapshot())}\n\n".encode())
                else:
                    self.wfile.write(b": ping\n\n")  # SSE comment, keeps the link open
                self.wfile.flush()
        except OSError:
            pass  # client went away (reset, broken pipe, or TCP gave up on a dead peer)
        finally:
            DEV.state.unsubscribe(ev)

    def _ours(self, host: str) -> bool:
        """Is `host` (a Host or Origin value, optional :port) one of our addresses?"""
        name = host.partition(":")[0]  # IPv4 only, we never bind an IPv6 address
        return name in ALLOWED_NAMES or name == self.connection.getsockname()[0]

    def _local_only(self) -> bool:
        """Reject cross-site / DNS-rebinding requests to this hardware server."""
        origin = self.headers.get("Origin")
        # Browsers send Origin on every POST, same-origin included, so only
        # reject Origins that are not us (cross-site) rather than any Origin.
        if origin is not None and not self._ours(origin.removeprefix("http://")):
            return False
        return self._ours(self.headers.get("Host", ""))

    def do_POST(self) -> None:
        if not self._local_only():
            return self._reply(403, {"error": "forbidden (local requests only)"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._reply(413, {"error": "body too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._reply(400, {"error": "bad json"})

        # Favourites touch only the local DB, no hardware gates.
        if self.path == "/favourites":
            return self._add_favourite(body)
        if self.path == "/favourites/delete":
            favourites.delete(int(body.get("id", 0)))
            return self._reply(200, {"ok": True})

        if self.path not in ACTIONS:
            return self._reply(404, {"error": "not found"})
        if DEV.state.get("busy"):
            return self._reply(409, {"error": "busy"})
        if self.path in NEEDS_LINK and not DEV.state.get("connected"):
            return self._reply(409, {"error": "not connected"})
        try:
            coro = ACTIONS[self.path](body)
        except (KeyError, ValueError, TypeError) as exc:
            return self._reply(400, {"error": f"bad request: {exc}"})
        DEV.run(coro)
        self._reply(202, {"ok": True})

    def _add_favourite(self, body: Any) -> None:
        try:
            fav_id = favourites.add(body.get("name"), body.get("recipe"))
        except (ValueError, TypeError, AttributeError) as exc:
            return self._reply(400, {"error": f"bad favourite: {exc}"})
        self._reply(201, {"id": fav_id})

    def log_message(self, *_args: Any) -> None:
        pass  # keep the terminal clean, the frame log is in the UI


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    where = f"{HOSTNAME}.local" if LAN else "127.0.0.1"
    logger.info("serving on http://%s:%d (bound %s)", where, PORT, BIND)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # never leave the link open in bluetoothd
        DEV.run(DEV.disconnect()).result(timeout=10)


if __name__ == "__main__":
    main()
