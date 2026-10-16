#!/bin/bash
set -eo pipefail
cd "${1:?usage: build.sh ANDROID_SOURCE}"
export LC_ALL=C TZ=UTC
export USE_CCACHE=0
export TW_THEME=portrait_hdpi DEVICE_RESOLUTION=750x1334
# The verified v64 Soong configuration leaves these overrides empty and uses
# DEVICE_RESOLUTION plus runtime framebuffer dimensions.
unset TARGET_SCREEN_WIDTH TARGET_SCREEN_HEIGHT
export TARGET_RECOVERY_PIXEL_FORMAT=RGBX_8888 AB_OTA_UPDATER=true
export TW_MAX_BRIGHTNESS=255 TW_DEFAULT_BRIGHTNESS=128
export TW_NO_SCREEN_BLANK=true TW_EXTRA_LANGUAGES=true
mkdir -p out
printf 1792111963 > out/build_date.txt
set +e
source build/envsetup.sh
set -e
lunch twrp_jm21-eng
libbase="$PWD/out/soong/.intermediates/system/libbase/libbase/android_recovery_arm64_armv8-a_cortex-a73_shared/libbase.so"
m recoveryimage "$libbase" -j"${JOBS:-8}"
# recoveryimage links libbase statically; explicitly build and stage the
# recovery shared variant used by the fixed ramdisk's HAL processes.
mkdir -p out/target/product/jm21/recovery/root/system/lib64
cp "$libbase" out/target/product/jm21/recovery/root/system/lib64/libbase.so
