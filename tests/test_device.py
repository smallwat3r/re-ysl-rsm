"""Tests for the thread-safe observable state store (no BLE, no HTTP)."""

from __future__ import annotations

from device import LOG_LINES, StateStore


def test_update_and_get() -> None:
    s = StateStore()
    s.update(connected=True, battery=42)
    assert s.get("connected") is True
    assert s.get("battery") == 42
    assert s.get("missing") is None


def test_log_is_a_capped_ring_buffer() -> None:
    s = StateStore()
    for i in range(LOG_LINES + 20):
        s.log(f"line {i}")
    log = s.snapshot()["log"]
    assert len(log) == LOG_LINES
    assert log[-1].endswith(f"line {LOG_LINES + 19}")


def test_snapshot_merges_extra_without_persisting_it() -> None:
    s = StateStore()
    s.update(connected=True)
    snap = s.snapshot(colours={"x": [1, 2, 3]})
    assert snap["connected"] is True
    assert snap["colours"] == {"x": [1, 2, 3]}
    assert "colours" not in s.snapshot()  # the extra was not stored


def test_subscriber_notified_only_while_subscribed() -> None:
    s = StateStore()
    ev = s.subscribe()
    assert not ev.is_set()
    s.update(busy=True)
    assert ev.is_set()

    s.unsubscribe(ev)
    ev.clear()
    s.update(busy=False)
    assert not ev.is_set()  # no wake-ups after unsubscribe
