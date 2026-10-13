# MS2109 "D7" ROM Notes

Notes on the D7.10.9A mask ROM, from a CODE dump made with
`tools/ms2109_d7_dump.py` (thanks to creedof69, issue #1). The dump itself
is not in the repo.

## Identification

- `XDATA[0xF800..0xF802]` = `D7 10 9A` (A7 units: `A7 10 9A`)
- Usually VID `0x345F` (descriptor in ROM at `0x1AF1`, copied to RAM
  `0xC8A9`), chip marking MS2109"S"
- Code occupies `0x0000-0x80B6`, data up to `0x8800`; reset vector
  `LJMP 0x5192`, main loop at `0x3E32`

## EEPROM Header

```
Offset  Stock   Meaning
0x00-01 5A A5   Magic. The loader (0x7226) accepts 5AA5, or 9669 when bit
                0x22.6 is set. A55A (A7 images) is NOT accepted.
0x02-03         Code length (big-endian), loaded to XDATA 0xCC00
0x04    01      Hook slots: bit n enables a call to CC00 + 0x10*n
0x05    40      Extra hook commands (see below)
0x06-07 FF FF   VID override, big-endian (FFFF = keep), ROM 0x2F5C
0x08-09 FF FF   PID override, big-endian (FFFF = keep)
0x0C-0F         Config bytes (stock 18 09 06 11)
0x10-2F         Length-prefixed product strings (FF = ROM default)
```

Checksums are the same as A7: `sum(hdr[0x02:0x30])` and `sum(code)`, both
16-bit big-endian, right after the code.

## Hooks

`header[4]` bit n makes the ROM call `CC00 + 0x10*n`:

| Bit | Slot | Called from |
|-----|------|-------------|
| 0 | CC00 | 0x7F91 (via `LJMP CC00` at 0x80AC), R7 = command |
| 1 | CC10 | 0x5176 |
| 2 | CC20 | 0x4011 |
| 3 | CC30 | 0x60BB |
| 4 | CC40 | 0x67D3 |
| 5 | CC50 | 0x6B74 |
| 6 | CC60 | 0x5361 |
| 7 | CC70 | 0x43F3 |

Stock images only use CC00, which is `LJMP 0xCD6E` into the stock code.
Commands passed in R7 (all via 0x7F91):

| R7 | When |
|----|------|
| 0x02 | Every main-loop iteration (0x3EFE) |
| 0x08 | Instead of loading the ROM EDID, if `header[5]` bit 4 (0x72D4) |
| 0x04-0x07 | `header[5]` bits 0-3 (0x5E58-0x5E8B) |
| 0x09 | `header[5]` bit 5 (0x681A) |
| 0x17 | `header[5]` bit 6 (0x1ED1), set in stock images |
| 0x24 | `header[5]` bit 7 (0x45E4) |
| 0x23 | 0x5E9E |

Also seen: 0x01, 0x0A-0x0C, 0x0F, 0x10, 0x13, 0x16, 0x19, 0x22, 0x25-0x29.

The gated commands (0x04-0x09, 0x17, 0x24) replace a built-in ROM routine
when their `header[5]` bit is set, e.g. 0x17 replaces `0x3472`/`0x32C9`.
The others are always called.

## Mode Setup

When the input or the USB format changes, the ROM runs hook 0x17 (or its
own class selection), then 0x22, reads the class back from `C713`, and
later runs 0x19. Stock EEPROM code uses 0x00, 0x17, 0x19 and 0x22;
`ms2109/eeprom_d7/firmware_d7.asm` does the same things (checked against
the stock code in a simulator). The state involved, all 16-bit big-endian:

| Addr | Meaning |
|------|---------|
| C6FE.. | Selected USB format: C703 width, C705 height, C707 pixel clock, C709 frame rate * 100 |
| C713 | Scaler class (0, 1, 2) |
| C714, C716 | Scaled image width, height |
| C71A.. | Measured input: C71A flags, C71F width, C721 height, C723 pixel clock (10 kHz), C725 frame rate * 100 |
| C6FD | 0x40 with class 2 |
| C73A | Set to 19000 by hook 0x00 |

Hook 0x19 also writes scaler registers in `F88B-F944` and `FD3B-FD41`
for inputs above 160 MHz and for a 1920-wide input to a 1080-line format.

## Boot Sequence

From the reset path (`0x3E32` onwards):

1. `0x71B4` loads the EEPROM: `0x44DB` reads `length + 0x34` bytes from
   EEPROM offset 0 to XDATA `0xCBD0` (so the code lands at `0xCC00`), then
   `0x3997` checks the code length (at most `0x13FC`) and both checksums.
2. Hook with R7 = 0x00.
3. `0x67FB` -> `0x72A3`: EDID load. Reads `header[5]` from the loaded image
   at `0xCBD5`; bit 4 set -> hook R7 = 0x08, else ROM EDID from `0x847C`.
4. `0x2F59`: VID/PID override from the header.
5. Hook with R7 = 0x01, then the main loop (hook R7 = 0x02 every pass).

So the image, including anything past offset 0x400, is in RAM before the
EDID and VID/PID steps use it.

## ROM Routines

Matched against the A7 ROM by instruction sequence.

| D7 | A7 | Function |
|----|----|----------|
| 0x7B1A | 0x6A8C | I2C start |
| 0x7B5F | 0x6ABA | I2C stop |
| 0x5728 | 0x4648 | I2C write byte: R7 = byte, **returns R7 = 1 on ACK** (A7: carry) |
| 0x5BEC | 0x4CF3 | I2C read byte: **R7 = 0 ACK / else NAK** (A7: bit 0x08), returns R7 |
| 0x7A89 | 0x69FB | Delay, R7 = count |
| 0x790D | 0x684C | Short delay loop |
| 0x73F7 | 0x6345 | Load 256-byte EDID from generic pointer R3:R2:R1 into EDID RAM at XDATA 0xFB00 |
| 0x634B | | memcpy generic -> XDATA (dst R6:R7, len R4:R5, src R3:R2:R1) |
| 0x71B4 | 0x5F19 | EEPROM reload |
| 0x64EC | 0x52DD | Arithmetic helper |

I2C pins are the same as A7 (SCL P2.2/P3.2, SDA P2.3/P3.3). The I2C
routines use IRAM 0x26-0x28, bit 0x08 and XDATA 0xC817-0xC819; the bit
delay comes from XDATA 0xC90C (A7: 0xC786).

The ROM EDID is at CODE 0x847C (256 bytes, "HD TO USB").

## Input Signal

`0x61F9` (main loop, via `0x80A9` -> `0x5E58`) measures the HDMI input with
`0x2988` and stores the result at XDATA `0xC8BB`. All fields big-endian;
checked against 480p60 and 1080p60 sources:

| Addr | Field |
|------|-------|
| C8BB | 1 = valid timing measured, 0 = no signal |
| C8BC | ? (1 for 480p, 7 for 1080p) |
| C8BD | Horizontal total |
| C8BF | Vertical total |
| C8C1 | Horizontal active |
| C8C3 | Vertical active |
| C8C5 | Pixel clock, 10 kHz units |
| C8C7 | Vertical refresh * 100 |
| C8C9 | Horizontal start (sync + back porch) |
| C8CB | Vertical start |
| C8CD | Horizontal sync width |
| C8CF | Vertical sync width |

`0x2988` writes up to `C8D0`. Next to it, `C8D1` holds a pixel clock value
(10 kHz, rounded differently) kept by `0x41A9`, and `C8D3` is state of
`0x7512`. `0x41A9` and `0x72A3` clear `C8BB`.

## Audio

The UAC format descriptor in RAM (`0xC4B7`, from ROM `0x8261`) already
says 2 channels, 16 bit, 48 kHz on D7. The A7 ROM's 1 channel / 96 kHz
descriptor is still in the D7 ROM at `0x83C4`, but no D7 dump uses it.

## RAM

- The ROM never references XDATA 0xD000-0xDFFF; the stock EEPROM code uses
  0xDCxx. RAM there is random at power-up.
- RAM layout is shifted from A7 by varying amounts, e.g. USB descriptor
  0xC688 -> 0xC8A9, I2C delay 0xC786 -> 0xC90C, I2C scratch 0xC61A -> 0xC817.
