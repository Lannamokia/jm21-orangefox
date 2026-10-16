#!/system/bin/sh
# TWRP mounts /external_sd once, early, and never retries. On this SoC the card
# does not enumerate until ~15s into boot, so that attempt runs against a device
# node that does not exist yet, leaves Actual_Block_Device empty, skips the blkid
# probe and finally calls mount(2) with fstype "auto" -- which the kernel rejects.
#
# Wait for the node, then hand the mount back to TWRP so its own bookkeeping
# (Is_Present, Actual_Block_Device, storage/MTP registration) stays consistent.
i=0
while [ $i -lt 60 ]; do
    [ -e /dev/block/mmcblk1p1 ] && break
    sleep 1
    i=$((i + 1))
done

# Let the recovery UI finish coming up before talking to it.
sleep 4

/system/bin/twrp unmount /external_sd >/dev/null 2>&1
/system/bin/twrp mount /external_sd >/dev/null 2>&1

# The MTP server registered its storage list during boot, before this card
# existed, so it has to be told about it now.
/system/bin/twrp mtp_refresh >/dev/null 2>&1
