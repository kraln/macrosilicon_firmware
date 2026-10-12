#!/usr/bin/env python3
"""
MS2109 "D7" EEPROM image builder

D7 units (ROM D7.10.9A, usually VID 0x345F) run their own stock EEPROM
code, which this repo does not ship. This script takes YOUR stock EEPROM
backup and adds the mailbox add-on (mailbox_d7.bin) on top of it:

  code offset 0x000  stock code, unchanged except the CC00 jump
  code offset 0x400  mailbox add-on (XDATA 0xD000); chains to the stock
                     hook handler for everything it doesn't handle
  code offset 0x600  optional custom EDID (XDATA 0xD200, 256 bytes)

Header changes: header[5] bit 4 is set when an EDID is given (the ROM then
asks the hook for the EDID instead of using its own); header[6:8] and
[8:10] hold an optional VID/PID override (big-endian), which the D7 ROM
applies itself.

Usage:
    python build_d7_image.py stock_backup.bin eeprom_image_d7.bin
    python build_d7_image.py stock_backup.bin out.bin --edid my_edid.bin
    python build_d7_image.py stock_backup.bin out.bin --vid 0x534D --pid 0x2109

Get the backup with: python ../../tools/ms_hid.py backup stock_backup.bin
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "tools"))
from ms_eeprom_checksum import fix_checksums, verify  # noqa: E402

D7_MAGIC = 0x5AA5
HOOK_BASE = 0xCC00
BLOB_OFFSET = 0x400          # -> XDATA 0xD000
EDID_OFFSET = 0x600          # -> XDATA 0xD200 (EDID_HI/LO in mailbox_d7.asm)
MAX_CODE = 2048 - 0x30 - 4
SIGNATURE = b"kD7mb"         # mailbox_d7.asm +0x06
CHAIN_OPERAND = 4            # mailbox_d7.asm +0x03: ljmp <stock target>


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


def build(stock, blob, edid=None, vid=None, pid=None):
    """Return (image, notes) or raise ValueError."""
    notes = []
    if len(stock) != 2048:
        raise ValueError(f"stock image must be 2048 bytes, got {len(stock)}")
    magic = (stock[0] << 8) | stock[1]
    if magic != D7_MAGIC:
        raise ValueError(f"magic 0x{magic:04X} is not a D7 image (expected "
                         f"0x5AA5); use your own D7 EEPROM backup")
    ok, d = verify(stock)
    if not ok:
        raise ValueError("stock image checksums are wrong; is the backup "
                         "complete?")
    length = d["code_length"]
    code = bytearray(stock[0x30:0x30 + length])

    if code[0] != 0x02:
        raise ValueError("stock code does not start with the CC00 hook LJMP")
    target = (code[1] << 8) | code[2]
    if code[BLOB_OFFSET + 6:BLOB_OFFSET + 6 + len(SIGNATURE)] == SIGNATURE:
        raise ValueError("this image already contains the mailbox add-on; "
                         "start from your original stock backup")
    if not HOOK_BASE <= target < HOOK_BASE + length:
        raise ValueError(f"CC00 jumps to 0x{target:04X}, outside the stock "
                         f"code; unexpected stock image")
    if not (stock[4] & 0x01):
        raise ValueError("header[4] bit 0 (CC00 hook) is clear in the stock "
                         "image; unexpected stock image")
    if any(b != 0xFF for b in code[BLOB_OFFSET:]):
        raise ValueError(f"stock code is longer than 0x{BLOB_OFFSET:X} "
                         f"bytes; no room for the add-on at a fixed address")
    del code[BLOB_OFFSET:]

    if blob[6:6 + len(SIGNATURE)] != SIGNATURE:
        raise ValueError("mailbox_d7.bin has no signature; rebuild it")
    if len(blob) > EDID_OFFSET - BLOB_OFFSET:
        raise ValueError("mailbox_d7.bin overlaps the EDID area")
    blob = bytearray(blob)
    blob[CHAIN_OPERAND:CHAIN_OPERAND + 2] = target.to_bytes(2, "big")

    code += b"\xFF" * (BLOB_OFFSET - len(code))
    code[1:3] = (HOOK_BASE + BLOB_OFFSET).to_bytes(2, "big")
    code += blob
    header = bytearray(stock[:0x30])
    if edid is not None:
        code += b"\xFF" * (EDID_OFFSET - len(code))
        code += check_edid(edid)
        header[5] |= 0x10
        notes.append("custom EDID at 0xD200, header[5] bit 4 set")
    if len(code) > MAX_CODE:
        raise ValueError("image too large")
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
    notes.insert(0, f"stock hook target 0x{target:04X}, stock code "
                    f"{length} bytes, new code length {len(code)} bytes")
    return image, notes


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Build an MS2109 D7 EEPROM "
                                 "image from your stock backup")
    ap.add_argument("stock", help="your stock EEPROM backup (2048 bytes)")
    ap.add_argument("output")
    ap.add_argument("--blob", default=os.path.join(here, "mailbox_d7.bin"),
                    help="add-on binary (default: mailbox_d7.bin, from make)")
    ap.add_argument("--edid", help="custom EDID, 128 or 256 bytes")
    ap.add_argument("--vid", type=lambda x: int(x, 0))
    ap.add_argument("--pid", type=lambda x: int(x, 0))
    args = ap.parse_args()

    with open(args.stock, "rb") as f:
        stock = f.read()
    with open(args.blob, "rb") as f:
        blob = f.read()
    edid = None
    if args.edid:
        with open(args.edid, "rb") as f:
            edid = f.read()
    try:
        image, notes = build(stock, blob, edid, args.vid, args.pid)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    with open(args.output, "wb") as f:
        f.write(image)
    for n in notes:
        print(n)
    print(f"Wrote {args.output}. Flash with: "
          f"python ../../tools/ms_hid.py flash {args.output}")


if __name__ == "__main__":
    main()
