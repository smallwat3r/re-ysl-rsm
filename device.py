#!/usr/bin/env python3
"""The device session: a thread-safe observable state store, and the Device
that owns the single connection on its own asyncio loop.

Transport-agnostic, `app.py` drives it over HTTP, but nothing here knows about
HTTP. Protocol framing lives in `protocol.py`. The radio is behind the `Link`
seam: `BleLink` is the real BlueZ/bleak one, `fake_device.FakeLink` a simulated
device for developing without hardware (`make mock`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any, Protocol

from bleak import BleakClient, BleakError, BleakScanner

from protocol import (
    HANDSHAKE,
    NOTIFY_UUID,
    WRITE_UUID,
    Op,
    StateFragment,
    annotate,
    parse_response,
)

logger = logging.getLogger("rsm")

# the device's BLE address, per unit so not committed (find it with `make scan`)
ADDRESS = os.environ.get("RSM_ADDRESS", "")

LOG_LINES = 40  # frame-log ring buffer kept in state
REFRESH_OPS = (Op.PRODUCTION, Op.USAGE, Op.DEVICE_INFO)
CONNECT_ATTEMPTS = 5


class Client(Protocol):
    """An open GATT connection, the subset of `BleakClient` the session uses."""

    @property
    def is_connected(self) -> bool: ...

    async def start_notify(
        self, uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None: ...

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool) -> None: ...

    async def disconnect(self) -> None: ...


class Link(Protocol):
    """How a session reaches a device: locate it once, then open connections to it.

    `find` runs once per session, `open` may run several times (the session retries
    a flaky connect), so anything slow or cached belongs in `find`.
    """

    async def find(self, log: Callable[[str], None]) -> Any: ...

    async def open(self, target: Any, on_disconnect: Callable[[], None]) -> Client: ...


class BleLink:
    """The real device over BlueZ, via bleak."""

    def __init__(self, address: str = ADDRESS) -> None:
        self.address = address

    async def find(self, log: Callable[[str], None]) -> Any:
        """Find the device, clearing a stale BlueZ link that stops it advertising."""
        if not self.address:
            raise BleakError(
                "no device address: set RSM_ADDRESS (find it with `make scan`)"
            )
        device = await BleakScanner.find_device_by_address(self.address, timeout=15.0)
        if device is None:
            subprocess.run(
                ["bluetoothctl", "disconnect", self.address],
                capture_output=True,
                timeout=10,
            )
            device = await BleakScanner.find_device_by_address(
                self.address, timeout=10.0
            )
        if device is None:
            raise BleakError(
                "not advertising (asleep? open the lid, or the phone has it)"
            )
        # The device bonds each central once (Just Works) and drops any that
        # won't: refused, it hangs up at once, ignored, the kernel's 30 s
        # pairing timeout hangs up for it. `make pair` bonds this machine.
        # Reads BlueZ's Device1 props, this app is Linux-only anyway.
        if not device.details.get("props", {}).get("Paired"):
            log("not bonded with this machine, run `make pair` first")
        return device

    async def open(self, target: Any, on_disconnect: Callable[[], None]) -> Client:
        client = BleakClient(
            target, timeout=20.0, disconnected_callback=lambda _c: on_disconnect()
        )
        await client.connect()
        return client


class StateStore:
    """Thread-safe device state with change subscribers.

    Any mutation wakes every subscriber (one per open SSE connection) so the page
    is pushed the new state the instant anything changes. Snapshots are plain
    JSON-serialisable dicts.
    """

    def __init__(self) -> None:
        self._state: StateFragment = {
            "connected": False,
            "busy": False,
            "error": "",
            "log": [],
        }
        self._lock = threading.Lock()
        self._subs: set[threading.Event] = set()
        self._subs_lock = threading.Lock()

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)
        self._notify()

    def log(self, line: str) -> None:
        now = time.time()
        clock = time.strftime("%H:%M:%S", time.localtime(now))
        stamped = f"{clock}.{int(now * 1000) % 1000:03d} {line}"
        logger.info(line)
        with self._lock:
            self._state["log"] = (self._state["log"] + [stamped])[-LOG_LINES:]
        self._notify()

    def get(self, key: str) -> Any:
        with self._lock:
            return self._state.get(key)

    def snapshot(self, **extra: Any) -> StateFragment:
        with self._lock:
            return {**self._state, **extra}

    def subscribe(self) -> threading.Event:
        ev = threading.Event()
        with self._subs_lock:
            self._subs.add(ev)
        return ev

    def unsubscribe(self, ev: threading.Event) -> None:
        with self._subs_lock:
            self._subs.discard(ev)

    def _notify(self) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for ev in subs:
            ev.set()


class Device:
    """The single device session, on its own asyncio loop, writing to `state`.

    All coroutine methods run on `self.loop`, never call them from the HTTP
    threads directly, submit them with `run`.
    """

    def __init__(self, link: Link | None = None) -> None:
        self.link: Link = link or BleLink()
        self.state = StateStore()
        self.loop = asyncio.new_event_loop()
        self.client: Client | None = None
        self.seq = 0
        self.waiters: dict[int, asyncio.Future[tuple[int, bytes]]] = {}
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        """Submit a coroutine to the BLE loop, surfacing any error to the page."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(self._on_done)
        return fut

    def _on_done(self, fut: Future[Any]) -> None:
        exc = fut.exception()
        if exc is None:
            return
        logger.error("BLE task failed: %r", exc)
        self.state.update(error=repr(exc))
        # A dropped link surfaces as a write/notify failure while state still says
        # connected (the disconnect callback doesn't always fire). Reconcile so the
        # UI stops showing a dead connection.
        if (
            self.client is None
            or not self.client.is_connected
            or "not connected" in str(exc).lower()
        ):
            self._gone()

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        self.state.log(annotate("<-", frame))
        if len(frame) < 5 or frame[0] != 0xAA:
            return
        op, status, payload = frame[2] & 0x7F, frame[3], frame[5 : 5 + frame[4]]
        self.state.update(**parse_response(op, payload))
        waiter = self.waiters.pop(op, None)
        if waiter and not waiter.done():
            waiter.set_result((status, payload))

    async def request(
        self, op: int, payload: bytes = b"", timeout: float = 5.0
    ) -> tuple[int, bytes]:
        """Write one frame and await the response with the same opcode."""
        assert self.client is not None, "not connected"
        frame = bytes([0xAA, self.seq & 0xFF, op, len(payload)]) + payload
        self.seq += 1
        waiter: asyncio.Future[tuple[int, bytes]] = self.loop.create_future()
        self.waiters[op & 0x7F] = waiter
        self.state.log(annotate("->", frame))
        await self.client.write_gatt_char(WRITE_UUID, frame, response=True)
        return await asyncio.wait_for(waiter, timeout)

    async def _open(self, target: Any) -> None:
        """Connect, subscribe to notifications, then send the required handshake."""
        t0 = time.monotonic()
        self.client = await self.link.open(target, self._gone)
        self.state.log(f"connected, discovery took {time.monotonic() - t0:.2f}s")
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        self.seq = 0
        await self.request(HANDSHAKE[2], HANDSHAKE[4:])
        self.state.log(f"handshake done {time.monotonic() - t0:.2f}s after connect")

    async def connect(self) -> None:
        """Find, connect, handshake, then read device state. Retries a flaky link."""
        self.state.update(busy=True, error="")
        try:
            # Find once, then retry _open straight away: a failed attempt has just
            # warmed the BlueZ GATT cache, so the next connect is fast enough to
            # beat the device's handshake deadline.
            target = await self.link.find(self.state.log)
            for attempt in range(1, CONNECT_ATTEMPTS + 1):
                try:
                    await self._open(target)
                    break
                except (TimeoutError, BleakError) as exc:
                    logger.warning(
                        "connect attempt %d/%d failed: %r",
                        attempt,
                        CONNECT_ATTEMPTS,
                        exc,
                    )
                    self.state.log(
                        f"connect attempt {attempt}/{CONNECT_ATTEMPTS} failed: {exc!r}"
                    )
                    await self.disconnect()
                    if attempt == CONNECT_ATTEMPTS:
                        raise
                    self.state.update(
                        error=f"retrying ({attempt}/{CONNECT_ATTEMPTS})... {exc!r}"
                    )
            self.state.update(connected=True, connected_at=time.time(), error="")
            logger.info("connected")
            await self.refresh()
        except Exception as exc:
            logger.exception("connect failed")
            self.state.update(error=str(exc))
            await self.disconnect()
        finally:
            self.state.update(busy=False)

    async def refresh(self) -> None:
        for op in REFRESH_OPS:
            await self.request(op)
            await asyncio.sleep(0.3)

    async def dispense(self, frame: bytes) -> None:
        self.state.update(busy=True, error="")
        try:
            status, payload = await self.request(frame[2], frame[4:], timeout=30.0)
            self.state.update(
                last_dispense={
                    "at": time.time(),
                    "status": status,
                    "ack": payload.hex(),
                }
            )
            await self.refresh()
        except Exception as exc:
            logger.exception("dispense failed")
            self.state.update(error=f"dispense: {exc}")
        finally:
            self.state.update(busy=False)

    async def disconnect(self) -> None:
        client, self.client = self.client, None
        if client and client.is_connected:
            await client.disconnect()
        self._gone()

    def _gone(self) -> None:
        self.state.update(connected=False)
        self.state.log("disconnected")
