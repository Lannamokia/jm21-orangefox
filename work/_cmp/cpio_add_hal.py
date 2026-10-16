"""Phase 2: add the vendor Keymaster/KeyMint HAL stack to the v31 crypto ramdisk
so TWRP can unwrap the wrapped FBE/metadata keys and mount /data.

The recovery ramdisk carries no /vendor/bin and no /vendor/etc/init, so init
never sees the stock vendor service definitions for these daemons. This adds the
binaries + their QTI libraries + one rc that defines and starts them, using the
same seclabel trick TWRP already uses for its own keystore2.rc (u:r:recovery:s0).

SELinux is switched to permissive inside recovery only, because neither the
ramdisk's vendor_file_contexts nor the recovery policy has entrypoint rules for
these newly added vendor binaries. The Android system partition is untouched.

Usage: python work/_cmp/cpio_add_hal.py [--no-image]
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(PROJ, "tools"))
sys.path.insert(0, HERE)
import bootimg                                                        # noqa: E402
from cpio_edit import read_cpio, write_cpio                           # noqa: E402

SRC_CPIO = os.path.join(PROJ, "work/_cmp/v31.cpio")
KM = os.path.join(PROJ, "work/_cmp/km")
KM2 = os.path.join(PROJ, "work/_cmp/km2")
BASE_IMG = os.path.join(PROJ, "work/_cmp/v28-crypto-gzip.img")
OUT_CPIO = os.path.join(PROJ, "work/_cmp/v32.cpio")
OUT_RD = os.path.join(PROJ, "work/_cmp/v32_crypto.ramdisk")
OUTIMG = os.path.join(PROJ, "work/v32-crypto-km-lz4.img")

# path in the image  ->  source file, relative to work/_cmp/km (or km2)
FILES = {
    "vendor/bin/hw/android.hardware.keymaster@4.0-service-qti": ("km", "bin/hw/android.hardware.keymaster@4.0-service-qti"),
    "vendor/bin/qseecomd": ("km", "bin/qseecomd"),
    "vendor/lib64/libqtikeymaster4.so": ("km", "lib64/libqtikeymaster4.so"),
    "vendor/lib64/libkeymasterutils.so": ("km", "lib64/libkeymasterutils.so"),
    "vendor/lib64/libkeymasterdeviceutils.so": ("km", "lib64/libkeymasterdeviceutils.so"),
    "vendor/lib64/libqcbor.so": ("km", "lib64/libqcbor.so"),
    "vendor/lib64/libQSEEComAPI.so": ("km", "lib64/libQSEEComAPI.so"),
    "vendor/lib64/libdrmfs.so": ("km", "lib64/libdrmfs.so"),
    "vendor/lib64/vendor.qti.hardware.qseecom@1.0.so": ("km", "lib64/vendor.qti.hardware.qseecom@1.0.so"),
    "system/lib64/libdmabufheap.so": ("km", "libdmabufheap.so"),
    # qseecomd -> libdrmfs.so -> libdiag.so -> libxml2.so. libxml2 had been
    # dropped earlier as "unreferenced"; DT_NEEDED says otherwise.
    "vendor/lib64/libdiag.so": ("km", "libdiag.so"),
    "system/lib64/libxml2.so": ("km", "libxml2.so"),
    # Restore the vendor environment the vendor HALs were built for. recovery
    # shipped none of it, which is the same class of gap as the missing VINTF
    # manifests and device nodes:
    #   - /vendor/build.prop carries the ro.vendor.* / ro.hardware.* properties
    #     the QTI keymaster stack reads (ro.arch, ro.vendor.build.security_patch,
    #     ro.hardware.keystore_desede, ...)
    #   - /vendor/etc/ueventd.rc is what gives /dev/qseecom its real ownership
    #     (system:drmrpc 0660) and creates/labels /dev/dma_heap/* and /dev/ion
    "vendor/build.prop": ("km", "vendor-build.prop"),
    "vendor/etc/ueventd.rc": ("km", "vendor-ueventd.rc"),
    "vendor/bin/hw/vendor.qti.hardware.qseecom@1.0-service": ("km", "vendor.qti.hardware.qseecom@1.0-service"),
    "vendor/lib64/hw/vendor.qti.hardware.qseecom@1.0-impl.so": ("km", "qseecom-impl.so"),
    # The passthrough QSEECom implementation lives on /vendor, but its memory
    # transport dependencies normally come from the VNDK APEX. Recovery's
    # default linker namespace cannot find them there. Without these three
    # libraries getService(..., getStub=true) fails and the service exits 1.
    # All three were tested together on the device before adding them here.
    "system/lib64/libhidlmemory.so": ("hidlmem", "libhidlmemory.so"),
    "system/lib64/android.hidl.memory@1.0.so": ("hidlmem", "android.hidl.memory@1.0.so"),
    "system/lib64/android.hidl.memory.token@1.0.so": ("hidlmem", "android.hidl.memory.token@1.0.so"),
    "system/lib64/android.hidl.allocator@1.0.so": ("hidlmem", "android.hidl.allocator@1.0.so"),
    # Android 13's keystore2 talks to KeyMint (AIDL), not Keymaster (HIDL).
    # system/vold/Keymaster.cpp blocks in AServiceManager_waitForService on
    # android.system.keystore2.IKeystoreService/default, so without a running
    # keystore2 TWRP's metadata decryption hangs forever -> black splash.
    "vendor/bin/hw/android.hardware.security.keymint-service-qti": ("km2", "android.hardware.security.keymint-service-qti"),
    "vendor/lib64/libqtikeymint.so": ("km2", "libqtikeymint.so"),
    "vendor/lib64/android.hardware.security.keymint-V2-ndk.so": ("km2", "android.hardware.security.keymint-V2-ndk.so"),
    "vendor/lib64/android.hardware.security.secureclock-V1-ndk.so": ("km2", "android.hardware.security.secureclock-V1-ndk.so"),
    "vendor/lib64/android.hardware.security.sharedsecret-V1-ndk.so": ("km2", "android.hardware.security.sharedsecret-V1-ndk.so"),
    # Gatekeeper: only needed when the device has a lock screen. Without it the
    # synthetic-password branch of Decrypt_User_Synth_Pass() returns at
    # system/vold/Decrypt.cpp:769 with "failed to get gatekeeper service", so CE
    # (/data/media -> /sdcard, MTP internal storage) stays unreadable -- the
    # VINTF fragment declaring @1.0::IGatekeeper/default has been shipping in
    # this ramdisk all along, with nothing behind it.
    #
    # The service is NOT the usual HIDL passthrough shim: it DT_NEEDEDs
    # libhardware.so and reaches its implementation through hw_get_module(), so
    # the impl lives in vendor/lib64/hw and is invisible to a DT_NEEDED closure
    # (work/_cmp/gkcheck.py walks both files). Its only non-ramdisk dependencies
    # are libkeymasterdeviceutils.so and libqcbor.so, which the keymaster work
    # above already pulled in.
    "vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti": ("gk", "bin/hw/android.hardware.gatekeeper@1.0-service-qti"),
    "vendor/lib64/hw/android.hardware.gatekeeper@1.0-impl-qti.so": ("gk", "lib64/hw/android.hardware.gatekeeper@1.0-impl-qti.so"),
}

# qseecomd dlopens one library per service it brings up. Those names live in its
# own error strings ("Init dlopen(%s, RTLD_NOW) is failed....") and are therefore
# INVISIBLE to any DT_NEEDED-based closure -- which is why qseecomd kept exiting
# (255) with all of its declared dependencies satisfied. This is the transitive
# DT_NEEDED closure of those ten names, resolved against the real vendor blobs.
DLOPEN_LIBS = [
    "libGPreqcancel.so", "libgpt.so", "librpmb.so", "libssd.so", "libspl.so",
    "libops.so", "libqisl.so", "libsecureui.so", "libdrmtime.so",
    "libGPreqcancel_svc.so", "libdrm.so", "libdisplayconfig.qti.so",
    "libStDrvInt.so", "libsecureui_svcsock.so", "vendor.display.config@1.0.so",
    "libtime_genoff.so", "vendor.display.config@2.0.so",
    "vendor.qti.hardware.tui_comm@1.0.so",
]
for _n in DLOPEN_LIBS:
    FILES["vendor/lib64/" + _n] = ("km", "lib64/" + _n)

EXEC = {"vendor/bin/hw/android.hardware.keymaster@4.0-service-qti",
        "vendor/bin/qseecomd",
        "vendor/bin/hw/android.hardware.security.keymint-service-qti",
        "vendor/bin/hw/vendor.qti.hardware.qseecom@1.0-service",
        "vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti"}
DIRS = [("vendor/bin", 0o040755), ("vendor/bin/hw", 0o040755),
        ("vendor/lib64/hw", 0o040755)]

# Blobs pulled straight from /vendor on the device (see work/_cmp/gkcheck.py).
GK = os.path.join(PROJ, "work/_cmp/gk")

RC = """# OrangeFox JM21 -- bring up the vendor Keymaster HAL inside recovery so TWRP can
# unwrap the wrapped metadata/FBE keys and mount /data.
#
# The recovery ramdisk has no /vendor/bin and no /vendor/etc/init, so init never
# sees the stock vendor service definitions for these daemons; these are copies
# of them, with the same seclabel TWRP already uses for its own keystore2.rc
# (u:r:recovery:s0) so that no new SELinux domain is required.
#
# SELinux is relaxed to permissive for recovery only: neither the ramdisk's
# vendor_file_contexts nor the recovery policy grants entrypoint/exec on the
# newly added vendor binaries. The Android system partition is untouched.

service of-km-wait /system/bin/sh /system/bin/of-km-wait.sh
    oneshot
    user root
    group root
    seclabel u:r:recovery:s0

service of-persist /system/bin/sh /system/bin/of-persist.sh
    oneshot
    user root
    group root system
    seclabel u:r:recovery:s0

service vendor.qseecomd /vendor/bin/qseecomd
    socket notify-topology stream 660 system drmrpc
    class core
    user root
    group root drmrpc
    seclabel u:r:recovery:s0

# NOTE: these run as root rather than the stock "user system". /dev/qseecom is
# 0600 root:root, because the recovery ramdisk has none of the vendor ueventd
# rules that chown it to system:drmrpc on a normal boot -- so a system-user
# keymaster cannot open the TEE at all and aborts (SIGABRT) during init.
# vendor.qseecomd, which the stock rc already starts as root, is the control:
# it stays up, and a root-run keymint service registers all four AIDL
# interfaces with servicemanager.
# stdio_to_kmsg on every vendor daemon: without it init sends their stdout and
# stderr to /dev/null, which is why "the keymint HAL runs but never shows up in
# servicemanager" was invisible. With it, dmesg carries whatever the daemon
# itself has to say about a failed registration.
service qseecom-service /system/bin/sh /system/bin/of-qseecom.sh
    class hal
    user root
    group root drmrpc
    stdio_to_kmsg
    seclabel u:r:recovery:s0

service keymaster-4-0-qti /vendor/bin/hw/android.hardware.keymaster@4.0-service-qti
    class hal
    user root
    group root drmrpc
    stdio_to_kmsg
    seclabel u:r:recovery:s0

service vendor.keymint-qti /vendor/bin/hw/android.hardware.security.keymint-service-qti
    class hal
    user root
    group root drmrpc
    stdio_to_kmsg
    seclabel u:r:recovery:s0

# The vendor boot HAL, copied from /vendor/etc/init/android.hardware.boot@1.2-service.rc.
# init parses /system/etc/init (init.cpp:310) before /vendor/etc/init (:316) and
# keeps the first definition of a name (service_parser.cpp:651), so this copy
# wins -- deliberately, for two reasons:
#
#   * stdio_to_kmsg. libbootloader_message's get_misc_blk_device() reads fs_mgr's
#     /etc/recovery.fstab, and in recovery fs_mgr looks for exactly that path
#     (fs_mgr_fstab.cpp:835). With that file missing the HAL cannot initialise,
#     exits 1, and init respawns it about once a second forever; a client's
#     blocking getService() then never returns. That is what made a stock OTA
#     flashed from recovery sit at 0% with no error message. Without this line
#     the reason is invisible: the stock definition has no stdio_to_kmsg, so
#     init sends the failure text to /dev/null.
#   * it is started below instead of on demand, so the interface is registered
#     and stable before anything asks for it. update_engine keeps the
#     IBootControl proxy it obtained at start-up and calls setActiveBootSlot
#     only at the very end (postinstall_runner_action.cc): if the service is
#     mid-restart at that moment the call returns DEAD_OBJECT
#     (boot_control_android.cc:137), the postinstall action fails, and a fully
#     written and verified payload is reported as a failed install.
service vendor.boot-hal-1-2 /vendor/bin/hw/android.hardware.boot@1.2-service
    interface android.hardware.boot@1.0::IBootControl default
    interface android.hardware.boot@1.1::IBootControl default
    interface android.hardware.boot@1.2::IBootControl default
    class early_hal
    user root
    group root
    stdio_to_kmsg
    seclabel u:r:recovery:s0

# Gatekeeper, same name as the stock definition (init keeps the first definition
# of a name, and /system/etc/init is parsed before /vendor/etc/init) so that:
#   * it actually runs. The stock rc is "class early_hal" with no interface line,
#     and nothing in recovery triggers that class -- so on stock recovery nothing
#     ever provides @1.0::IGatekeeper/default either, which is why the stock
#     recovery cannot decrypt CE on a device with a lock screen.
#   * it is started from on init below instead of relying on the lazy
#     ctl.interface_start path.
#   * stdio_to_kmsg makes a failure legible: Decrypt.cpp:769 prints
#     "failed to get gatekeeper service" only if the client times out, and the
#     service's own reason for dying would otherwise go to /dev/null.
# Runs as root rather than the stock "user system" for the same reason the
# keymaster daemons do: /dev/qseecom is 0600 root:root in this ramdisk and the
# QTI gatekeeper implementation talks to the TEE through it.
service gatekeeper-1-0 /system/bin/sh /system/bin/of-gatekeeper.sh
    interface android.hardware.gatekeeper@1.0::IGatekeeper default
    class early_hal
    user root
    group root
    stdio_to_kmsg
    seclabel u:r:recovery:s0

on init
    write /sys/fs/selinux/enforce 0
    write /proc/sys/kernel/core_pattern /tmp/core.%p
    chmod 0666 /dev/qseecom
    # ABL does not pass androidboot.flash.locked in recovery mode, but a working
    # Android boot has it (0 = unlocked). The QTI keymaster TA builds its
    # RootOfTrust from these ro.boot.* values; a missing one is the only
    # environmental difference we found that could make every TEE operation
    # fail with KM_ERROR_SECURE_HW_COMMUNICATION_FAILED.
    setprop ro.boot.flash.locked 0
    start vendor.boot-hal-1-2
    start of-persist
    start of-km-wait
    start vendor.qseecomd
    start qseecom-service
    start keymaster-4-0-qti
    start vendor.keymint-qti

# The GK TA accesses secure storage through qseecomd's GP FS listener. The
# stock vendor config names /mnt/vendor/persist/data/. Keep TWRP's /persist
# mount canonical and expose the vendor path as a bind mount. Read-only is
# insufficient: a correct PIN returned -49 until persist became writable.
on property:of.persist.ready=1
    start gatekeeper-1-0
"""
RC_PATH = "system/etc/init/keymaster-recovery.rc"

# keystore2 aborts ~5.4 s after starting -- before the KeyMint HAL has finished
# pulling its TEE TA and registering. TWRP attempts decryption exactly once, in
# that window, so it fails and /data stays unmounted. Get the abort message:
# init runs services with RLIMIT_CORE 0, so raise it here and have the rc point
# core_pattern at /tmp.
KEYSTORE2_RC_PATH = "system/etc/init/keystore2.rc"

# Wait for the KeyMint HAL before init starts keystore2 (see module docstring).
KS2_SHIM_PATH = "system/bin/of-keystore2.sh"
# Prefer the device's own Android 13 keystore2 over the ramdisk's AOSP 12.1 one.
# See work/_cmp/patch7_ks2.py for the reasoning; the short version is that the
# A13 keystore2 is the only configuration known to be able to open this device's
# metadata key blob, and it is far too large (11.9 MiB with its dependency
# closure) to fit in the 2.62 MiB of ramdisk headroom, whereas /super's system_b
# logical volume is already mapped here and mounting it read-only is free.
KS2_SHIM_SH = """#!/system/bin/sh
SRC=/mnt/ks2src
LOG=/tmp/of-keystore2.log
data=$1
[ -z "$data" ] && data=/tmp/misc/keystore
mkdir -p "$data" 2>/dev/null

i=0
while [ $i -lt 8 ]; do
    grep -q " $SRC " /proc/mounts && break
    mkdir -p $SRC 2>/dev/null
    if [ -e /dev/block/mapper/system_b ]; then
        mount -t ext4 -o ro /dev/block/mapper/system_b $SRC >> $LOG 2>&1 && break
        mount -t erofs -o ro /dev/block/mapper/system_b $SRC >> $LOG 2>&1 && break
    fi
    i=$((i + 1))
    sleep 1
done

if [ -x $SRC/system/bin/keystore2 ]; then
    echo "of-keystore2: A13 keystore2 from $SRC, db=$data" >> $LOG
    LD_LIBRARY_PATH=$SRC/system/lib64:$SRC/system_ext/lib64:$SRC/system_dlkm/lib64:$SRC/vendor/lib64 \
        $SRC/system/bin/keystore2 "$data" >> $LOG 2>&1 &
    pid=$!
    sleep 3
    if kill -0 $pid 2>/dev/null; then
        echo "of-keystore2: serving as pid $pid" >> $LOG
        wait $pid
        exit 0
    fi
    echo "of-keystore2: A13 keystore2 died at startup, falling back" >> $LOG
else
    echo "of-keystore2: $SRC not usable, falling back" >> $LOG
fi
exec /system/bin/keystore2 "$data"
"""

KM_WAIT_PATH = "system/bin/of-km-wait.sh"
KM_WAIT_SH = """#!/system/bin/sh
sleep 8
setprop of.keystore2.go 1
"""
PERSIST_PATH = "system/bin/of-persist.sh"
QSEE_SH_PATH = "system/bin/of-qseecom.sh"
GK_SH_PATH = "system/bin/of-gatekeeper.sh"
GK_SH = """#!/system/bin/sh
# Avoid holding TWRP's temporary stock /vendor mount during HAL discovery.
while ! grep -qs ' /data ' /proc/mounts; do
    sleep 1
done
exec /vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti
"""
QSEE_SH = """#!/system/bin/sh
# TWRP temporarily mounts stock /vendor during startup, then unmounts it to
# expose the Recovery-compatible VINTF manifest. A working QSEE passthrough
# service must not pin that temporary mount (A13 manifest breaks A12 keystore).
# Metadata decryption already works without this HIDL service.
while ! grep -qs ' /data ' /proc/mounts; do
    sleep 1
done
exec /vendor/bin/hw/vendor.qti.hardware.qseecom@1.0-service
"""
PERSIST_SH = """#!/system/bin/sh
exec >> /tmp/of-persist.log 2>&1
mkdir -p /persist /mnt/vendor/persist
i=0
while [ "$i" -lt 20 ]; do
    if grep -qs ' /persist ' /proc/mounts; then
        break
    fi
    if [ -b /dev/block/bootdevice/by-name/persist ]; then
        mount -t ext4 -o rw,noatime,nosuid,nodev /dev/block/bootdevice/by-name/persist /persist && break
    fi
    i=$((i + 1))
    sleep 1
done
if ! grep -qs ' /persist ' /proc/mounts; then
    echo 'OF: persist mount failed; PIN Gatekeeper unavailable'
    exit 1
fi
if ! grep -qs ' /mnt/vendor/persist ' /proc/mounts; then
    mount -o bind /persist /mnt/vendor/persist || exit 1
fi
echo 'OF: persist ready at /persist and /mnt/vendor/persist'
setprop of.persist.ready 1
"""
KEYSTORE2_RLIMIT = "    rlimit core unlimited unlimited\n"

# Real /vendor/etc/vintf tree, pulled from the device's vendor partition.
# The recovery ramdisk originally shipped 3 manifest fragments and NO
# manifest.xml at all. libvintf's fetchDeviceHalManifest() then returns
# NAME_NOT_FOUND, VintfObject::GetDeviceHalManifest() yields a null HalManifest,
# and keystore2 dereferences it -- SIGSEGV at libkeystore2_vintf_cpp.so+0x1848,
# the instruction right after the GetDeviceHalManifest() PLT call (verified from
# a core dump).
#
# Simply installing the device's real Android-13 manifest.xml does NOT work
# either: its meta version is 5.0 while this tree's libvintf has
# include/vintf/constants.h: constexpr Version kMetaVersion{4, 0};
# and parse_xml.cpp buildObject() does
#     if (metaVersion > kMetaVersion) { *error = "Unrecognized manifest.version"; return false; }
# so the parse fails and the manifest stays null. Same crash.
#
# What works instead: a minimal version-1.0 manifest.xml (so the vendor manifest
# is found and addDirectoryManifests() then merges the fragment directory) plus
# only the fragment that declares the KeyMint AIDL HAL. The ramdisk's own
# fragments stay as they are.
VINTF = os.path.join(PROJ, "work/_cmp/vintf/vintf")
VINTF_MANIFEST_XML = b'<manifest version="1.0" type="device">\n</manifest>\n'
# ... and the same trap on the framework side: AIDL servicemanager loads
# /system/etc/vintf/manifest.xml to decide whether a service registration is
# legitimate. Without it, keystore2 gets as far as creating its service and then
# dies with
#   panicked at 'Failed to register service
#     android.system.keystore2.IKeystoreService/default because of UNKNOWN_ERROR.'
# The ramdisk already carries /system/etc/vintf/manifest/
# android.system.keystore2-service.xml, which declares exactly that service;
# only the top-level manifest.xml is missing.
VINTF_FRAMEWORK_XML = b'<manifest version="1.0" type="framework">\n</manifest>\n'
VINTF_FRAGMENTS = [
    "android.hardware.security.keymint-service-qti.xml",
    # The HIDL services the recovery now runs have to be declared too, or
    # hwservicemanager refuses their registerAsService(): that is exactly why
    # vendor.qti.hardware.qseecom@1.0-service kept exiting with
    #   "IQSEECom::getService() failed / registerAsService() failed with status: %d"
    # These two blocks are lifted verbatim out of the device's real
    # manifest.xml (which we cannot ship wholesale: it is meta version 5.0).
    "vendor.qti.hardware.qseecom@1.0-service.xml",
    "android.hardware.gatekeeper@1.0-service-qti.xml",
]

VENDOR_RD = 7340032
# measured 2026-10-16 (work/_cmp/probe_budget.py): ctx[0x78] is the main image's
# ramdisk (= 0 for boot_<slot>), NOT init_boot's, so the recovery fragment cap is
# 37,216,256 - 7,340,032 and a 27,482,454-byte fragment still boots.
FRAG_LIMIT = 37216256 - VENDOR_RD


import re

# Root-of-Trust inputs. The QTI KeyMint HAL builds the RoT from these and the TEE
# stamps it into every key blob, so recovery has to claim the same Android
# version and patch level the device really runs, or the TA declares every
# existing key as needing an upgrade (KeyMint KEY_REQUIRES_UPGRADE, -62) and the
# follow-up upgradeKey dies with INVALID_ARGUMENT (-38). See
# work/_cmp/patch8_rot.py for how the expected values were read off the device's
# own metadata key blob.
ROT_PROPS = {
    "ro.build.version.release": "13",
    "ro.build.version.release_or_codename": "13",
    "ro.build.version.security_patch": "2023-02-05",
}


def apply_rot_props(by_name):
    e = by_name.get("prop.default")
    if e is None:
        print("  prop.default: ABSENT, Root-of-Trust not corrected")
        return 0
    txt = e["data"].decode("utf-8", "replace")
    changed = []
    for key, want in sorted(ROT_PROPS.items()):
        new, n = re.subn(r"(?m)^%s=.*$" % re.escape(key), "%s=%s" % (key, want), txt)
        if n == 0:
            new = txt.rstrip("\n") + "\n%s=%s\n" % (key, want)
            print("  prop.default: %s added (%s)" % (key, want))
        elif new != txt:
            print("  prop.default: %s -> %s" % (key, want))
        txt = new
        changed.append(key)
    e["data"] = txt.encode()
    return len(changed)


# fs_mgr::ReadDefaultFstab() chooses its path by probing for /system/bin/recovery
# and in that case reads exactly /etc/recovery.fstab (fs_mgr_fstab.cpp:835). The
# stock FiiO ramdisk satisfied that because its /etc is a symlink to
# /system/etc, which carries the full AOSP recovery fstab. This ramdisk has /etc
# as a real directory holding only twrp.fstab, so /etc/recovery.fstab did not
# exist and ReadDefaultFstab() returned false.
#
# That one missing file broke every fs_mgr consumer in recovery. The one that
# mattered is libbootloader_message's get_misc_blk_device(): no fstab -> no
# /misc entry -> android.hardware.boot@1.2-service could not initialise
#
#   libboot_control.cpp:383    Could not read merge status: failed to read
#                              default fstab
#   ServiceManagement.cpp:417  Could not find instance 'default' in library
#                              android.hardware.boot@1.0-impl-1.2-qti.so
#
# -> HIDL_FETCH_IBootControl() returned nullptr, the service exited 1, init's
# ctl.interface_start loop respawned it every ~2 s, and update_engine_sideload
# spun forever on
#
#   Waited one second for android.hardware.boot@1.0::IBootControl/default
#
# TWRP draws the install progress bar from update_engine_sideload's --status_fd,
# so flashing the stock full OTA from recovery sat at 0% with no error message.
#
# Deliberately minimal: only /misc, which is all get_misc_blk_device() asks for.
# Keeping /data out of this file stops vold's mount_via_fs_mgr() from finding a
# second, non-FBE /data entry to mount (see patch10_skipfsmgr.py). /etc/twrp.fstab
# still owns everything TWRP mounts: twrp.cpp:534 reads it first and only falls
# back to /etc/recovery.fstab when it is absent.
RECOVERY_FSTAB_PATH = "etc/recovery.fstab"
RECOVERY_FSTAB = """\
# OrangeFox JM21 -- fs_mgr's recovery-mode fstab.  See work/_cmp/cpio_add_hal.py
# for the full story: without this file ReadDefaultFstab() fails in recovery,
# libbootloader_message cannot find /misc, the vendor boot HAL cannot
# initialise, and an A/B OTA sideload hangs with no progress bar.
/dev/block/bootdevice/by-name/misc    /misc    emmc    defaults    defaults
"""


def main():
    entries = read_cpio(open(SRC_CPIO, "rb").read())
    # read_cpio keeps the TRAILER entry; drop it so exactly one trailer, at the
    # very end, is emitted by write_cpio -- otherwise everything we append here
    # would land after the trailer.
    entries = [e for e in entries if e["name"] != "TRAILER!!!"]
    by_name = {e["name"]: e for e in entries}
    print("read %d entries" % len(entries))

    for d, mode in DIRS:
        if d not in by_name:
            entries.append(dict(name=d, ino=0, mode=mode, uid=0, gid=0, nlink=2,
                                mtime=0, data=b""))
            by_name[d] = entries[-1]
            print("  mkdir    %s" % d)

    added = 0
    srcroot = {"km": KM, "km2": KM2, "gk": GK,
               "hidlmem": os.path.join(PROJ, "work/_cmp/hidl-memory-a13")}
    for dst, (srcdir, src) in FILES.items():
        p = os.path.join(srcroot[srcdir], src)
        if not os.path.exists(p):
            raise SystemExit("Missing required HAL input: %s/%s" % (srcdir, src))
        data = open(p, "rb").read()
        mode = 0o100755 if dst in EXEC else 0o100644
        assert dst not in by_name, dst
        e = dict(name=dst, ino=0, mode=mode, uid=0, gid=0, nlink=1, mtime=0, data=data)
        entries.append(e)
        by_name[dst] = e
        added += len(data)
        print("  add      %-56s %8d B  %s" % (dst, len(data), oct(mode)))

    # Drop any /usb-otg entry from TWRP's fstab. With voldmanaged=usbotg:auto
    # TWRP enumerates every block device it can find as writable USB-OTG
    # storage -- on this device that produced /usb-otg-1..N covering mmcblk0boot0,
    # mmcblk0boot1 and every mmcblk0pNN (i.e. userdata, metadata, misc, persist,
    # ...), all of them mountable and writable.  Writing to those is a brick
    # risk, and they clutter the mount page with unidentifiable "Storage N"
    # entries.
    #
    # NOTE: the ramdisk has /etc as a REAL DIRECTORY, so /etc/twrp.fstab and
    # /system/etc/twrp.fstab are two different files -- and TWRP reads the /etc
    # one (observed: the /etc copy still carried the entry while the
    # /system/etc copy had it dropped).  Strip every copy we can find.
    nfus = 0
    for path in ("etc/twrp.fstab", "system/etc/twrp.fstab", "etc/twrp.fstab.org"):
        e = by_name.get(path)
        if e is None:
            continue
        lines = e["data"].decode().splitlines(True)
        kept = [l for l in lines if "/usb-otg" not in l]
        dropped = len(lines) - len(kept)
        if dropped:
            e["data"] = "".join(kept).encode()
            print("  fstab    %-24s dropped %d /usb-otg line(s)" % (path, dropped))
            nfus += dropped
    if not nfus:
        print("  fstab    no /usb-otg entry found in any copy")

    # fs_mgr's recovery-mode fstab. RECOVERY_FSTAB above has the whole story:
    # whether this file exists decides whether the vendor boot HAL can come up,
    # and therefore whether an A/B OTA can be flashed from recovery at all.
    e = by_name.get(RECOVERY_FSTAB_PATH)
    if e is None:
        e = dict(name=RECOVERY_FSTAB_PATH, ino=0, mode=0o100644, uid=0, gid=0,
                 nlink=1, mtime=0, data=b"")
        entries.append(e)
        by_name[RECOVERY_FSTAB_PATH] = e
    e["data"] = RECOVERY_FSTAB.encode()
    added += len(e["data"])
    print("  fstab    %-24s installed (%d B, /misc only)"
          % (RECOVERY_FSTAB_PATH, len(e["data"])))

    nv = nvb = 0
    todo = [("vendor/etc/vintf/manifest.xml", VINTF_MANIFEST_XML),
            ("system/etc/vintf/manifest.xml", VINTF_FRAMEWORK_XML)]
    for f in VINTF_FRAGMENTS:
        todo.append(("vendor/etc/vintf/manifest/" + f,
                     open(os.path.join(VINTF, "manifest", f), "rb").read()))
    for dst, data in todo:
        if dst in by_name:
            by_name[dst]["data"] = data
        else:
            e = dict(name=dst, ino=0, mode=0o100644, uid=0, gid=0, nlink=1,
                     mtime=0, data=data)
            entries.append(e)
            by_name[dst] = e
        added += len(data)
        nv += 1
        nvb += len(data)
        print("  vintf    %-56s %6d B" % (dst, len(data)))
    print("  vintf    %d files, %d B total" % (nv, nvb))

    e = dict(name=KS2_SHIM_PATH, ino=0, mode=0o100755, uid=0, gid=0, nlink=1,
             mtime=0, data=KS2_SHIM_SH.encode())
    entries.append(e)
    by_name[KS2_SHIM_PATH] = e
    added += len(e["data"])
    print("  add      %-56s (A13 keystore2 loader)" % KS2_SHIM_PATH)

    e = dict(name=KM_WAIT_PATH, ino=0, mode=0o100755, uid=0, gid=0, nlink=1,
             mtime=0, data=KM_WAIT_SH.encode())
    entries.append(e)
    by_name[KM_WAIT_PATH] = e
    print("  add      %-56s (keystore2 delay shim)" % KM_WAIT_PATH)

    e = dict(name=PERSIST_PATH, ino=0, mode=0o100755, uid=0, gid=0, nlink=1,
             mtime=0, data=PERSIST_SH.encode())
    entries.append(e)
    by_name[PERSIST_PATH] = e
    added += len(e["data"])
    print("  add      %-56s (writable persist for Gatekeeper)" % PERSIST_PATH)

    e = dict(name=QSEE_SH_PATH, ino=0, mode=0o100755, uid=0, gid=0, nlink=1,
             mtime=0, data=QSEE_SH.encode())
    entries.append(e)
    by_name[QSEE_SH_PATH] = e
    added += len(e["data"])
    print("  add      %-56s (QSEE starts after temporary vendor mount)" % QSEE_SH_PATH)

    e = dict(name=GK_SH_PATH, ino=0, mode=0o100755, uid=0, gid=0, nlink=1,
             mtime=0, data=GK_SH.encode())
    entries.append(e)
    by_name[GK_SH_PATH] = e
    added += len(e["data"])
    print("  add      %-56s (Gatekeeper starts after temporary vendor mount)" % GK_SH_PATH)

    apply_rot_props(by_name)

    ks2 = by_name.get(KEYSTORE2_RC_PATH)
    # The A13 keystore2 shim is disabled for now: it panics (the AIDL KeyMint
    # service is not registered in recovery, see README 17), and because the shim
    # only falls back when the binary dies inside its first 3 s, a panic at ~4 s
    # leaves keystore2 dead instead of falling back. Re-enable deliberately.
    if ks2 is not None and b"of-keystore2.sh" in ks2["data"]:
        ks2["data"] = ks2["data"].decode().replace(
            "service keystore2 /system/bin/sh /system/bin/of-keystore2.sh /tmp/misc/keystore",
            "service keystore2 /system/bin/keystore2 /tmp/misc/keystore").encode()
        print("  keystore2.rc: reverted to ramdisk keystore2 (shim off)")
    # keystore2 is Rust: it logs through liblog, and with logd absent in recovery
    # those lines were dropped outright -- which is why "the lock-screen path
    # dies inside keystore2" left no trace anywhere. stdio_to_kmsg puts them in
    # dmesg alongside the vendor daemons' output.
    if ks2 is not None and "stdio_to_kmsg" not in ks2["data"].decode():
        txt = ks2["data"].decode().replace(
            "service keystore2 /system/bin/keystore2 /tmp/misc/keystore\n",
            "service keystore2 /system/bin/keystore2 /tmp/misc/keystore\n"
            "    stdio_to_kmsg\n")
        ks2["data"] = txt.encode()
        print("  keystore2.rc: stdio_to_kmsg")
    if ks2 is not None and "on property:of.keystore2.go=1" not in ks2["data"].decode():
        txt = ks2["data"].decode().replace(
            "on late-init\n    start keystore2\n",
            "# started by of-km-wait once the KeyMint HAL has registered\n"
            "on property:of.keystore2.go=1\n    start keystore2\n")
        ks2["data"] = txt.encode()
        print("  keystore2.rc: start moved behind of.keystore2.go")
    if ks2 is not None and b"rlimit core" not in ks2["data"]:
        txt = ks2["data"].decode()
        txt = txt.replace("    writepid /dev/cpuset/foreground/tasks\n",
                          "    writepid /dev/cpuset/foreground/tasks\n" + KEYSTORE2_RLIMIT)
        ks2["data"] = txt.encode()
        print("  keystore2.rc: raised RLIMIT_CORE (diagnostic)")

    e = dict(name=RC_PATH, ino=0, mode=0o100644, uid=0, gid=0, nlink=1,
             mtime=0, data=RC.encode())
    entries.append(e)
    by_name[RC_PATH] = e
    added += len(e["data"])
    print("  add      %-56s %8d B" % (RC_PATH, len(e["data"])))
    print("  total added = %d B (%.2f MiB)" % (added, added / 1048576))

    # rebuild the manifests TWRP feeds to cpio to rebuild the live ramdisk
    real_files = [x["name"] for x in entries
                  if (x["mode"] & 0o170000) == 0o100000
                  and x["name"] not in ("ramdisk-files.txt", "ramdisk-files.sha256sum")]
    txt = ("\n".join(x["name"] for x in entries
                     if x["name"] not in ("ramdisk-files.txt", "ramdisk-files.sha256sum")) + "\n").encode()
    sha_lines = ["%s  ./%s" % (hashlib.sha256(by_name[n]["data"]).hexdigest(), n)
                 for n in sorted(real_files)]
    sha_lines.append("%s  ./ramdisk-files.txt" % hashlib.sha256(txt).hexdigest())
    sha = ("\n".join(sha_lines) + "\n").encode()
    by_name["ramdisk-files.txt"]["data"] = txt
    by_name["ramdisk-files.sha256sum"]["data"] = sha
    print("  manifests: txt=%d B, sha256sum=%d B" % (len(txt), len(sha)))

    cpio = write_cpio(entries)
    open(OUT_CPIO, "wb").write(cpio)
    comp = bootimg.compress_ramdisk(cpio, "lz4-legacy")
    open(OUT_RD, "wb").write(comp)
    frag = len(comp)
    print()
    print("cpio               = %10d B" % len(cpio))
    print("recovery 段 (lz4)  = %10d B (%.2f MiB)" % (frag, frag / 1048576))
    print("实测上限 %d -> 余量 %+d B (%.2f MiB)"
          % (FRAG_LIMIT, FRAG_LIMIT - frag, (FRAG_LIMIT - frag) / 1048576))

    chk = read_cpio(bootimg.decompress(comp)[0])
    bad = [x["name"] for x in chk
           if x["name"] in ("system/bin/init", "system/bin/recovery")
           and x["mode"] != 0o100755]
    print("mode sanity:", "OK" if not bad else ("BROKEN " + str(bad)))
    for n in ("vendor/bin/qseecomd", "vendor/lib64/libsecureui.so", RC_PATH,
              RECOVERY_FSTAB_PATH):
        e = [x for x in chk if x["name"] == n]
        print("  %-46s %s" % (n, oct(e[0]["mode"]) if e else "ABSENT"))

    if "--no-image" in sys.argv:
        return
    subprocess.run([sys.executable, os.path.join(PROJ, "tools/bootimg.py"),
                    "replace-ramdisk", BASE_IMG, OUT_RD, OUTIMG], check=True, cwd=PROJ)
    print("wrote %s (%d B)" % (OUTIMG, os.path.getsize(OUTIMG)))


if __name__ == "__main__":
    main()
