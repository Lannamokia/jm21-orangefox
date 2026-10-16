#!/system/bin/sh
# Counterpart to pre_rom_flash.sh. OrangeFox calls it right before TWinstall_zip
# returns (twrpinstall/twinstall.cpp, FOX_POST_ROM_FLASH_SCRIPT), so it runs when
# the package installed cleanly and when it failed -- there is no early return
# between the two hooks.
#
# Slots are enumerated from by-name instead of ro.boot.slot_suffix on purpose: an
# OTA may write the *inactive* slot and then switch the active slot over to it, in
# which case protecting only the slot we happen to be running from would leave the
# device booting the stock recovery afterwards. Writing the running image into
# every recovery slot the package disturbed covers both that and a same-slot write.
set -u

IMG=/tmp/.of_rec_survival.img
[ -s "$IMG" ] || exit 0

SAVED="$(md5sum "$IMG" | cut -d' ' -f1)"

for DEV in /dev/block/by-name/recovery*; do
    [ -b "$DEV" ] || continue
    [ "$(md5sum "$DEV" | cut -d' ' -f1)" = "$SAVED" ] && continue
    dd if="$IMG" of="$DEV" bs=1M 2>/dev/null
    sync
done

rm -f "$IMG"
