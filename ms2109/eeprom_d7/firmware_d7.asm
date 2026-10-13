	.module firmware_d7

	.area HOME    (CODE)

	; ================================================================
	; MS2109 "D7" (ROM D7.10.9A) EEPROM firmware, linked at 0xCC00
	;
	; Replaces the whole EEPROM code. The D7 ROM calls CC00 with
	; R7 = command (header[4] bit 0); we handle:
	;
	;   0x00  boot (0x3E51)            mode-switch threshold
	;   0x02  every main-loop pass     host mailbox (mailbox_d7.inc)
	;   0x08  EDID load (header[5].4)  our EDID instead of the ROM's
	;   0x17  mode setup (header[5].6) pick the scaler class
	;   0x22  mode setup, after 0x17   re-check the class by frame rate
	;   0x19  mode setup, later        scaler register adjustments
	;
	; 0x00/0x17/0x19/0x22 reproduce what the stock D7 EEPROM code
	; does, so the video path behaves as on a stock unit; see
	; ms2109/rom/d7_rom_notes.md. Everything else returns at once.
	; ================================================================

EDID_LOAD	= 0x73F7	; ROM: R3:R2:R1 = generic ptr to 256 EDID bytes

	; ROM mode-setup state (16-bit values big-endian)
OUT_W		= 0xC703	; selected USB format: width
OUT_H		= 0xC705	;   height
OUT_FPS		= 0xC709	;   frame rate * 100
CLASS		= 0xC713	; scaler class (0, 1, 2) for the ROM
SCL_W		= 0xC714	; scaled image width
SCL_H		= 0xC716	;   height
IN_FLAGS	= 0xC71A	; input mode; bit 0 skips the adjustments
IN_W		= 0xC71F	; measured input: active width
IN_H		= 0xC721	;   active height
IN_PCLK		= 0xC723	;   pixel clock, 10 kHz units
IN_FPS		= 0xC725	;   frame rate * 100
FAST_FLAG	= 0xC6FD	; 0x40 with class 2
THRESHOLD	= 0xC73A	; set at boot

	; +0x00: CC00 hook entry
	ljmp	hook_entry

	; +0x03: signature + version, checked by the host tools
	.ascii	"kD7fw"
	.db	0x01

	; +0x09: XDATA address of our EDID (big-endian), patched by
	; build_d7_image.py; only used if header[5] bit 4 is set
edid_ptr:
	.db	0x00, 0x00

hook_entry:
	cjne	r7, #0x02, 1$
	ljmp	mbox_poll
1$:	cjne	r7, #0x17, 2$
	ljmp	pick_class
2$:	cjne	r7, #0x22, 3$
	ljmp	recheck_class
3$:	cjne	r7, #0x19, 4$
	ljmp	scaler_adjust
4$:	cjne	r7, #0x08, 5$
	ljmp	edid_hook
5$:	cjne	r7, #0x00, 6$
	mov	dptr, #THRESHOLD	; 19000
	mov	a, #0x4A
	movx	@dptr, a
	inc	dptr
	mov	a, #0x38
	movx	@dptr, a
6$:	ret

edid_hook:
	mov	dptr, #edid_ptr		; our code is in XDATA
	movx	a, @dptr
	mov	r2, a
	inc	dptr
	movx	a, @dptr
	mov	r1, a
	mov	r3, #0x01		; generic pointer, XDATA
	ljmp	EDID_LOAD		; tail call, returns to the ROM

	; ================================================================
	; 16-bit helpers. Values are big-endian: R6:R7 = hi:lo.
	; ================================================================

	; R6:R7 = word at DPTR
rd16:
	movx	a, @dptr
	mov	r6, a
	inc	dptr
	movx	a, @dptr
	mov	r7, a
	ret

	; C = (R6:R7 > R4:R5)
gt16:
	clr	c
	mov	a, r5
	subb	a, r7
	mov	a, r4
	subb	a, r6
	ret

	; C = (R6:R7 < R4:R5)
lt16:
	clr	c
	mov	a, r7
	subb	a, r5
	mov	a, r6
	subb	a, r4
	ret

	; R4:R5 = R6:R7
mv16:
	mov	a, r6
	mov	r4, a
	mov	a, r7
	mov	r5, a
	ret

	; R4:R5 = input frame rate + 0.90 Hz (16-bit, wraps)
in_fps_margin:
	mov	dptr, #IN_FPS
	lcall	rd16
	mov	a, r7
	add	a, #90
	mov	r5, a
	clr	a
	addc	a, r6
	mov	r4, a
	ret

	; C = 1 if the input is R2:R3 x R4:R5 pixels
in_size_is:
	mov	dptr, #IN_W		; IN_W, IN_H are adjacent
	movx	a, @dptr
	xrl	a, r2
	jnz	1$
	inc	dptr
	movx	a, @dptr
	xrl	a, r3
	jnz	1$
	inc	dptr
	movx	a, @dptr
	xrl	a, r4
	jnz	1$
	inc	dptr
	movx	a, @dptr
	xrl	a, r5
	jnz	1$
	setb	c
	ret
1$:	clr	c
	ret

	; ================================================================
	; R7 = 0x17: choose the scaler class and store it in CLASS.
	; Returns R7 = class.
	; ================================================================
pick_class:
	lcall	class_select
	mov	dptr, #CLASS
	mov	a, r7
	movx	@dptr, a
	ret

class_select:
	mov	dptr, #SIG_VALID
	movx	a, @dptr
	jnz	1$
	mov	r7, #0			; no input: class 0
	ret
1$:	clr	a
	mov	dptr, #FAST_FLAG
	movx	@dptr, a
	lcall	in_fps_margin
	mov	dptr, #OUT_FPS
	lcall	rd16
	lcall	gt16
	jc	out_faster
	ljmp	out_not_faster

	; Output frame rate above the input's (+0.9 Hz).
out_faster:
	; Scaled image of at most 0x100000 pixels: class 0
	mov	dptr, #SCL_W
	lcall	rd16
	mov	a, r6
	mov	r2, a
	mov	a, r7
	mov	r3, a
	mov	dptr, #SCL_H
	lcall	rd16
	lcall	mul16			; R5:R4:R1:R0 = SCL_W * SCL_H
	clr	c
	clr	a
	subb	a, r0
	clr	a
	subb	a, r1
	mov	a, #0x10
	subb	a, r4
	clr	a
	subb	a, r5
	jc	1$
	mov	r7, #0
	ret
1$:
	; Input at least as big as the output and pixel clock at most
	; 160 MHz: class 2
	mov	dptr, #OUT_W
	lcall	rd16
	lcall	mv16
	mov	dptr, #IN_W
	lcall	rd16
	lcall	lt16
	jc	2$
	mov	dptr, #OUT_H
	lcall	rd16
	lcall	mv16
	mov	dptr, #IN_H
	lcall	rd16
	lcall	lt16
	jc	2$
	mov	dptr, #IN_PCLK
	lcall	rd16
	mov	r4, #0x3E		; 16000
	mov	r5, #0x80
	lcall	gt16
	jc	2$
	mov	dptr, #FAST_FLAG
	mov	a, #0x40
	movx	@dptr, a
	mov	r7, #2
	ret
2$:
	; 2560x1440 input: class 1
	mov	r2, #0x0A		; 2560
	mov	r3, #0x00
	mov	r4, #0x05		; 1440
	mov	r5, #0xA0
	lcall	in_size_is
	jc	class_1_near
	ljmp	class_0_clamp
class_1_near:
	mov	r7, #1
	ret

	; Output frame rate not above the input's.
out_not_faster:
	mov	dptr, #IN_PCLK
	lcall	rd16
	mov	r4, #0x46		; 18000
	mov	r5, #0x50
	lcall	gt16
	jnc	class_1_near
	; Input frame rate below 64 Hz, pixel clock at most 300 MHz and
	; none of the large VESA modes: class 1
	mov	dptr, #IN_FPS
	movx	a, @dptr
	clr	c
	subb	a, #0x19		; >= 6400 (low byte is 0x00)
	jnc	3$
	mov	r4, #0x75		; 30000
	mov	r5, #0x30
	lcall	gt16			; R6:R7 still IN_PCLK
	jc	3$
	mov	r2, #0x07		; 1920x1440
	mov	r3, #0x80
	mov	r4, #0x05
	mov	r5, #0xA0
	lcall	in_size_is
	jc	3$
	mov	r2, #0x07		; 1856x1392
	mov	r3, #0x40
	mov	r4, #0x05
	mov	r5, #0x70
	lcall	in_size_is
	jc	3$
	mov	r2, #0x0A		; 2560x1600
	mov	r3, #0x00
	mov	r4, #0x06
	mov	r5, #0x40
	lcall	in_size_is
	jc	3$
	mov	r2, #0x08		; 2048x1536
	mov	r3, #0x00
	mov	r4, #0x06
	mov	r5, #0x00
	lcall	in_size_is
	jc	3$
	sjmp	class_1
3$:
	; Output below 1080 lines: class 1
	mov	dptr, #OUT_H
	lcall	rd16
	mov	r4, #0x04		; 1080
	mov	r5, #0x38
	lcall	lt16
	jc	class_1
	; Mean of input and output frame rate (16-bit sum) below 56 Hz:
	; class 1
	mov	dptr, #IN_FPS
	lcall	rd16
	lcall	mv16
	mov	dptr, #OUT_FPS
	lcall	rd16
	mov	a, r7
	add	a, r5
	mov	r7, a
	mov	a, r6
	addc	a, r4
	clr	c
	rrc	a
	mov	r6, a
	mov	a, r7
	rrc	a
	mov	r7, a
	mov	r4, #0x15		; 5600
	mov	r5, #0xE0
	lcall	lt16
	jc	class_1
class_0_clamp:
	; Class 0: limit the scaled image to 1280x720
	mov	dptr, #IN_W
	lcall	rd16
	mov	r4, #0x05		; 1280
	mov	r5, #0x00
	lcall	gt16
	jnc	4$
	mov	dptr, #SCL_W
	mov	a, #0x05
	movx	@dptr, a
	inc	dptr
	clr	a
	movx	@dptr, a
4$:	mov	dptr, #IN_H
	lcall	rd16
	mov	r4, #0x02		; 720
	mov	r5, #0xD0
	lcall	gt16
	jnc	5$
	mov	dptr, #SCL_H
	mov	a, #0x02
	movx	@dptr, a
	inc	dptr
	mov	a, #0xD0
	movx	@dptr, a
5$:	mov	r7, #0
	ret
class_1:
	mov	r7, #1
	ret

	; R5:R4:R1:R0 = R2:R3 * R6:R7 (unsigned). Uses A, B.
mul16:
	mov	a, r3
	mov	b, r7
	mul	ab
	mov	r0, a
	mov	r1, b
	mov	r4, #0
	mov	r5, #0
	mov	a, r3
	mov	b, r6
	lcall	mul16_add
	mov	a, r2
	mov	b, r7
	lcall	mul16_add
	mov	a, r2
	mov	b, r6
	mul	ab
	add	a, r4
	mov	r4, a
	mov	a, b
	addc	a, r5
	mov	r5, a
	ret
	; R5:R4:R1 += A * B
mul16_add:
	mul	ab
	add	a, r1
	mov	r1, a
	mov	a, b
	addc	a, r4
	mov	r4, a
	clr	a
	addc	a, r5
	mov	r5, a
	ret

	; ================================================================
	; R7 = 0x22: with a valid input (and IN_FLAGS bit 0 clear), class
	; 1 if the output frame rate is at most the input's + 0.9 Hz,
	; else class 0.
	; ================================================================
recheck_class:
	mov	dptr, #SIG_VALID
	movx	a, @dptr
	jz	1$
	mov	dptr, #IN_FLAGS
	movx	a, @dptr
	jb	acc.0, 1$
	lcall	in_fps_margin
	mov	dptr, #OUT_FPS
	lcall	rd16
	lcall	gt16
	clr	a
	jc	2$
	inc	a
2$:	mov	dptr, #CLASS
	movx	@dptr, a
1$:	ret

	; ================================================================
	; R7 = 0x19: scaler register adjustments, in the stock order.
	; ================================================================
scaler_adjust:
	; Pixel clock above 160 MHz
	mov	dptr, #IN_PCLK
	lcall	rd16
	mov	r4, #0x3E		; 16000
	mov	r5, #0x80
	lcall	gt16
	jnc	1$
	mov	dptr, #0xF923
	mov	a, #0x10
	movx	@dptr, a
	mov	dptr, #0xF92C
	mov	a, #0x16
	movx	@dptr, a
	mov	dptr, #0xF8E9
	movx	a, @dptr
	orl	a, #0x01
	movx	@dptr, a
	mov	dptr, #0xF8E1
	movx	a, @dptr
	orl	a, #0x04
	movx	@dptr, a
	mov	dptr, #0xF8E0
	movx	a, @dptr
	orl	a, #0x01
	movx	@dptr, a
1$:
	mov	dptr, #CLASS
	movx	a, @dptr
	mov	r7, a
	cjne	a, #1, 2$
	mov	dptr, #0xF95C
	movx	a, @dptr
	orl	a, #0x15
	movx	@dptr, a
2$:	cjne	r7, #2, 3$
	mov	dptr, #0xF88B
	mov	a, #0x84
	movx	@dptr, a
3$:	mov	dptr, #IN_FLAGS
	movx	a, @dptr
	jnb	acc.0, 4$
	ret
4$:
	; 1920-wide input to a 1080-line output
	mov	dptr, #IN_W
	lcall	rd16
	cjne	r6, #0x07, 6$
	cjne	r7, #0x80, 6$
	mov	dptr, #OUT_H
	lcall	rd16
	cjne	r6, #0x04, 6$
	cjne	r7, #0x38, 6$
	mov	dptr, #0xF8E6
	mov	a, #0x80
	movx	@dptr, a
	inc	dptr
	mov	a, #0x07
	movx	@dptr, a
	mov	dptr, #0xF924
	mov	a, #0xFF
	movx	@dptr, a
	inc	dptr
	mov	a, #0xE8
	movx	@dptr, a
	inc	dptr
	mov	a, #0x07
	movx	@dptr, a
	mov	dptr, #CLASS
	movx	a, @dptr
	jnz	5$
	mov	dptr, #0xF934
	movx	@dptr, a
	inc	dptr
	mov	a, #0xE9
	movx	@dptr, a
	inc	dptr
	mov	a, #0x07
	movx	@dptr, a
	mov	dptr, #0xF927
	mov	a, #0xFF
	movx	@dptr, a
	inc	dptr
	mov	a, #0xD1
	movx	@dptr, a
	inc	dptr
	mov	a, #0x0F
	movx	@dptr, a
5$:	mov	dptr, #0xF937
	mov	a, #0xC0
	movx	@dptr, a
	inc	dptr
	mov	a, #0x03
	movx	@dptr, a
	inc	dptr
	mov	a, #0xC0
	movx	@dptr, a
	inc	dptr
	mov	a, #0x03
	movx	@dptr, a
	mov	dptr, #0xFD3B
	clr	a
	movx	@dptr, a
	inc	dptr
	mov	a, #0x10
	movx	@dptr, a
	mov	dptr, #0xFD40
	mov	a, #0x80
	movx	@dptr, a
	inc	dptr
	mov	a, #0x07
	movx	@dptr, a
6$:	mov	dptr, #0xF944
	movx	a, @dptr
	anl	a, #0xFB
	movx	@dptr, a
	ret

	.include "mailbox_d7.inc"
