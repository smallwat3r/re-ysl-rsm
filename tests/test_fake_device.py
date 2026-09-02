"""Tests for the simulated device: its frames decode with the real parsers, and a
whole session (connect, handshake, refresh, dispense) runs through `Device`."""

from __future__ import annotations

import fake_device
from device import Device
from fake_device import STATUS_ERROR, STATUS_OK, FakeDevice, FakeLink
from protocol import Op, build_dispense, build_frame, parse_response


def ask(dev: FakeDevice, frame: bytes) -> tuple[int, dict]:
    reply = dev.handle(frame)
    return reply.status, parse_response(frame[2] & 0x7F, reply.payload)


def test_replies_decode_with_the_real_parsers() -> None:
    dev = FakeDevice(names=("MA_100", "VC_201", "MA_513"))
    assert ask(dev, build_frame(Op.HANDSHAKE))[1]["lid_opens"] == 88
    carts = ask(dev, build_frame(Op.PRODUCTION))[1]["cartridges"]
    assert [c["name"] for c in carts] == ["MA_100", "VC_201", "MA_513"]
    assert [c["usable_ml"] for c in carts] == [5.8, 5.8, 5.8]
    assert ask(dev, build_frame(Op.DEVICE_INFO))[1]["model"] == "RSM"
    assert ask(dev, build_frame(Op.BATTERY))[1] == {"battery": 87, "charging": False}
    assert ask(dev, build_frame(0x7F))[0] == STATUS_ERROR  # unknown opcode


def test_dispense_opens_tubes_and_draws_them_down() -> None:
    dev = FakeDevice()
    # slot 0 starts factory-fresh, the other two pre-worn
    assert [u["opened"] for u in ask(dev, build_frame(Op.USAGE))[1]["usage"]] == [
        False,
        True,
        True,
    ]
    assert ask(dev, build_dispense([249, 180, 0]))[0] == STATUS_OK
    usage = ask(dev, build_frame(Op.USAGE))[1]["usage"]
    assert [u["opened"] for u in usage] == [True, True, True]
    assert usage[0]["remaining_ml"] == round(5.8 - 249 * fake_device.ML_PER_UNIT, 3)
    dev.cartridges[2].remaining_ml = 0.0
    assert ask(dev, build_dispense([0, 0, 1]))[0] == STATUS_ERROR  # would run dry


def test_full_session_through_device(monkeypatch) -> None:
    monkeypatch.setattr(fake_device, "CONNECT_S", 0.0)
    monkeypatch.setattr(fake_device, "PUMP_UNITS_PER_S", 1e6)
    device = Device(FakeLink())
    device.run(device.connect()).result(timeout=10)
    state = device.state.snapshot()
    assert state["connected"] and not state["error"]
    assert [c["name"] for c in state["cartridges"]] == ["VC_220", "MA_527", "MA_200"]
    assert state["variant"] == "MOCK"

    device.run(device.dispense(build_dispense([100, 100, 100]))).result(timeout=10)
    state = device.state.snapshot()
    assert state["last_dispense"]["status"] == STATUS_OK
    assert all(u["opened"] for u in state["usage"])

    device.run(device.disconnect()).result(timeout=10)
    assert device.state.get("connected") is False
