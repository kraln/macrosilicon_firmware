# Macrosilicon Custom Firmware

Custom EEPROM firmware for Macrosilicon USB video chips, adding
host-accessible features via a mailbox protocol over the existing HID
interface.

These images could be customized, flashed to most cheap USB dongles, or used as the basis for new products.

<img width="1920" height="1080" alt="MS9123 output being received by MS2107" src="https://github.com/user-attachments/assets/bb49619c-b692-4e93-a60c-a70a99612670" />


## Supported Chips

| Chip | Function | Status | New Features |
|------|----------|--------|----------|
| **MS2107** | CVBS/S-Video to USB capture | Working | Image adjust, input select, GPIO, I2C master |
| **MS9123** | USB to CVBS/S-Video display | Working | GPIO, I2C master |
| **MS2109** | HDMI to USB capture | Working | GPIO, PID override, I2C master |
| **MS2109 "D7"** | HDMI to USB capture | Mailbox tested; flashed image untested on hardware | GPIO, VID/PID override, I2C master, EDID |

## What's "New"?

The stock firmwares handle things like signal detection and video configuration
but expose no user-accessible interface. This project adds:

- **Host command mailbox** — read signal status, video parameters, and
  hardware state over USB HID without any driver changes
- **I2C master** — turn the dongle into a USB-to-I2C bridge for
  communicating with sensors, EEPROMs, or other devices on the board
- **GPIO read/write** — direct port access for hardware debugging
- **Image adjustment** (MS2107) — brightness, contrast, saturation, hue
- **Video input switching** (MS2107) — force CVBS, S-Video, or auto-detect
- **DAC control** (MS9123) — read/write the 10-bit video DAC
- **USB PID override** (MS2109) — customize the USB product ID at build time
- **Editable EDID** (MS2109) — customize the HDMI EDID the source device sees

## How It Works

These Video ICs contain an 8051 microcontroller that loads firmware
from an external I2C EEPROM (24C16) at boot. The mask ROM provides the base
USB video functionality; the EEPROM firmware extends it with signal detection,
video configuration, and interrupt handling.

All Macrosilicon devices expose a 9-byte HID feature report interface for
reading and writing XDATA (external RAM). The firmware places a command
mailbox at a known XDATA address. The host writes a command byte, waits
briefly, then reads back the response:

```python
from ms_hid import MSDevice

with MSDevice(vid=0x534D, pid=0x2109) as d:
    # Write command 0x01 (signal status)
    d.xdata_write(0xDE00, 0x01)
    time.sleep(0.05)

    # Read status and response
    status = d.xdata_read(0xDE03)  # 0x02 = done
    signal = d.xdata_read(0xDE04)  # signal state
```

The `ms_hid.py` library in `tools/` wraps the raw HID protocol.

## Tools

| Tool | Description |
|------|-------------|
| `tools/ms_hid.py` | HID library + CLI: device detection, chip ID, EEPROM backup/flash with safety checks |
| `tools/ms2109_mailbox.py` | MS2109 mailbox client (status, GPIO, I2C, EDID); can run the mailbox from RAM on stock D7 units |
| `tools/ms_movc_dump.py` | CODE space dump via RAM-patched MOVC handler |
| `tools/ms2109_d7_dump.py` | CODE/EEPROM dump for the MS2109 "D7" variant (see below) |
| `tools/ms_xdata_dump.py` | Full XDATA dump (single-byte reads) |
| `tools/ms_eeprom_checksum.py` | EEPROM checksum calculator |
| `tools/ms_testpattern.py` | USB display test pattern generator (SMPTE, PM5544, etc.) |
| `tools/bp5_eeprom.py` | Bus Pirate 5 EEPROM recovery tool |

## MS2107 (CVBS Capture)

Mailbox at XDATA `0xD210-0xD219`. Written in C with SDCC.

| Command | Feature | Description |
|---------|---------|-------------|
| 0x01 | Signal status | NTSC/PAL detection, lock state, line count |
| 0x02 | Image read | Brightness, contrast, saturation, hue values |
| 0x03 | Image write | Set brightness/contrast/saturation/hue |
| 0x04 | Input select | Force CVBS, S-Video, G1IN, or auto-detect |
| 0x05 | GPIO read | P0, P2, P3 port states |
| 0x06 | GPIO write | Set P0, P2, or P3 port values |
| 0x10 | I2C write | Send byte to I2C device |
| 0x11 | I2C read | Read byte from I2C device register |
| 0x12 | I2C scan | Scan 6 addresses, return bitmap |
| 0xFE | Identify | Returns "@kraln" |

The I2C master turns the capture dongle into a USB-to-I2C bridge — useful for
communicating with sensors, EEPROMs, or other I2C devices on the same board.

### Building and Flashing (MS2107)

```bash
cd ms2107/eeprom
make

# Custom USB strings
make CUSTOM_STRINGS='-DCUSTOM_USB_SERIAL="\"MySerial\""'
```

Flash via `ms_hid.py` or any I2C programmer:

```bash
python ../../tools/ms_hid.py info                     # check what's connected
python ../../tools/ms_hid.py flash eeprom_image.bin   # then unplug/replug
```

`flash` auto-detects the device (or takes `--vid`/`--pid`), saves a backup
of the current EEPROM, and refuses to write an image whose magic the
connected chip's ROM doesn't load (e.g. an A7 MS2109 image on a D7 unit),
an image with bad checksums, or any image onto an unknown chip. It verifies
by reading back. From Python, use `MSDevice().flash_eeprom(image)`.

## MS9123 (USB Display Adapter)

Mailbox at XDATA `0xDDF0-0xDDF9`. Written in C with SDCC.

| Command | Feature | Description |
|---------|---------|-------------|
| 0x01 | Display status | Mode, output params, host connection state |
| 0x03 | DAC read | 10-bit DAC samples and config registers |
| 0x04 | DAC write | Set DAC configuration (F880/F005/F020) |
| 0x05 | GPIO read | P0, P2, P3, P3ALT, DAC readback |
| 0x06 | GPIO write | Set P0, P2, or P3 port values |
| 0x10 | I2C write | Send byte to I2C device (7-bit addr) |
| 0x11 | I2C read | Read byte from I2C device register |
| 0x12 | I2C scan | Scan 8 addresses, return bitmap |
| 0xFE | Identify | Returns "@kraln" |

PAL/NTSC mode switching is host-side via the 0xA6 protocol (mode
0x0200=NTSC 720x480, 0x1100=PAL 720x576). The `ms_testpattern.py`
tool handles this automatically.

### Building and Flashing (MS9123)

```bash
cd ms9123/eeprom
make
```

Flash the same way as MS2107.

## MS2109 (HDMI Capture)

Mailbox at XDATA `0xDE00-0xDE07`. Written in pure 8051 assembly.

| Command | Feature | Description |
|---------|---------|-------------|
| 0x01 | Signal status | HDMI signal state, chip ID |
| 0x05 | GPIO read | P0, P2, P3 port states |
| 0x11 | I2C read | Read byte from I2C device register |
| 0x12 | I2C scan | Scan 0x50-0x57, return bitmap |
| 0xFE | Identify | Returns "@kraln" |

USB PID is configurable at build time (edit `_set_usb_pid` in crt0).
EDID, video timing table, and register configuration are editable in
the assembly source.

### Building and Flashing (MS2109)

```bash
cd ms2109/eeprom
make
python ../../tools/ms_hid.py flash eeprom_image.bin
```

`ms_hid.py` finds MS2109 units under either VID (`0x534D` or `0x345F`) and
will refuse to flash this image onto a D7 unit.

The MS2109 firmware is pure assembly (sdas8051 + sdld). No C compiler needed
beyond what SDCC provides.

### MS2109 "D7" Variant

Some MS2109 dongles (commonly VID `0x345F`, chip marked MS2109"S") report
`D7.10.9A` at XDATA `0xF800` instead of `A7.10.9A`. They run a different
mask ROM, which only loads `0x5AA5` images, so the A7 firmware above does
not work on them (`ms_hid.py` refuses to flash it). See
`ms2109/rom/d7_rom_notes.md`.

`ms2109/eeprom_d7` is a complete EEPROM firmware for D7 units (pure
assembly), with the same mailbox at `0xDE00`:

| Command | Feature | Description |
|---------|---------|-------------|
| 0x01 | Signal status | Valid-signal flag, chip ID, input resolution, refresh rate, pixel clock |
| 0x05 | GPIO read | P0, P2, P3 port states |
| 0x11 | I2C read | Read byte from I2C device register; DE05 = 1 if ACKed |
| 0x12 | I2C scan | Scan 0x50-0x57, return bitmap |
| 0xFE | Identify | Returns "@kraln" |

It also reimplements the video mode-setup hooks of the stock D7 EEPROM code
(scaler class selection and register adjustments), so capture behaves as
on a stock unit. Optionally it serves a custom EDID (the ROM asks the
firmware for it instead of using its built-in one) and sets a VID/PID
override (applied by the D7 ROM from the EEPROM header).

D7 units already report 48 kHz stereo audio, so the A7 "stereo patch"
(96 kHz mono descriptor fix) is not needed on them.

### Building and Flashing (MS2109 D7)

```bash
cd ms2109/eeprom_d7
make                                   # or: make EDID=my_edid.bin VID=0x534D PID=0x2109
python ../../tools/ms_hid.py flash eeprom_image_d7.bin
```

`flash` saves the current EEPROM first (`eeprom_backup_<time>.bin`); keep
that file, it is how you go back to stock. After replugging:

```bash
python ../../tools/ms2109_mailbox.py identify   # also: status, gpio, i2c-scan, i2c-read, edid
```

Status: the mailbox has been tested on a real D7 unit (running from RAM,
see below). The mode-setup hooks were checked against the stock D7 code in
a simulator (20,000 randomised and boundary cases, identical results), and
the boot path against the ROM, but the flashed image has not yet been run
on hardware. If the dongle no longer enumerates after flashing, see EEPROM
Recovery below.

To try the mailbox on a stock D7 unit without flashing anything, load it
into RAM; unplugging undoes it:

```bash
python ../../tools/ms2109_mailbox.py load
python ../../tools/ms2109_mailbox.py status
```

### EEPROM Recovery (MS2109)

If a bad firmware prevents USB enumeration, the EEPROM can be rewritten
in-circuit using an I2C programmer (Bus Pirate, CH341, etc.):

- Supply voltage: **2.5V** (at 3.3V the MS2109 back-powers through board
  traces and interferes with I2C writes)
- USB cable **disconnected** from the target device
- EEPROM WP pin grounded
- EEPROM type: 24C16 (2KB, 8 block addresses 0x50-0x57)

A known-good minimal firmware (`eeprom_minimal_test.bin`, 33 bytes) is
included for recovery testing.

## Architecture Notes

### EEPROM Format

All Macrosilicon chips use a 2KB I2C EEPROM with this layout:

```
[0x00-0x01]  Magic (0x0816 for MS2107, 0xA55A for MS2109/MS9123,
             0x5AA5 for MS2109 D7)
[0x02-0x03]  Code length (big-endian, determines checksum position)
[0x04-0x07]  VID/PID (MS2107) or hook config (MS2109/MS9123)
[0x08-0x0F]  Hook flags + config bytes
[0x10-0x2F]  USB string descriptors (MS2107 only)
[0x30+]      Firmware code (loaded to XDATA at boot)
[0x30+len]   Checksums (uint16 BE: header + code)
```

Power cycling causes the mask ROM to reload the EEPROM. The original
firmware can always be restored by reflashing a backup.

### Calling Conventions

The mask ROM appears to use Keil-style calling conventions. The MS2107
and MS9123 firmware is compiled with SDCC, which uses different conventions:

| | ROM (Keil-style) | SDCC (firmware) |
|--|----------------|-----------------|
| First uint8_t arg | R7 | DPL |
| Return value | R7 / carry | DPL |

The custom crt0 assembly handles bridging between conventions. The MS2109
firmware is pure assembly and calls ROM functions directly without bridging.

IRQ handlers are written in assembly on all three chips. The mask ROM's
USB stack seems to be sensitive to exact instruction sequences in interrupt context.

## License

The firmware source code in this repository is original work. All copyrights remain with the original author(s). 

## Acknowledgments

- [BertoldVdb/ms-tools](https://github.com/BertoldVdb/ms-tools) — the
  essential tool for communicating with these chips
- [amnemonic/MacroSilicon](https://github.com/amnemonic/MacroSilicon) — HID
  protocol documentation and reference dumps
- [alexbsys/macrosilicon_ms2107_research](https://github.com/alexbsys/macrosilicon_ms2107_research) —
  prior EEPROM structure research
