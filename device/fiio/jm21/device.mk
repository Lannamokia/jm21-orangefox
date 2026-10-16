LOCAL_PATH := device/fiio/jm21

TARGET_NO_KERNEL            := false
TARGET_NO_RECOVERY          := false
TARGET_USES_64_BIT_BINDER   := true
TARGET_RECOVERY_FSTAB       := $(LOCAL_PATH)/recovery.fstab
TARGET_RECOVERY_UI_MARGIN_HEIGHT := 100

# Nothing extra is forced here on purpose: TWRP's own Android.mk adds the
# recovery packages it needs. Add device-specific ones as the build demands.
