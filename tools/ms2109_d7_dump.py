"""
Dump the mask ROM (CODE space) of an MS2109 "D7" variant via MOVC.

Some MS2109 dongles (often VID 0x345F, chip marked MS2109"S") report chip ID
0xD7 at XDATA 0xF800 ("D7.10.9A") instead of 0xA7. They run a different mask
ROM, so the A7 firmware in this repo does not work on them. This script
collects what is needed to port it: the CODE space, the EEPROM, and a record
of which hook commands the ROM issues.

How it works (nothing is written to the EEPROM; a power cycle undoes it all):

  1. Upload a small 8051 handler into unused XDATA RAM (0xD800 page).
  2. Retarget the EEPROM hook at XDATA 0xCC00 (stock: LJMP 0xCD6E) to the
     handler. The handler sits at 0xD8xx with the same low byte as the
     original target, so the switch is a single byte write (no window where
     the LJMP points at garbage).
  3. Every time the ROM calls the hook, the handler counts the call, records
     the command ID (R7), services one MOVC copy request from a mailbox,
     restores R7 and jumps to the original stock handler.
  4. The host requests 128-byte chunks and checks each one against an 8-bit
     sum computed on the device; chunks alternate between two buffers so a
     stale buffer can't be mistaken for fresh data.

Requirements: Python 3, `pip install hidapi`. On Linux, run as root or add a
udev rule for the device. Close OBS / any app using the device first.

Usage:
    python ms2109_d7_dump.py                  # probe, then dump CODE 0x0000-0xBFFF
    python ms2109_d7_dump.py --probe-only     # only check that the hook fires
    python ms2109_d7_dump.py --vid 0x534D     # pick the VID (default: try both)

Outputs (prefix set with -o, default "ms2109_d7"):
    <prefix>_code.bin     CODE space via MOVC
    <prefix>_eeprom.bin   2KB EEPROM (read-only HID command 0xE5)
    <prefix>_info.txt     chip ID, hook statistics, checksums
"""

import argparse
import hashlib
import sys
import time

try:
    import hid
except ImportError:
    sys.exit("Missing dependency: pip install hidapi")


HOOK_ADDR = 0xCC00          # EEPROM normal hook (stock D7: LJMP 0xCD6E)
STOCK_HOOK_TARGET = 0xCD6E
HANDLER_PAGE = 0xD800       # handler lives at 0xD800 | low byte of target
MAILBOX = 0xD7F0            # see build_handler() for layout
HIST_PAGE = 0xDA00          # 256-byte histogram of hook command IDs (R7)
BUFFERS = (0xD000, 0xD080)  # alternating MOVC destination buffers
CHUNK = 0x80

# Mailbox layout (offsets from MAILBOX)
MB_COUNT = 0x0      # bytes to copy; host writes last, handler clears when done
MB_SRC = 0x1        # CODE source address, big-endian
MB_DST = 0x3        # XDATA destination address, big-endian
MB_SUM = 0x5        # 8-bit sum of the copied bytes
MB_DONE = 0x6       # incremented after every completed copy
MB_CALLS = 0x8      # 16-bit little-endian hook call counter
MB_LASTCMD = 0xA    # R7 of the most recent hook call
MB_SCRATCH = 0xF    # used to test that XDATA writes work


def build_handler(orig_target):
    """Assemble the hook handler. Position independent apart from data
    addresses; ends with LJMP orig_target."""
    code = bytearray()
    labels, fixups = {}, []

    def emit(*b):
        code.extend(b)

    def rel(label):             # placeholder for an 8-bit relative offset
        fixups.append((len(code), label))
        code.append(0)

    def mov_dptr(addr):
        emit(0x90, addr >> 8, addr & 0xFF)

    emit(0xEF)                          # MOV A,R7       save hook command
    emit(0xC0, 0xE0)                    # PUSH ACC
    # histogram[R7]++ (saturating at 0xFF)
    emit(0x75, 0x83, HIST_PAGE >> 8)    # MOV DPH,#HIST_PAGE
    emit(0x8F, 0x82)                    # MOV DPL,R7
    emit(0xE0, 0x04)                    # MOVX A,@DPTR; INC A
    emit(0x60); rel("hist_sat")         # JZ hist_sat
    emit(0xF0)                          # MOVX @DPTR,A
    labels["hist_sat"] = len(code)
    # calls++ (16-bit LE)
    mov_dptr(MAILBOX + MB_CALLS)
    emit(0xE0, 0x04, 0xF0)              # MOVX A,@DPTR; INC A; MOVX @DPTR,A
    emit(0x70); rel("calls_done")       # JNZ calls_done
    emit(0xA3, 0xE0, 0x04, 0xF0)        # INC DPTR; high byte++
    labels["calls_done"] = len(code)
    mov_dptr(MAILBOX + MB_LASTCMD)
    emit(0xEF, 0xF0)                    # MOV A,R7; MOVX @DPTR,A
    # mailbox request?
    mov_dptr(MAILBOX + MB_COUNT)
    emit(0xE0)                          # MOVX A,@DPTR
    emit(0x60); rel("done")             # JZ done
    emit(0xFF)                          # MOV R7,A       count
    emit(0xA3, 0xE0, 0xFC)              # R4 = src_hi
    emit(0xA3, 0xE0, 0xFD)              # R5 = src_lo
    emit(0xA3, 0xE0, 0xFA)              # R2 = dst_hi
    emit(0xA3, 0xE0, 0xFB)              # R3 = dst_lo
    emit(0x7E, 0x00)                    # MOV R6,#0      running sum
    labels["copy"] = len(code)
    emit(0x8C, 0x83, 0x8D, 0x82)        # DPTR = R4:R5
    emit(0xE4, 0x93)                    # CLR A; MOVC A,@A+DPTR
    emit(0x8A, 0x83, 0x8B, 0x82)        # DPTR = R2:R3
    emit(0xF0)                          # MOVX @DPTR,A
    emit(0x2E, 0xFE)                    # ADD A,R6; MOV R6,A
    emit(0x0D)                          # INC R5
    emit(0xBD, 0x00); rel("src_ok")     # CJNE R5,#0,src_ok
    emit(0x0C)                          # INC R4
    labels["src_ok"] = len(code)
    emit(0x0B)                          # INC R3
    emit(0xBB, 0x00); rel("dst_ok")     # CJNE R3,#0,dst_ok
    emit(0x0A)                          # INC R2
    labels["dst_ok"] = len(code)
    emit(0xDF); rel("copy")             # DJNZ R7,copy
    mov_dptr(MAILBOX + MB_SUM)
    emit(0xEE, 0xF0)                    # MOV A,R6; MOVX @DPTR,A
    emit(0xA3, 0xE0, 0x04, 0xF0)        # done counter++
    mov_dptr(MAILBOX + MB_COUNT)
    emit(0xE4, 0xF0)                    # CLR A; MOVX @DPTR,A   (signal done)
    labels["done"] = len(code)
    emit(0xD0, 0xE0)                    # POP ACC
    emit(0xFF)                          # MOV R7,A       restore hook command
    emit(0x02, orig_target >> 8, orig_target & 0xFF)  # LJMP original

    for pos, label in fixups:
        off = labels[label] - (pos + 1)
        assert -128 <= off <= 127, label
        code[pos] = off & 0xFF
    return bytes(code)


class Device:
    def __init__(self, vid, pid):
        self.dev = hid.device()
        self.dev.open(vid, pid)

    def close(self):
        self.dev.close()

    def _xfer(self, cmd, addr, val=0):
        self.dev.send_feature_report(
            [0x00, cmd, (addr >> 8) & 0xFF, addr & 0xFF, val, 0, 0, 0, 0])

    def xread(self, addr):
        self._xfer(0xB5, addr)
        return self.dev.get_feature_report(0, 9)[4]

    def xwrite(self, addr, val):
        self._xfer(0xB6, addr, val)

    def xread_block(self, addr, n):
        return bytes(self.xread(addr + i) for i in range(n))

    def xwrite_block(self, addr, data):
        for i, b in enumerate(data):
            self.xwrite(addr + i, b)

    def eeprom_read(self, addr):
        self._xfer(0xE5, addr)
        return self.dev.get_feature_report(0, 9)[4]


class Dumper:
    def __init__(self, dev, orig_target):
        self.dev = dev
        self.orig_target = orig_target
        self.handler_addr = HANDLER_PAGE | (orig_target & 0xFF)
        self.handler = build_handler(orig_target)
        self.patched = False
        self.buf_idx = 0

    def install(self):
        d = self.dev
        d.xwrite_block(MAILBOX, bytes(16))
        d.xwrite_block(HIST_PAGE, bytes(256))
        d.xwrite_block(self.handler_addr, self.handler)
        if d.xread_block(self.handler_addr, len(self.handler)) != self.handler:
            raise RuntimeError("handler readback mismatch; not patching hook")
        # Single-byte switch: LJMP CDxx -> LJMP D8xx
        d.xwrite(HOOK_ADDR + 1, self.handler_addr >> 8)
        self.patched = True
        if d.xread(HOOK_ADDR + 1) != self.handler_addr >> 8:
            raise RuntimeError("hook patch did not stick")

    def uninstall(self):
        if self.patched:
            self.dev.xwrite(HOOK_ADDR + 1, self.orig_target >> 8)
            self.patched = False

    def stats(self):
        mb = self.dev.xread_block(MAILBOX, 16)
        calls = mb[MB_CALLS] | (mb[MB_CALLS + 1] << 8)
        return calls, mb[MB_LASTCMD]

    def histogram(self):
        hist = self.dev.xread_block(HIST_PAGE, 256)
        return {cmd: n for cmd, n in enumerate(hist) if n}

    def read_chunk(self, src, n, retries=3):
        d = self.dev
        for attempt in range(retries):
            dst = BUFFERS[self.buf_idx]
            self.buf_idx ^= 1
            # Poison the first byte so a skipped copy is visible
            d.xwrite(dst, 0xFF ^ d.xread(dst))
            d.xwrite_block(MAILBOX + MB_SRC,
                           [src >> 8, src & 0xFF, dst >> 8, dst & 0xFF])
            d.xwrite(MAILBOX + MB_COUNT, n)           # trigger
            deadline = time.time() + 2.0
            while d.xread(MAILBOX + MB_COUNT) != 0:
                if time.time() > deadline:
                    raise RuntimeError(
                        f"no response at CODE 0x{src:04X}: hook stopped firing")
                time.sleep(0.001)
            data = d.xread_block(dst, n)
            if sum(data) & 0xFF == d.xread(MAILBOX + MB_SUM):
                return data
            print(f"  checksum mismatch at 0x{src:04X}, retry {attempt + 1}")
        raise RuntimeError(f"CODE 0x{src:04X}: checksum failed {retries} times")


def sanity_report(code):
    lines = []
    counts = [0] * 256
    for b in code:
        counts[b] += 1
    missing = [v for v in range(256) if counts[v] == 0]
    lines.append(f"byte values never seen: {len(missing)}")
    if len(missing) > 16:
        lines.append("  WARNING: many byte values missing - dump may be bad")
    if code[:1] != b"\x02":
        lines.append("  WARNING: CODE[0] is not LJMP (0x02)")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vid", type=lambda x: int(x, 0),
                    help="default: try 0x345F, then 0x534D")
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=0x2109)
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0x0000)
    ap.add_argument("--length", type=lambda x: int(x, 0), default=0xC000)
    ap.add_argument("-o", "--prefix", default="ms2109_d7")
    ap.add_argument("--probe-only", action="store_true",
                    help="install handler, report hook activity, uninstall")
    ap.add_argument("--no-eeprom", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="continue even if chip ID or hook is not stock D7")
    args = ap.parse_args()

    info = []

    def log(msg=""):
        print(msg)
        info.append(msg)

    # D7 units have been seen with both VIDs; try each unless one was given
    dev = None
    for vid in [args.vid] if args.vid is not None else [0x345F, 0x534D]:
        try:
            dev = Device(vid, args.pid)
            args.vid = vid
            break
        except OSError as e:
            err = e
    if dev is None:
        sys.exit(f"Could not open the device (PID {args.pid:04X}): {err}\n"
                 "Check the VID/PID, close apps using the device, and on "
                 "Linux run as root.")

    dumper = None
    try:
        log(f"Device: {dev.dev.get_manufacturer_string()} / "
            f"{dev.dev.get_product_string()} ({args.vid:04X}:{args.pid:04X})")
        chip = dev.xread_block(0xF800, 3)
        log(f"Chip version (XDATA F800): {chip[0]:02X}.{chip[1]:02X}.{chip[2]:02X}")
        hdr = dev.xread_block(0xCBD0, 16)
        log(f"EEPROM header in RAM (CBD0): {hdr.hex(' ')}")
        hook = dev.xread_block(HOOK_ADDR, 3)
        log(f"Hook at 0x{HOOK_ADDR:04X}: {hook.hex(' ')}")

        if chip[0] != 0xD7 and not args.force:
            sys.exit(f"Chip ID 0x{chip[0]:02X} is not 0xD7; this tool is for "
                     "the D7 variant (use --force to override).")
        if hook[0] != 0x02:
            sys.exit("Hook is not an LJMP - the EEPROM is not stock or the "
                     "hook is disabled. Restore the stock EEPROM first.")
        orig = (hook[1] << 8) | hook[2]
        if orig != STOCK_HOOK_TARGET and not args.force:
            sys.exit(f"Hook target 0x{orig:04X} differs from stock 0x"
                     f"{STOCK_HOOK_TARGET:04X} (use --force to override).")

        # Check XDATA writes work before touching anything that matters
        for v in (0xA5, 0x5A):
            dev.xwrite(MAILBOX + MB_SCRATCH, v)
            if dev.xread(MAILBOX + MB_SCRATCH) != v:
                sys.exit("XDATA write (HID 0xB6) does not work on this device.")

        dumper = Dumper(dev, orig)
        log(f"Installing {len(dumper.handler)}-byte handler at "
            f"0x{dumper.handler_addr:04X}")
        dumper.install()

        time.sleep(2.0)
        calls, last = dumper.stats()
        hist = dumper.histogram()
        log(f"Hook calls in ~2s: {calls} (last cmd 0x{last:02X})")
        log("Hook command histogram (R7: count, saturates at 255): " +
            ", ".join(f"0x{c:02X}:{n}" for c, n in sorted(hist.items())))

        if calls == 0:
            log("The hook did not fire. Try again with an HDMI source "
                "connected and the video stream open (e.g. in OBS).")
            return
        if args.probe_only:
            return

        log(f"Dumping CODE 0x{args.start:04X}-"
            f"0x{args.start + args.length - 1:04X}...")
        code = bytearray()
        t0 = time.time()
        for off in range(0, args.length, CHUNK):
            n = min(CHUNK, args.length - off)
            code += dumper.read_chunk(args.start + off, n)
            if off and off % 0x1000 == 0:
                el = time.time() - t0
                print(f"  0x{args.start + off:04X}  {el:.0f}s elapsed, "
                      f"~{el / off * (args.length - off):.0f}s left")
        dumper.uninstall()

        log(f"Dump finished in {time.time() - t0:.0f}s")
        log("Hook command histogram after dump: " + ", ".join(
            f"0x{c:02X}:{n}" for c, n in sorted(dumper.histogram().items())))
        with open(f"{args.prefix}_code.bin", "wb") as f:
            f.write(code)
        log(f"Wrote {args.prefix}_code.bin ({len(code)} bytes, "
            f"start 0x{args.start:04X}) sha256 "
            f"{hashlib.sha256(code).hexdigest()}")
        for line in sanity_report(code):
            log(line)

        if not args.no_eeprom:
            print("Reading EEPROM (2KB)...")
            ee = bytes(dev.eeprom_read(a) for a in range(2048))
            with open(f"{args.prefix}_eeprom.bin", "wb") as f:
                f.write(ee)
            log(f"Wrote {args.prefix}_eeprom.bin sha256 "
                f"{hashlib.sha256(ee).hexdigest()}")
    finally:
        if dumper is not None:
            dumper.uninstall()
            print("Hook restored.")
        dev.close()
        if info:
            with open(f"{args.prefix}_info.txt", "w") as f:
                f.write("\n".join(info) + "\n")
            print(f"Wrote {args.prefix}_info.txt")


if __name__ == "__main__":
    main()
