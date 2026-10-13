	.module mailbox_d7

	.area HOME    (CODE)

	; ================================================================
	; MS2109 "D7" mailbox, RAM-only variant, linked at 0xD000
	;
	; `ms2109_mailbox.py load` writes this to XDATA 0xD000 on a unit
	; still running its stock EEPROM, and points the CC00 hook here.
	; It adds the mailbox and passes every hook call on to the stock
	; handler through `chain`. Gone at the next unplug. The flashable
	; firmware is firmware_d7.asm.
	; ================================================================

	; +0x00: CC00 hook lands here (via a trampoline)
	ljmp	hook_entry

	; +0x03: stock hook handler. The operand is patched with the
	; stock CC00 jump target by ms2109_mailbox.py.
chain:
	ljmp	0xCD6E

	; +0x06: signature + version, checked by the host tools
	.ascii	"kD7mb"
	.db	0x02

hook_entry:
	cjne	r7, #0x02, chain
	lcall	mbox_poll
	sjmp	chain

	.include "mailbox_d7.inc"
