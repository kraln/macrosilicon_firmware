#!/usr/bin/env python3
"""
MS2109 "D7" EEPROM image builder

Wraps firmware_d7.bin (linked at XDATA 0xCC00) in a complete 2048-byte
EEPROM image for D7 units (ROM D7.10.9A, usually VID 0x345F):

  0x00-0x2F  header: magic 0x5AA5, code length, hook config, optional
             VID/PID override, ROM-default USB strings
  0x30-      firmware_d7.bin, then the optional EDID (256 bytes)
  after      header and code checksums

With --edid, header[5] bit 4 is set so the ROM asks the firmware for the
EDID instead of using its own, and the EDID's XDATA address is written to
the firmware's edid_ptr (+0x09). --vid/--pid go in header[6:10]
(big-endian); the D7 ROM applies them itself.

Usage:
    python build_d7_image.py firmware_d7.bin eeprom_image_d7.bin
    python build_d7_image.py firmware_d7.bin out.bin --edid my_edid.bin
    python build_d7_image.py firmware_d7.bin out.bin --vid 0x534D --pid 0x2109
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "tools"))
from ms_eeprom_checksum import fix_checksums, verify  # noqa: E402

CODE_BASE = 0xCC00
MAX_CODE = 2048 - 0x30 - 4
SIGNATURE = b"kD7fw"         # firmware_d7.asm +0x03
EDID_PTR = 9                 # firmware_d7.asm +0x09: EDID address, BE

HOOKS = 0x01                 # header[4]: CC00 hook
HOOK_CMDS = 0x40             # header[5]: bit 6 = R7 0x17 mode setup
HOOK_CMD_EDID = 0x10         # header[5]: bit 4 = R7 0x08 EDID load

HEADER = bytes([
    0x5A, 0xA5,              # magic (D7 ROM)
    0x00, 0x00,              # code length (filled in)
    HOOKS, HOOK_CMDS,
    0xFF, 0xFF,              # VID override (FFFF = ROM default)
    0xFF, 0xFF,              # PID override
    0xFF, 0xFF,
    0x18, 0x09, 0x06, 0x11,  # config bytes, as on stock D7 units
]) + b"\xFF" * 32            # USB strings (FF = ROM default)


def check_edid(edid):
    if len(edid) not in (128, 256):
        raise ValueError(f"EDID must be 128 or 256 bytes, got {len(edid)}")
    if edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        raise ValueError("EDID header (00 FF FF FF FF FF FF 00) missing")
    for blk in range(0, len(edid), 128):
        if sum(edid[blk:blk + 128]) & 0xFF:
            raise ValueError(f"EDID block {blk // 128} checksum is wrong")
    if len(edid) == 128 and edid[126]:
        raise ValueError("EDID says it has extension blocks but only 128 "
                         "bytes were given")
    return edid + bytes(256 - len(edid))


def build(firmware, edid=None, vid=None, pid=None):
    """Return (image, notes) or raise ValueError."""
    if firmware[3:3 + len(SIGNATURE)] != SIGNATURE:
        raise ValueError("firmware has no kD7fw signature; is this "
                         "firmware_d7.bin?")
    notes = [f"firmware {len(firmware)} bytes"]
    code = bytearray(firmware)
    header = bytearray(HEADER)
    if edid is not None:
        addr = CODE_BASE + len(code)
        code[EDID_PTR:EDID_PTR + 2] = addr.to_bytes(2, "big")
        code += check_edid(edid)
        header[5] |= HOOK_CMD_EDID
        notes.append(f"custom EDID at 0x{addr:04X}")
    if len(code) > MAX_CODE:
        raise ValueError(f"image too large: code {len(code)} bytes, at most "
                         f"{MAX_CODE} fit")
    if vid is not None:
        header[6:8] = vid.to_bytes(2, "big")
        notes.append(f"VID override 0x{vid:04X}")
    if pid is not None:
        header[8:10] = pid.to_bytes(2, "big")
        notes.append(f"PID override 0x{pid:04X}")
    header[2:4] = len(code).to_bytes(2, "big")

    image = bytearray(b"\xFF" * 2048)
    image[:0x30] = header
    image[0x30:0x30 + len(code)] = code
    image = fix_checksums(image)
    if not verify(image)[0]:
        raise ValueError("checksum verification failed")
    return image, notes


def main():
    ap = argparse.ArgumentParser(description="Build an MS2109 D7 EEPROM "
                                 "image")
    ap.add_argument("firmware", help="firmware_d7.bin, from make")
    ap.add_argument("output")
    ap.add_argument("--edid", help="custom EDID, 128 or 256 bytes")
    ap.add_argument("--vid", type=lambda x: int(x, 0))
    ap.add_argument("--pid", type=lambda x: int(x, 0))
    args = ap.parse_args()

    with open(args.firmware, "rb") as f:
        firmware = f.read()
    edid = None
    if args.edid:
        with open(args.edid, "rb") as f:
            edid = f.read()
    try:
        image, notes = build(firmware, edid, args.vid, args.pid)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    with open(args.output, "wb") as f:
        f.write(image)
    for n in notes:
        print(n)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
