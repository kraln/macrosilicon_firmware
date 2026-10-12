"""
Low-level HID helpers for Macrosilicon MS2107/MS2109/MS9123 devices.

Provides byte-level EEPROM (E5/E6) and XDATA (B5/B6) access over
9-byte HID feature reports. Works on Windows with the `hidapi` package.

Usage:
    from ms_hid import MSDevice
    with MSDevice() as d:                  # first known device found
        val = d.xdata_read(0xC617)
        d.xdata_write(0xC617, val | 0x20)
        eeprom = d.eeprom_dump()

    with MSDevice(vid=0x534D, pid=0x6021) as d:   # or a specific one
        ...

Command line:
    python ms_hid.py info                  # identify the connected device
    python ms_hid.py backup eeprom.bin     # save the current EEPROM
    python ms_hid.py flash eeprom_image.bin
    python ms_hid.py --vid 0x534D --pid 0x2109 flash eeprom_image.bin
"""

import argparse
import sys
import time

import hid


# USB IDs seen on these chips. MS2109 units ship with either VID.
KNOWN_USB_IDS = {
    (0x534D, 0x2109): "MS2109",
    (0x345F, 0x2109): "MS2109",
    (0x534D, 0x6021): "MS9123",
    (0x534D, 0x0021): "MS2107",
    (0x0002, 0xAFA1): "MS2107",
}

# XDATA[0xF800] -> chip, and the EEPROM magics that chip's ROM loads.
# MS2109 "D7" runs a newer mask ROM; see ms2109/eeprom_d7.
CHIP_IDS = {
    0xA7: ("MS2109", {0xA55A, 0x9669}),
    0xD7: ("MS2109 (D7 ROM)", {0x5AA5}),
    0xFF: ("MS2107", {0x0816, 0x3264}),
    0x00: ("MS9123", {0xA55A}),
}


def find_devices():
    """Return [(vid, pid, name)] for connected devices with known USB IDs."""
    found = []
    for d in hid.enumerate():
        key = (d["vendor_id"], d["product_id"])
        if key in KNOWN_USB_IDS and (*key, KNOWN_USB_IDS[key]) not in found:
            found.append((*key, KNOWN_USB_IDS[key]))
    return found


def find_device():
    """Return (vid, pid) of the single connected known device."""
    found = find_devices()
    if not found:
        raise OSError("no known Macrosilicon device found "
                      "(pass vid/pid if it uses a custom USB ID)")
    if len(found) > 1:
        ids = ", ".join(f"{v:04X}:{p:04X} ({n})" for v, p, n in found)
        raise OSError(f"several devices found, pick one with vid/pid: {ids}")
    return found[0][:2]


class MSDevice:
    """HID interface to a Macrosilicon capture device."""

    def __init__(self, vid=None, pid=None, path=None):
        self.dev = hid.device()
        self.vid = vid
        self.pid = pid
        self.path = path

    def __enter__(self):
        if self.path:
            self.dev.open_path(self.path)
        else:
            if self.vid is None and self.pid is None:
                self.vid, self.pid = find_device()
            self.dev.open(self.vid, self.pid)
        return self

    def __exit__(self, *args):
        self.dev.close()

    # -- raw HID helpers --

    def _send(self, cmd, addr, data_byte=0x00):
        report = [0x00, cmd, (addr >> 8) & 0xFF, addr & 0xFF,
                  data_byte, 0x00, 0x00, 0x00, 0x00]
        self.dev.send_feature_report(report)

    def _recv(self):
        return self.dev.get_feature_report(0, 9)

    # -- XDATA (B5/B6) --

    def xdata_read(self, addr):
        """Read one byte from XDATA."""
        self._send(0xB5, addr)
        return self._recv()[4]

    def xdata_write(self, addr, val):
        """Write one byte to XDATA."""
        self._send(0xB6, addr, val)

    def xdata_read_block(self, addr, length):
        """Read a block of XDATA bytes (1 byte per HID transaction)."""
        buf = bytearray(length)
        for i in range(length):
            buf[i] = self.xdata_read(addr + i)
        return buf

    def xdata_write_block(self, addr, data):
        """Write a block of bytes to XDATA."""
        for i, b in enumerate(data):
            self.xdata_write(addr + i, b)

    # -- EEPROM (E5/E6) --

    def eeprom_read(self, addr):
        """Read one byte from EEPROM (uses E5 which returns 5 bytes, we take [4])."""
        self._send(0xE5, addr)
        return self._recv()[4]

    def eeprom_write(self, addr, val, delay=0.01):
        """Write one byte to EEPROM. WARNING: live write, no undo."""
        self._send(0xE6, addr, val)
        time.sleep(delay)

    def eeprom_read_block(self, addr, length):
        """Read a block of EEPROM bytes."""
        buf = bytearray(length)
        for i in range(length):
            buf[i] = self.eeprom_read(addr + i)
        return buf

    def eeprom_dump(self, size=2048):
        """Dump the full EEPROM."""
        return self.eeprom_read_block(0, size)

    def eeprom_write_block(self, addr, data, delay=0.01):
        """Write a block to EEPROM with per-byte delay. No safety checks;
        prefer flash_eeprom() for firmware images."""
        for i, b in enumerate(data):
            self.eeprom_write(addr + i, b, delay)

    def flash_eeprom(self, image, force=False, progress=None):
        """Write a firmware image after checking it suits the connected chip.

        Refuses (ValueError) if the image's magic is not one the chip's ROM
        loads, if its checksums are wrong, or if the chip is unknown, unless
        force=True. Verifies by reading back. Returns the list of
        mismatching addresses (empty on success).
        """
        problems = check_image(self.chip_id(), image)
        if problems and not force:
            raise ValueError("; ".join(problems))
        for i, b in enumerate(image):
            self.eeprom_write(i, b)
            if progress and i % 256 == 255:
                progress(i + 1, len(image))
        readback = self.eeprom_read_block(0, len(image))
        return [a for a in range(len(image)) if readback[a] != image[a]]

    # -- device info --

    def chip_id(self):
        """Chip/ROM ID byte at XDATA 0xF800 (see CHIP_IDS)."""
        return self.xdata_read(0xF800)

    def chip_version(self):
        """ROM version string as tools print it, e.g. 'A7.10.9A'."""
        return ".".join(f"{b:02X}" for b in self.xdata_read_block(0xF800, 3))

    def get_strings(self):
        """Read USB descriptor strings."""
        return {
            "manufacturer": self.dev.get_manufacturer_string(),
            "product": self.dev.get_product_string(),
            "serial": self.dev.get_serial_number_string(),
        }


def chip_name(chip_id):
    return CHIP_IDS.get(chip_id, (f"unknown (0x{chip_id:02X})",))[0]


def check_image(chip_id, image):
    """Return a list of reasons not to flash image onto this chip."""
    from ms_eeprom_checksum import compute_checksums, read_stored_checksums

    problems = []
    if len(image) < 0x34:
        return ["image is too short to be an EEPROM image"]
    magic = (image[0] << 8) | image[1]
    if chip_id not in CHIP_IDS:
        problems.append(f"unknown chip ID 0x{chip_id:02X} at 0xF800")
    elif chip_id == 0xD7 and magic != 0x5AA5:
        problems.append(
            "this MS2109 has the newer D7 mask ROM, which only loads "
            "0x5AA5 images; build one with ms2109/eeprom_d7 (see README)")
    elif magic not in CHIP_IDS[chip_id][1]:
        problems.append(f"image magic 0x{magic:04X} is not loaded by "
                        f"{chip_name(chip_id)}: wrong image for this chip?")
    try:
        hdr, code, end = compute_checksums(image)
        if (hdr, code) != read_stored_checksums(image, end):
            problems.append("image checksums are wrong")
    except (ValueError, IndexError) as e:
        problems.append(f"bad image: {e}")
    return problems


def main():
    ap = argparse.ArgumentParser(description="Macrosilicon HID tool")
    ap.add_argument("--vid", type=lambda x: int(x, 0))
    ap.add_argument("--pid", type=lambda x: int(x, 0))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("info", help="identify the connected device (default)")
    p = sub.add_parser("backup", help="save the EEPROM to a file")
    p.add_argument("output")
    p = sub.add_parser("flash", help="write a firmware image to the EEPROM")
    p.add_argument("image")
    p.add_argument("--backup", default=None,
                   help="where to save the current EEPROM first "
                        "(default: eeprom_backup_<time>.bin)")
    p.add_argument("--force", action="store_true",
                   help="flash even if the image does not match the chip")
    args = ap.parse_args()
    if (args.vid is None) != (args.pid is None):
        ap.error("--vid and --pid must be given together")

    try:
        with MSDevice(args.vid, args.pid) as d:
            run(d, args)
    except OSError as e:
        sys.exit(f"Device error: {e}")


def run(d, args):
    chip = d.chip_id()
    info = d.get_strings()
    print(f"Device:  {d.vid:04X}:{d.pid:04X} "
          f"{info['manufacturer']} / {info['product']}")
    print(f"Chip:    {chip_name(chip)}, ROM {d.chip_version()}")

    if args.cmd == "backup":
        with open(args.output, "wb") as f:
            f.write(d.eeprom_dump())
        print(f"Saved EEPROM to {args.output}")

    elif args.cmd == "flash":
        with open(args.image, "rb") as f:
            image = f.read()
        problems = check_image(chip, image)
        for msg in problems:
            print(f"WARNING (forced): {msg}" if args.force else
                  f"Refusing: {msg}")
        if problems and not args.force:
            sys.exit(1)
        backup = args.backup or time.strftime("eeprom_backup_%Y%m%d_%H%M%S.bin")
        with open(backup, "wb") as f:
            f.write(d.eeprom_dump())
        print(f"Saved current EEPROM to {backup}")
        print(f"Writing {len(image)} bytes...")
        bad = d.flash_eeprom(image, force=args.force, progress=lambda n, t:
                             print(f"  {n}/{t} bytes"))
        if bad:
            sys.exit(f"Verify FAILED at {len(bad)} addresses (first "
                     f"0x{bad[0]:03X}). Restore with: python ms_hid.py "
                     f"flash --force {backup}")
        print("Verified. Unplug and replug the device to load it.")

if __name__ == "__main__":
    main()
