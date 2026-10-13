	; ================================================================
	; MS2109 "D7" host mailbox, shared by firmware_d7.asm (EEPROM
	; image) and mailbox_d7.asm (RAM add-on for `ms2109_mailbox.py
	; load`). Included into both; call mbox_poll from the R7 = 0x02
	; hook, which the ROM runs every main-loop iteration (0x3EFE).
	; ================================================================

	; D7 ROM routines (Keil calling convention)
I2C_START	= 0x7B1A	; SDA/SCL from P2.3/P2.2
I2C_STOP	= 0x7B5F
I2C_WRITE	= 0x5728	; R7 = byte; returns R7 = 1 on ACK, 0 on NAK
I2C_READ	= 0x5BEC	; R7 = 0 to ACK, else NAK; returns R7 = byte

	; Mailbox (same layout and address as the A7 firmware)
MBOX_CMD	= 0xDE00	; host writes command last; we clear it
MBOX_P0		= 0xDE01
MBOX_P1		= 0xDE02
MBOX_STATUS	= 0xDE03	; 0x02 = done, 0xFF = unknown command
MBOX_RESP	= 0xDE04	; response bytes DE04..
MBOX_MAGIC	= 0xDE0E	; "KD" once we have cleared the mailbox

	; Input timing measured by the ROM (0x2988, called from the main
	; loop via 0x61F9). Fields are big-endian.
SIG_VALID	= 0xC8BB	; 1 = valid input timing measured
SIG_HACTIVE	= 0xC8C1	; then vactive, pixel clock (10 kHz), vfreq*100
CHIP_ID		= 0xF800

	; Preserves everything it touches; returns with R7 = 0x02.
mbox_poll:
	push	psw
	push	acc
	push	dpl
	push	dph
	mov	dptr, #MBOX_MAGIC
	movx	a, @dptr
	cjne	a, #0x4B, mbox_init
	inc	dptr
	movx	a, @dptr
	cjne	a, #0x44, mbox_init
	mov	dptr, #MBOX_CMD
	movx	a, @dptr
	jnz	mbox_run
mbox_out:
	pop	dph
	pop	dpl
	pop	acc
	pop	psw
	ret

	; First tick after power-up: the mailbox RAM holds random data,
	; which must not be taken for a command. Clear DE00-DE0D.
mbox_init:
	mov	dptr, #MBOX_CMD
	clr	a
mbox_init_loop:
	movx	@dptr, a
	inc	dptr
	mov	a, dpl
	cjne	a, #0x0E, mbox_init_clr
	mov	a, #0x4B
	movx	@dptr, a
	inc	dptr
	mov	a, #0x44
	movx	@dptr, a
	sjmp	mbox_out
mbox_init_clr:
	clr	a
	sjmp	mbox_init_loop

	; ================================================================
	; Mailbox command. Runs in the ROM main loop. Saves everything we
	; or the ROM I2C routines touch, including the Keil locals at
	; IRAM 0x26-0x28 and bit 0x08, which may overlay live main-loop
	; variables. EX0/EX1 are masked because the ext0 ISR uses I2C too.
	; ================================================================
mbox_run:
	push	b
	mov	a, r0
	push	acc
	mov	a, r1
	push	acc
	mov	a, r5
	push	acc
	mov	a, r6
	push	acc
	push	0x26
	push	0x27
	push	0x28
	clr	a
	mov	c, 0x08
	rlc	a
	push	acc
	push	0xA8		; IE
	clr	0xA8		; EX0
	clr	0xAA		; EX1

	mov	dptr, #MBOX_CMD
	movx	a, @dptr
	mov	r6, a

	cjne	a, #0x01, mbox_not_01
	; cmd 0x01: signal status -> DE04 = 1 if the input timing is valid,
	; DE05 = chip ID, DE06-DE0D = hactive, vactive, pixel clock / 10 kHz,
	; vfreq * 100 (big-endian, only meaningful if DE04 = 1)
	mov	dptr, #SIG_VALID
	movx	a, @dptr
	mov	r0, a
	mov	dptr, #CHIP_ID
	movx	a, @dptr
	mov	r1, a
	mov	dptr, #MBOX_RESP
	mov	a, r0
	movx	@dptr, a
	inc	dptr
	mov	a, r1
	movx	@dptr, a
	mov	r5, #0x00
sig_copy:
	mov	a, r5
	add	a, #(SIG_HACTIVE & 0xFF)
	mov	dpl, a
	mov	dph, #(SIG_HACTIVE >> 8)
	movx	a, @dptr
	mov	r1, a
	mov	a, r5
	add	a, #((MBOX_RESP + 2) & 0xFF)
	mov	dpl, a
	mov	dph, #(MBOX_RESP >> 8)
	mov	a, r1
	movx	@dptr, a
	inc	r5
	cjne	r5, #8, sig_copy
	ljmp	mbox_done
mbox_not_01:

	cjne	a, #0x05, mbox_not_05
	; cmd 0x05: GPIO read -> P0, P2, P3
	mov	dptr, #MBOX_RESP
	mov	a, 0x80
	movx	@dptr, a
	inc	dptr
	mov	a, 0xA0
	movx	@dptr, a
	inc	dptr
	mov	a, 0xB0
	movx	@dptr, a
	ljmp	mbox_done
mbox_not_05:

	cjne	a, #0x11, mbox_not_11
	; cmd 0x11: I2C read register (P0 = 7-bit addr, P1 = reg)
	; -> DE04 = value (0xFF on failure), DE05 = 1 if every byte was ACKed
	mov	dptr, #MBOX_P0
	movx	a, @dptr
	rl	a
	mov	r0, a		; R0 = addr << 1
	inc	dptr
	movx	a, @dptr
	mov	r1, a		; R1 = reg
	lcall	I2C_START
	mov	a, r0
	mov	r7, a
	lcall	I2C_WRITE
	mov	a, r7
	jz	i2c_rd_fail
	mov	a, r1
	mov	r7, a
	lcall	I2C_WRITE
	mov	a, r7
	jz	i2c_rd_fail
	lcall	I2C_START	; repeated start
	mov	a, r0
	orl	a, #0x01
	mov	r7, a
	lcall	I2C_WRITE
	mov	a, r7
	jz	i2c_rd_fail
	mov	r7, #0x01	; NAK: single byte
	lcall	I2C_READ
	mov	a, r7
	mov	r1, #0x01
	sjmp	i2c_rd_done
i2c_rd_fail:
	mov	a, #0xFF
	mov	r1, #0x00
i2c_rd_done:
	mov	dptr, #MBOX_RESP
	movx	@dptr, a
	inc	dptr
	mov	a, r1
	movx	@dptr, a
	lcall	I2C_STOP
	ljmp	mbox_done
mbox_not_11:

	cjne	a, #0x12, mbox_not_12
	; cmd 0x12: I2C scan 0x50-0x57 -> DE04 = bitmap of ACKing addresses
	mov	r0, #0x00	; bitmap
	mov	r1, #0x01	; bit for current address
	mov	r5, #0x50	; address
i2c_scan_loop:
	lcall	I2C_START
	mov	a, r5
	rl	a
	mov	r7, a
	lcall	I2C_WRITE
	mov	a, r7
	jz	i2c_scan_nak
	mov	a, r1
	orl	a, r0
	mov	r0, a
i2c_scan_nak:
	lcall	I2C_STOP
	mov	a, r1
	rl	a
	mov	r1, a
	inc	r5
	cjne	r5, #0x58, i2c_scan_loop
	mov	dptr, #MBOX_RESP
	mov	a, r0
	movx	@dptr, a
	sjmp	mbox_done
mbox_not_12:

	cjne	a, #0xFE, mbox_unknown
	; cmd 0xFE: identify -> "@kraln"
	mov	dptr, #MBOX_RESP
	mov	r0, #0x00
ident_loop:
	mov	a, r0
	add	a, #(ident_str - ident_base)
	movc	a, @a+pc
ident_base:
	movx	@dptr, a
	inc	dptr
	inc	r0
	cjne	r0, #6, ident_loop
	sjmp	mbox_done
ident_str:
	.ascii	"@kraln"

mbox_unknown:
	mov	a, #0xFF
	sjmp	mbox_status
mbox_done:
	mov	a, #0x02
mbox_status:
	mov	dptr, #MBOX_STATUS
	movx	@dptr, a
	mov	dptr, #MBOX_CMD
	clr	a
	movx	@dptr, a

	pop	0xA8
	pop	acc
	rrc	a
	mov	0x08, c
	pop	0x28
	pop	0x27
	pop	0x26
	pop	acc
	mov	r6, a
	pop	acc
	mov	r5, a
	pop	acc
	mov	r1, a
	pop	acc
	mov	r0, a
	pop	b
	mov	r7, #0x02	; I2C routines use R7; the ROM passed 0x02
	ljmp	mbox_out
