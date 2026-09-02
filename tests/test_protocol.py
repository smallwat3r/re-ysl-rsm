"""Tests for the pure protocol layer: frame building, parsing, log annotation.

All offline, no device or BLE stack. The hex constants are real frames captured
from the app, so these pin the wire format we depend on.
"""

from __future__ import annotations

import pytest

from protocol import (
    MAX_PAYLOAD,
    Op,
    annotate,
    build_dispense,
    build_frame,
    parse_response,
)

# Real frames captured from the app (see README).
RED = "aa00a60e00aaf900b400b700aa00aa00aa00"
PINK = "aa00a60e00aa0e0100000000aa0000000000"
HANDSHAKE_RESP = (
    "03036d00580000004149e30a63bcba15955e001508c7d8bc04d00104e000000008a491bc0"
    "4d00104e0000000083ba84a06d00104e0000000"
)
PRODUCTION_RESP = (
    "0000000056435f3232300000a816da0236325536303000006f49b64d3a4722e3000000004d"
    "415f3532370000a816da0236325536303000007349ba4d1a289ddd000000004d415f3230300"
    "000a816da023632583630310000524c9950da25bde8"
)
USAGE_RESP = (
    "0000aa0065bb31c7654fb64d3ebd476a00001400000041490000aa0165bb31c71e4cba4d3eb"
    "d476a00001400e30a63bc0000aa0265bb31c7634f99503ebd476a00001400ba15955e"
)
DEVICE_INFO_RESP = (
    "4c274f72c3a9616c0052534d0032313336313632334c003300332e313039"
    "00322e31320030004456542d3200"
)


class TestBuildDispense:
    def test_reproduces_captured_red(self) -> None:
        assert build_dispense([249, 180, 183]).hex() == RED

    def test_reproduces_captured_pink_single_cartridge(self) -> None:
        assert build_dispense([270, 0, 0]).hex() == PINK

    def test_unused_cartridges_get_a_zero_flag(self) -> None:
        # slot 0 used -> its flag is 0x00AA, slots 1,2 unused -> 0x0000
        assert build_dispense([100, 0, 0]).hex().endswith("aa0000000000")

    def test_seq_byte_is_written(self) -> None:
        assert build_dispense([1, 0, 0], seq=0x2A)[1] == 0x2A

    @pytest.mark.parametrize("amounts", [[1, 2], [1, 2, 3, 4]])
    def test_wrong_cartridge_count_rejected(self, amounts: list[int]) -> None:
        with pytest.raises(ValueError, match="exactly 3"):
            build_dispense(amounts)

    @pytest.mark.parametrize("amount", [-1, 70000])
    def test_out_of_range_amount_rejected(self, amount: int) -> None:
        with pytest.raises(ValueError, match="0.."):
            build_dispense([amount, 0, 0])

    def test_total_over_safety_cap_rejected(self) -> None:
        assert build_dispense([500, 500, 500])  # 1500 is allowed
        with pytest.raises(ValueError, match="safety cap"):
            build_dispense([500, 500, 501])  # 1501 is not


class TestBuildFrame:
    def test_layout(self) -> None:
        assert build_frame(Op.USAGE, seq=2).hex() == "aa022500"

    def test_payload_length_capped(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            build_frame(Op.DISPENSE, b"\0" * (MAX_PAYLOAD + 1))


class TestParseResponse:
    def test_handshake_lid_count(self) -> None:
        assert (
            parse_response(Op.HANDSHAKE, bytes.fromhex(HANDSHAKE_RESP))["lid_opens"]
            == 88
        )

    def test_production_names_and_batch(self) -> None:
        carts = parse_response(Op.PRODUCTION, bytes.fromhex(PRODUCTION_RESP))[
            "cartridges"
        ]
        assert [c["name"] for c in carts] == ["VC_220", "MA_527", "MA_200"]
        assert carts[2]["batch"] == "62X601"
        assert [c["expires"] for c in carts] == [19894, 19898, 20633]
        assert [c["usable_ml"] for c in carts] == [5.8, 5.8, 5.8]

    def test_usage_lid_timestamp_and_remaining(self) -> None:
        usage = parse_response(Op.USAGE, bytes.fromhex(USAGE_RESP))["usage"]
        assert [u["lid_count"] for u in usage] == [20, 20, 20]
        assert usage[0]["last_use"] == 0x6A47BD3E
        assert [u["opened"] for u in usage] == [True, True, True]
        assert [u["ends"] for u in usage] == [19894, 19898, 20633]
        assert [u["remaining_ml"] for u in usage] == [0.0, 2.787, 5.562]

    def test_usage_never_used_cartridge_is_unopened(self) -> None:
        # a tube the app never wrote back has an all-zero record (live capture)
        usage = parse_response(Op.USAGE, bytes(48) + bytes.fromhex(USAGE_RESP)[48:])
        assert [u["opened"] for u in usage["usage"]] == [False, False, True]

    def test_device_info_strings(self) -> None:
        info = parse_response(Op.DEVICE_INFO, bytes.fromhex(DEVICE_INFO_RESP))
        assert (info["brand"], info["model"]) == ("L'Oréal", "RSM")
        assert (info["fw"], info["hw"], info["variant"]) == ("3.109", "2.12", "DVT-2")

    def test_battery_percent_and_charging_flag(self) -> None:
        # low 7 bits = percent, bit 7 = charging
        assert parse_response(Op.BATTERY, b"\x23") == {"battery": 35, "charging": False}
        assert parse_response(Op.BATTERY, b"\xa2") == {"battery": 34, "charging": True}

    def test_unknown_opcode_is_empty(self) -> None:
        assert parse_response(0x7F, b"\x00") == {}

    @pytest.mark.parametrize(
        "op", [Op.HANDSHAKE, Op.PRODUCTION, Op.USAGE, Op.DEVICE_INFO, Op.BATTERY]
    )
    def test_empty_frame_never_raises(self, op: int) -> None:
        # a malformed / spoofed empty frame must yield {}, not raise into the handler
        assert parse_response(op, b"") == {}

    @pytest.mark.parametrize(
        "op", [Op.HANDSHAKE, Op.PRODUCTION, Op.USAGE, Op.DEVICE_INFO]
    )
    def test_truncated_multibyte_frame_returns_empty(self, op: int) -> None:
        assert parse_response(op, b"\x03\x03") == {}


class TestAnnotate:
    def test_labels_a_read_request(self) -> None:
        assert annotate("->", build_frame(Op.USAGE)) == "-> usage            aa002500"

    def test_decodes_battery_notification(self) -> None:
        assert (
            annotate("<-", bytes.fromhex("aa0040000123"))
            == "<- battery 35%      aa0040000123"
        )

    def test_dispense_ack_vs_error(self) -> None:
        assert "ack" in annotate("<-", bytes.fromhex("aa01a600246400"))
        assert "ERROR" in annotate("<-", bytes.fromhex("aa01a6ff246400"))

    def test_non_frame_is_marked_unknown(self) -> None:
        assert annotate("->", b"\x01\x02").startswith("-> ?")
