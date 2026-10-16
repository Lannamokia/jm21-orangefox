#
# BoardConfig.mk -- FiiO JM21
#   platform : Qualcomm Khaje / SM6225, "bengal_515"  (device runs Android 13)
#   tree     : OrangeFox R12.0 (android-12.1 base)
#   every value below was measured on the real device (see project README)
#

# ---------------- architecture ----------------
TARGET_ARCH              := arm64
TARGET_ARCH_VARIANT      := armv8-a
TARGET_CPU_ABI           := arm64-v8a
TARGET_CPU_VARIANT       := cortex-a73
TARGET_2ND_ARCH          := arm
TARGET_2ND_ARCH_VARIANT  := armv8-a
TARGET_2ND_CPU_ABI       := armeabi-v7a
TARGET_2ND_CPU_VARIANT   := cortex-a53
TARGET_BOARD_PLATFORM    := bengal
TARGET_BOOTLOADER_BOARD_NAME := bengal_515
TARGET_USES_64_BIT_BINDER := true
TARGET_SUPPORTS_64_BIT_APPS := true

# ---------------- kernel ----------------
# Prebuilt GKI Image extracted from the stock boot_a partition (49,646,080 B,
# raw uncompressed arm64 Image). Nothing is built from source: the vendor
# modules in vendor_boot are linked against exactly this kernel.
TARGET_PREBUILT_KERNEL   := device/fiio/jm21/prebuilt/Image
BOARD_KERNEL_IMAGE_NAME  := Image
BOARD_KERNEL_PAGESIZE    := 4096
BOARD_BOOT_HEADER_VERSION := 4
BOARD_MKBOOTIMG_ARGS     += --header_version 4
# lz4 "-l" is the legacy framing the stock ramdisks use (magic 02 21 4c 18)
BOARD_RAMDISK_USE_LZ4    := true
# Stock recovery.img is RAMDISK-ONLY (kernel_size = 0): ABL takes the kernel
# from boot_<slot> and concatenates vendor_boot's vendor ramdisk below ours.
BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true

# ---------------- partitions (fastboot getvar all) ----------------
BOARD_BOOTIMAGE_PARTITION_SIZE        := 100663296     # 0x6000000
BOARD_RECOVERYIMAGE_PARTITION_SIZE    := 104857600     # 0x6400000
BOARD_DTBOIMG_PARTITION_SIZE          := 25165824      # 0x1800000
BOARD_INIT_BOOT_IMAGE_PARTITION_SIZE  := 8388608       # 0x800000
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := 100663296     # 0x6000000
BOARD_SUPER_PARTITION_SIZE            := 5371461632    # 0x1402A0000
BOARD_USES_METADATA_PARTITION         := true

# ---------------- partition output layout ----------------
# vendor is a real logical partition here (stock vendor fstab mounts it at
# /vendor via first_stage_mount), so lay it out under $(PRODUCT_OUT)/vendor
# instead of the default system/vendor.
#
# This is load-bearing, not cosmetic: board_config.mk derives
# BOARD_USES_VENDORIMAGE from TARGET_COPY_OUT_VENDOR, and rootdir's
# Android.mk then creates TARGET_ROOT_OUT/vendor as a directory instead of a
# "-> /system/vendor" symlink. With the symlink, the recovery ramdisk rsync
# cannot replace the non-empty recovery/root/vendor that soong has already
# filled with recovery VINTF manifests, and the build aborts.
# Stock recovery ramdisk confirms the shape: vendor/ is a directory while
# product/ and system_ext/ are symlinks, i.e. only vendor gets redirected.
TARGET_COPY_OUT_VENDOR := vendor

# Mount points rootdir does not create on its own: efs is a Qualcomm modem
# partition, system_dlkm an Android 13 logical partition. Stock recovery
# carries both as directories.
BOARD_ROOT_EXTRA_FOLDERS := efs system_dlkm

# ---------------- A/B ----------------
AB_OTA_UPDATER := true
BOARD_USES_RECOVERY_AS_BOOT := false
BOARD_MOVE_RECOVERY_RESOURCES_TO_VENDOR_BOOT := false
BOARD_BUILD_SYSTEM_ROOT_IMAGE := false

# ---------------- filesystems ----------------
TARGET_USERIMAGES_USE_EXT4 := true
TARGET_USERIMAGES_USE_F2FS := true

# ---------------- recovery fstab ----------------
TARGET_RECOVERY_FSTAB := device/fiio/jm21/recovery.fstab

# ---------------- display: 750x1334, panel0-backlight ----------------
DEVICE_RESOLUTION          := 750x1334
TW_THEME                   := portrait_hdpi
TW_BRIGHTNESS_PATH         := /sys/class/backlight/panel0-backlight/brightness
TW_MAX_BRIGHTNESS          := 255
TW_DEFAULT_BRIGHTNESS      := 128
TW_SCREEN_BLANK_ON_BOOT    := true
TARGET_RECOVERY_PIXEL_FORMAT := "RGBX_8888"

# MTP is enabled; the required configfs nodes (ffs.mtp, /dev/usb-ffs/mtp and the
# mtp/mtp,adb triggers) are provided by bootable/recovery/etc/init.rc.
# Do not use gr_fb_blank() to blank the panel. With it, blanktimer parks in
# kBlanked and the unblank path re-writes a stored brightness that the OrangeFox
# theme may have computed out of range, leaving the display dark. With this set,
# blanking is brightness-only and the state machine stays in kOff, which restores
# brightness properly on unblank.
TW_NO_SCREEN_BLANK := true

# ---------------- misc ----------------
TW_INCLUDE_FASTBOOTD  := true

# The whole point of this recovery is to decrypt /data, so the crypto block in
# bootable/recovery/Android.mk must be enabled. It is what defines
# TW_INCLUDE_FBE / USE_FSCRYPT / TW_INCLUDE_FBE_METADATA_DECRYPT (which is what
# makes  TWPartitionManager::Decrypt_Data()  call
# android::vold::fscrypt_mount_metadata_encrypted()), links libvold and
# libkeymint_support into the recovery binary, and adds keystore2,
# plat_keystore2_key_contexts, keystore_auth, vold_prepare_subdirs and
# fscryptpolicyget.recovery to the ramdisk.
#
# Without it the recovery binary contains no MetadataCrypt code at all -- the
# static linker never pulls MetadataCrypt.o out of libvold.a because nothing
# references fscrypt_mount_metadata_encrypted -- and /data silently stays
# undecryptable.
TW_INCLUDE_CRYPTO     := true

# Names this recovery accepts from an OTA package's 'pre-device'. Stock FiiO
# packages assert bengal_515 (the Qualcomm platform name); the device tree itself
# is jm21, so without both entries OrangeFox rejects every official full OTA.
FOX_TARGET_DEVICES := jm21,bengal_515

# Excludes exfat-fuse from the ramdisk (Android.mk gates it on this flag), which
# makes TWPartition::Mount() fall through to a plain mount(2) with the fstab
# fstype -- this kernel has a native exfat driver. The fuse daemon does not
# daemonize here, so Exec_Cmd() on it never returns and mounting hangs.
TW_NO_EXFAT_FUSE := true
TW_USE_TOOLBOX        := true
TW_NO_NTFS            := true

# The android variant of lpdump is a binder client for the lpdumpd service, and
# nothing registers that service in recovery (the ramdisk sepolicy gives
# u:r:recovery:s0 no permission to add it), so lpdumpd.rc would ship a daemon
# that never starts and lpdump itself blocks forever with no output. Excluding
# both keeps the ramdisk honest; liblp inside the recovery binary maps and
# mounts the logical partitions without either of them.
TW_EXCLUDE_LPDUMP     := true

# Read the battery percentage straight from /sys/class/power_supply/battery/
# capacity. Without this TWRP uses the health HAL, which is shipped "disabled"
# and never started in this ramdisk, so the background battery monitor blocks
# forever on android.hardware.health@2.0::IHealth/default and no percentage is
# ever shown. The sysfs node is present and valid on this device.
TW_USE_LEGACY_BATTERY_SERVICES := true

# Pull in gui/theme/extra-languages/ (which holds zh_CN.xml). Without it the
# copyTheme rule in libguitwrp_defaults.go only ships gui/theme/common/languages/.
# Like TW_THEME this travels through the twrpVarsPlugin soong namespace, so it must
# ALSO be exported into the build environment.
TW_EXTRA_LANGUAGES := true




# -------- A/B OTA partition list (required with AB_OTA_UPDATER) --------
AB_OTA_PARTITIONS += \
    boot \
    init_boot \
    vendor_boot \
    dtbo \
    recovery \
    vbmeta \
    vbmeta_system \
    system \
    system_ext \
    product \
    vendor \
    odm \
    vendor_dlkm \
    system_dlkm

# ---------------------------------------------------------------------------
# 关掉 OrangeFox 自带的 "reflash OrangeFox after flashing a ROM"：
# 我们在 recovery/root/system/bin/{pre,post}_rom_flash.sh 里已经做了
# 「刷前快照当前槽 recovery → 刷后写回被动过的槽」，功能重叠；而且 OF 自带的
# 那条依赖 IsRecoveryOverwritten() -> Find_Partition_By_Path("/recovery")，
# 本机分区列表里没有 /recovery 条目，它本来就是哑的。
# 这个开关（orangefox.mk:550 -> CFLAGS，data.cpp:881/1261）会同时：
#   * 隐藏设置/高级/安装页里所有相关 UI（主题里 5 处 fox_disable_reflash_current 条件）
#   * 强制 tw_auto_reflashtwrp=0，功能层面也彻底关掉
OF_NO_REFLASH_CURRENT_ORANGEFOX := 1
