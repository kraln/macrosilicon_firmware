AS = sdas8051

# make                      -> eeprom_image_d7.bin (flash with ms_hid.py)
# make EDID=my_edid.bin     custom EDID
# make VID=0x534D PID=0x2109  USB ID override
IMAGE_OPTS = $(if $(EDID),--edid $(EDID)) $(if $(VID),--vid $(VID)) \
	$(if $(PID),--pid $(PID))

all: eeprom_image_d7.bin mailbox_d7.bin

eeprom_image_d7.bin: firmware_d7.bin build_d7_image.py
	python build_d7_image.py firmware_d7.bin $@ $(IMAGE_OPTS)

# Full EEPROM firmware, loaded by the ROM at XDATA 0xCC00
firmware_d7.rel: firmware_d7.asm mailbox_d7.inc
	$(AS) -lo $@ $<

firmware_d7.ihx: firmware_d7.rel
	sdld -nui -b HOME=0xCC00 -i $@ $^

# RAM-only mailbox for `ms2109_mailbox.py load` on a stock unit
mailbox_d7.rel: mailbox_d7.asm mailbox_d7.inc
	$(AS) -lo $@ $<

mailbox_d7.ihx: mailbox_d7.rel
	sdld -nui -b HOME=0xD000 -i $@ $^

%.bin: %.ihx
	sdobjcopy -I ihex -O binary $< $@

clean:
	rm -f *.ihx *.lst *.rel *.rst *.sym *.map *.mem *.lk *.noi
	rm -f firmware_d7.bin mailbox_d7.bin eeprom_image_d7.bin

.PHONY: all clean
