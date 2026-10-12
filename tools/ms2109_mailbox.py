#!/usr/bin/env python3
"""
Host side of the MS2109 mailbox (XDATA 0xDE00), for both the A7 firmware
(ms2109/eeprom) and the D7 add-on (ms2109/eeprom_d7).

    python ms2109_mailbox.py identify
    python ms2109_mailbox.py status
    python ms2109_mailbox.py gpio
    python ms2109_mailbox.py i2c-scan
    python ms2109_mailbox.py i2c-read 0x50 0x00

D7 units can run the add-on from RAM without touching the EEPROM, which is
the safe way to try it first. It is gone at the next unplug:

    python ms2109_mailbox.py load       # then identify, gpio, ...
    python ms2109_mailbox.py unload     # or just unplug

Needs `pip install hidapi`.
"""

import argparse
import os
import sys
import time

from ms_hid import MSDevice, chip_name

MBOX = 0xDE00            # +0 cmd, +1/+2 params, +3 status, +4.. response
MBOX_MAGIC = 0xDE0E      # "KD": D7 add-on has initialised the mailbox
STATUS_DONE = 0x02
STATUS_UNKNOWN = 0xFF

# D7 add-on, see ms2109/eeprom_d7/mailbox_d7.asm
HOOK = 0xCC00
BLOB_ADDR = 0xD000
SIGNATURE = b"kD7mb"     # blob +6
CHAIN_OPERAND = 4        # blob +3: ljmp <stock target>
TRAMP_PAGE = 0xD800      # RAM load only: trampoline at 0xD800 | target low

DEFAULT_BLOB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "ms2109", "eeprom_d7", "mailbox_d7.bin")


class MailboxError(Exception):
    pass


def command(d, cmd, p0=0, p1=0, timeout=1.0):
    """Run one mailbox command; returns the 10 response bytes."""
    if d.xdata_read(MBOX) != 0:
        raise MailboxError("mailbox busy, or no mailbox firmware running")
    d.xdata_write(MBOX + 3, 0)
    d.xdata_write(MBOX + 1, p0)
    d.xdata_write(MBOX + 2, p1)
    d.xdata_write(MBOX, cmd)
    end = time.monotonic() + timeout
    while d.xdata_read(MBOX) != 0:
        if time.monotonic() > end:
            d.xdata_write(MBOX, 0)
            raise MailboxError("no response: is the mailbox firmware "
                               "running? (D7: try `load`)")
        time.sleep(0.01)
    status = d.xdata_read(MBOX + 3)
    if status == STATUS_UNKNOWN:
        raise MailboxError(f"command 0x{cmd:02X} not supported by this "
                           f"firmware")
    if status != STATUS_DONE:
        raise MailboxError(f"unexpected status 0x{status:02X}")
    return d.xdata_read_block(MBOX + 4, 10)


def hook_target(d):
    hook = d.xdata_read_block(HOOK, 3)
    if hook[0] != 0x02:
        raise MailboxError(f"unexpected hook at 0xCC00: {hook.hex(' ')}")
    return (hook[1] << 8) | hook[2]


def addon_running(d):
    """'eeprom', 'ram' or None."""
    target = hook_target(d)
    if target == BLOB_ADDR:
        where = "eeprom"
    elif target >> 8 == TRAMP_PAGE >> 8:
        where = "ram"
    else:
        return None
    sig = d.xdata_read_block(BLOB_ADDR + 6, len(SIGNATURE))
    return where if bytes(sig) == SIGNATURE else None


def load(d, blob):
    if d.chip_id() != 0xD7:
        raise MailboxError("`load` is for D7 units only; A7 units run the "
                           "flashed firmware from ms2109/eeprom")
    if blob[6:6 + len(SIGNATURE)] != SIGNATURE:
        raise MailboxError("blob has no signature; rebuild mailbox_d7.bin")
    where = addon_running(d)
    if where:
        raise MailboxError(f"the add-on is already running (from {where})")
    target = hook_target(d)
    if not HOOK < target < BLOB_ADDR:
        raise MailboxError(f"unexpected stock hook target 0x{target:04X}")
    tramp = TRAMP_PAGE | (target & 0xFF)

    blob = bytearray(blob)
    blob[CHAIN_OPERAND:CHAIN_OPERAND + 2] = target.to_bytes(2, "big")
    d.xdata_write_block(BLOB_ADDR, blob)
    jump = bytes([0x02, BLOB_ADDR >> 8, BLOB_ADDR & 0xFF])
    d.xdata_write_block(tramp, jump)
    if (d.xdata_read_block(BLOB_ADDR, len(blob)) != blob
            or d.xdata_read_block(tramp, 3) != jump):
        raise MailboxError("RAM write did not verify; hook left unchanged")
    # Mailbox RAM is random at power-up; start it clean.
    d.xdata_write_block(MBOX, bytes(14) + b"KD")
    # One-byte switch: 0xCC01 high byte of the target -> trampoline page.
    d.xdata_write(HOOK + 1, tramp >> 8)
    time.sleep(0.1)


def unload(d):
    where = addon_running(d)
    if where == "eeprom":
        raise MailboxError("the add-on is in the EEPROM; flash your stock "
                           "backup to remove it")
    if where != "ram":
        raise MailboxError("the add-on is not loaded")
    chain = d.xdata_read_block(BLOB_ADDR + CHAIN_OPERAND, 2)
    if chain[1] != d.xdata_read(HOOK + 2):
        raise MailboxError("hook does not match the loaded add-on; unplug "
                           "the device to reset it")
    d.xdata_write(HOOK + 1, chain[0])


def run(d, args):
    chip = d.chip_id()
    print(f"Chip: {chip_name(chip)}, ROM {d.chip_version()}")

    if args.cmd == "load":
        with open(args.blob, "rb") as f:
            load(d, f.read())
        print("Add-on running from RAM until unplugged.")
        resp = command(d, 0xFE)
        print(f"Identify: {bytes(resp[:6]).decode('ascii', 'replace')}")
    elif args.cmd == "unload":
        unload(d)
        print("Stock hook restored.")
    elif args.cmd == "identify":
        resp = command(d, 0xFE)
        print(f"Identify: {bytes(resp[:6]).decode('ascii', 'replace')}")
    elif args.cmd == "status":
        resp = command(d, 0x01)
        if chip != 0xD7:
            # A7 firmware: DE04 = its signal state machine (0-3)
            print(f"Signal state: {resp[0]}")
        elif not resp[0]:
            print("No valid input signal")
        else:
            w = lambda i: resp[i] << 8 | resp[i + 1]
            print(f"Input: {w(2)}x{w(4)} @ {w(8) / 100:.2f} Hz, "
                  f"pixel clock {w(6) / 100:.2f} MHz")
    elif args.cmd == "gpio":
        resp = command(d, 0x05)
        print(f"P0=0x{resp[0]:02X} P2=0x{resp[1]:02X} P3=0x{resp[2]:02X}")
    elif args.cmd == "i2c-scan":
        bitmap = command(d, 0x12)[0]
        found = [f"0x{0x50 + i:02X}" for i in range(8) if bitmap >> i & 1]
        print("ACK from: " + (", ".join(found) if found else "none"))
    elif args.cmd == "i2c-read":
        resp = command(d, 0x11, args.addr, args.reg)
        # The A7 firmware only returns the value (0xFF on failure).
        if chip == 0xD7 and not resp[1]:
            sys.exit(f"No ACK from 0x{args.addr:02X}")
        print(f"[0x{args.addr:02X}:0x{args.reg:02X}] = 0x{resp[0]:02X}")


def main():
    num = lambda x: int(x, 0)
    ap = argparse.ArgumentParser(description="MS2109 mailbox client")
    ap.add_argument("--vid", type=num)
    ap.add_argument("--pid", type=num)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identify", help="read the firmware signature")
    sub.add_parser("status", help="HDMI input signal status")
    sub.add_parser("gpio", help="read P0, P2, P3")
    sub.add_parser("i2c-scan", help="probe I2C addresses 0x50-0x57")
    p = sub.add_parser("i2c-read", help="read one I2C register")
    p.add_argument("addr", type=num, help="7-bit address")
    p.add_argument("reg", type=num)
    p = sub.add_parser("load", help="D7: run the add-on from RAM")
    p.add_argument("--blob", default=DEFAULT_BLOB)
    sub.add_parser("unload", help="D7: restore the stock hook")
    args = ap.parse_args()
    if (args.vid is None) != (args.pid is None):
        ap.error("--vid and --pid must be given together")

    try:
        with MSDevice(args.vid, args.pid) as d:
            run(d, args)
    except MailboxError as e:
        sys.exit(f"Error: {e}")
    except OSError as e:
        sys.exit(f"Device error: {e}")


if __name__ == "__main__":
    main()
