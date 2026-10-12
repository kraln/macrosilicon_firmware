"""
EEPROM checksum calculator for Macrosilicon devices.

Supports two checksum variants:
- MS2107 (magic 0x0816): header sum skips bytes 0x0B-0x0F
- MS9123/MS2109 (magic 0xA55A): header sum includes ALL bytes 0x02-0x2F

Both use: code_sum = sum(data[0x30:end]), header_sum = sum(header_bytes)
Stored as two uint16 big-endian values at data[end:end+4] where end = 0x30 + code_length.

Usage:
    python ms_eeprom_checksum.py <eeprom.bin>              # verify
    python ms_eeprom_checksum.py <eeprom.bin> --fix        # fix in place
    python ms_eeprom_checksum.py <eeprom.bin> --fix -o out.bin  # fix to new file
"""

import sys
import struct


def detect_chip(data):
    magic = (data[0] << 8) | data[1]
    if magic == 0x0816 or magic == 0x3264:
        return "MS2107"
    elif magic == 0xA55A or magic == 0x9669:
        return "MS2109/MS9123"
    elif magic == 0x5AA5:
        return "MS2109 D7"
    else:
        return None


def compute_checksums(data, chip=None):
    """Compute header and code checksums. Returns (header_sum, code_sum, end_offset)."""
    if chip is None:
        chip = detect_chip(data)

    code_length = (data[2] << 8) | data[3]
    end = 0x30 + code_length

    if end + 4 > len(data):
        raise ValueError(f"Code length 0x{code_length:04X} extends beyond EEPROM "
                         f"(end=0x{end:04X}, eeprom size={len(data)})")

    code_sum = sum(data[0x30:end]) & 0xFFFF

    if chip == "MS2107":
        # MS2107: skip bytes 0x0B-0x0F in header sum
        header_sum = (sum(data[0x02:0x0B]) + sum(data[0x10:0x30])) & 0xFFFF
    else:
        # MS2109/MS9123: no skip
        header_sum = sum(data[0x02:0x30]) & 0xFFFF

    return header_sum, code_sum, end


def read_stored_checksums(data, end):
    """Read the stored checksum values from the EEPROM."""
    stored_hdr = (data[end] << 8) | data[end + 1]
    stored_code = (data[end + 2] << 8) | data[end + 3]
    return stored_hdr, stored_code


def verify(data, chip=None):
    """Verify EEPROM checksums. Returns (ok, details_dict)."""
    if chip is None:
        chip = detect_chip(data)

    header_sum, code_sum, end = compute_checksums(data, chip)
    stored_hdr, stored_code = read_stored_checksums(data, end)

    code_length = (data[2] << 8) | data[3]

    details = {
        "chip": chip,
        "code_length": code_length,
        "end": end,
        "header_computed": header_sum,
        "header_stored": stored_hdr,
        "header_ok": header_sum == stored_hdr,
        "code_computed": code_sum,
        "code_stored": stored_code,
        "code_ok": code_sum == stored_code,
    }
    details["ok"] = details["header_ok"] and details["code_ok"]
    return details["ok"], details


def fix_checksums(data, chip=None):
    """Fix checksums in-place. Returns the modified data."""
    data = bytearray(data)
    header_sum, code_sum, end = compute_checksums(data, chip)
    data[end] = (header_sum >> 8) & 0xFF
    data[end + 1] = header_sum & 0xFF
    data[end + 2] = (code_sum >> 8) & 0xFF
    data[end + 3] = code_sum & 0xFF
    return data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Macrosilicon EEPROM checksum tool")
    parser.add_argument("file", help="EEPROM binary file")
    parser.add_argument("--fix", action="store_true", help="Fix checksums")
    parser.add_argument("-o", "--output", help="Output file (default: overwrite input)")
    parser.add_argument("--chip", choices=["MS2107", "MS2109/MS9123"],
                        help="Force chip type (default: auto-detect from magic)")
    args = parser.parse_args()

    with open(args.file, "rb") as f:
        data = bytearray(f.read())

    chip = args.chip or detect_chip(data)
    if chip is None:
        print(f"Unknown magic: 0x{data[0]:02X}{data[1]:02X}")
        sys.exit(1)

    ok, details = verify(data, chip)

    print(f"Chip:        {details['chip']}")
    print(f"Code length: 0x{details['code_length']:04X} ({details['code_length']} bytes)")
    print(f"Checksum at: 0x{details['end']:04X}")
    print(f"Header:      computed=0x{details['header_computed']:04X}  "
          f"stored=0x{details['header_stored']:04X}  "
          f"{'OK' if details['header_ok'] else 'MISMATCH'}")
    print(f"Code:        computed=0x{details['code_computed']:04X}  "
          f"stored=0x{details['code_stored']:04X}  "
          f"{'OK' if details['code_ok'] else 'MISMATCH'}")

    if args.fix:
        fixed = fix_checksums(data, chip)
        out_path = args.output or args.file
        with open(out_path, "wb") as f:
            f.write(fixed)
        print(f"\nChecksums fixed, written to {out_path}")
    elif not ok:
        print("\nUse --fix to correct checksums")
        sys.exit(1)
