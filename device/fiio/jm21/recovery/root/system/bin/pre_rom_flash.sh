#!/system/bin/sh
# OrangeFox calls this before every package install (twrpinstall/twinstall.cpp,
# TWinstall_zip -> FOX_PRE_ROM_FLASH_SCRIPT), always with the zip path as $1.
#
# Snapshot the recovery image of the slot we are running from. Every full A/B OTA
# payload carries recovery.img, and update_engine writes it straight into a
# recovery partition, so without this an OTA silently replaces this recovery.
#
# Only the current slot is captured. The post hook writes this same image into
# whichever recovery slot the package disturbed -- that is deliberately not
# "restore each slot's previous contents", because the inactive slot may well
# have held the stock recovery, and copying that back would not protect anything.
set -u

SLOT="$(getprop ro.boot.slot_suffix)"
DEV="/dev/block/by-name/recovery${SLOT}"
[ -e "$DEV" ] || DEV="/dev/block/by-name/recovery"
[ -e "$DEV" ] || exit 0

IMG=/tmp/.of_rec_survival.img

rm -f "$IMG"
dd if="$DEV" of="$IMG" bs=1M 2>/dev/null
[ -s "$IMG" ] || { rm -f "$IMG"; exit 0; }
sync
