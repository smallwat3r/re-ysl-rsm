#!/usr/bin/env python3
"""Dump ATT writes/notifications from a btsnoop_hci.log, no Wireshark needed.

    python frames.py [btsnoop_hci.log]

Dispenses are the WRITE rows whose value starts  aa <seq> 26 0e 00aa ...
"""

import struct
import sys

OPS = {0x12: "WRITE   ", 0x52: "WRITE   ", 0x1B: "NOTIFY  ", 0x13: "WRITERSP"}


def main(path):
    with open(path, "rb") as f:
        assert f.read(8) == b"btsnoop\0", "not a btsnoop file"
        f.read(8)
        t0 = None
        while (hdr := f.read(24)) and len(hdr) == 24:
            _, inc_len, _, _, ts = struct.unpack(">IIIIq", hdr)
            d = f.read(inc_len)
            t0 = ts if t0 is None else t0
            # HCI ACL data (type 2), L2CAP CID 4 = ATT
            if d[0] != 2 or len(d) < 10 or struct.unpack("<H", d[7:9])[0] != 4:
                continue
            p = d[9:]
            if p[0] not in OPS:
                continue
            handle = struct.unpack("<H", p[1:3])[0] if len(p) >= 3 else 0
            print(f"{(ts - t0) / 1e6:8.3f} {OPS[p[0]]} h={handle:#06x} {p[3:].hex()}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "btsnoop_hci.log")
