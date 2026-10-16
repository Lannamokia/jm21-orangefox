$(call inherit-product, $(SRC_TARGET_DIR)/product/aosp_base.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/generic_ramdisk.mk)
$(call inherit-product, device/fiio/jm21/device.mk)

PRODUCT_DEVICE       := jm21
PRODUCT_NAME         := twrp_jm21
PRODUCT_BRAND        := FiiO
PRODUCT_MODEL        := FiiO JM21
PRODUCT_MANUFACTURER := FiiO
PRODUCT_RELEASE_NAME := JM21

# The device ships Android 13 with a super partition holding system / vendor /
# product / odm / system_ext / vendor_dlkm / system_dlkm as logical partitions.
# This flag makes the build produce lpdump (required to read the super metadata)
# and enables logical-partition handling in TWRP - without it none of those
# partitions mount.
PRODUCT_USE_DYNAMIC_PARTITIONS := true
