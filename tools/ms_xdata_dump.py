"""
Dump the full 64KB XDATA space from a Macrosilicon device via HID.

Reads one byte at a time via command 0xB5. Takes ~6-7 minutes for 64KB.
Note: 0xB5 returns 1 byte per read (at resp[4]), NOT 5 like 0xE5.

Usage:
    python ms_xdata_dump.py                              # auto-detect, dump to MS_XDATA.bin
    python ms_xdata_dump.py -o xdata.bin                 # custom output
    python ms_xdata_dump.py --vid 0x0002 --pid 0xAFA1    # specify device
"""

import hid
import time
import argparse

from ms_hid import find_device


def dump_xdata(vid, pid, size=65536):
    d = hid.device()
    d.open(vid, pid)

    info = d.get_manufacturer_string()
    product = d.get_product_string()
    print(f"Connected: {info} {product}")
    print(f"Dumping {size} bytes of XDATA...")

    xdata = bytearray(size)
    start = time.time()

    for addr in range(size):
        d.send_feature_report(
            [0x00, 0xB5, (addr >> 8) & 0xFF, addr & 0xFF, 0, 0, 0, 0, 0])
        resp = d.get_feature_report(0, 9)
        xdata[addr] = resp[4]  # Only byte [4] is valid XDATA

        if addr % 8192 == 0 and addr > 0:
            elapsed = time.time() - start
            rate = addr / elapsed
            eta = (size - addr) / rate
            print(f"  {addr:5d}/{size} ({100*addr/size:.0f}%) "
                  f"- {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining")

    elapsed = time.time() - start
    print(f"Done in {elapsed:.1f}s")

    d.close()
    return xdata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump XDATA via HID 0xB5")
    parser.add_argument("-o", "--output", default="MS_XDATA.bin")
    parser.add_argument("--vid", type=lambda x: int(x, 0))
    parser.add_argument("--pid", type=lambda x: int(x, 0))
    parser.add_argument("--size", type=lambda x: int(x, 0), default=65536)
    args = parser.parse_args()
    if args.vid is None or args.pid is None:
        args.vid, args.pid = find_device()

    xdata = dump_xdata(args.vid, args.pid, args.size)

    with open(args.output, "wb") as f:
        f.write(xdata)
    print(f"Saved {len(xdata)} bytes to {args.output}")

    # Quick stats
    non_zero = sum(1 for b in xdata if b != 0x00)
    non_ff = sum(1 for b in xdata if b != 0xFF)
    print(f"Non-zero: {non_zero}/{len(xdata)}, Non-FF: {non_ff}/{len(xdata)}")
