AS = sdas8051
# Appended to the stock image at EEPROM code offset 0x400 (XDATA 0xD000).
CODE_LOC = 0xD000

# Default target: the add-on blob. A flashable image needs your own stock
# EEPROM backup: make image STOCK=backup.bin [EDID=edid.bin]
all: mailbox_d7.bin

mailbox_d7.rel: mailbox_d7.asm
	$(AS) -lo $@ $<

mailbox_d7.ihx: mailbox_d7.rel
	sdld -nui -b HOME=$(CODE_LOC) -i $@ $^

mailbox_d7.bin: mailbox_d7.ihx
	sdobjcopy -I ihex -O binary $< $@

image: mailbox_d7.bin
	python build_d7_image.py $(STOCK) eeprom_image_d7.bin $(if $(EDID),--edid $(EDID))

clean:
	rm -f *.ihx *.lst *.rel *.rst *.sym *.map *.mem *.lk *.noi
	rm -f mailbox_d7.bin eeprom_image_d7.bin

.PHONY: all image clean
