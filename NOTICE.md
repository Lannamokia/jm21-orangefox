# Copyright and licenses

This is a mixed-source device integration repository. It does not relicense its
upstreams or proprietary firmware.

- OrangeFox / TWRP recovery modifications are governed by their upstream
  GPL-3.0-or-later terms. See `bootable/recovery/NOTICE` at the pinned revision.
- AOSP build, vold, libbase, liblp and update_engine patches retain the licenses
  of their respective upstream files, predominantly Apache-2.0.
- Qualcomm init/fstab files retain the BSD notices embedded in those files.
- FiiO / Qualcomm binaries, stock images and artwork retain their original
  ownership and licensing. The prebuilt kernel is Linux 5.15.41 and is subject
  to the Linux kernel's GPL-2.0 terms; no kernel source build is claimed here.
- Fixed ramdisk inputs contain Android / OrangeFox software and fonts under
  their respective licenses. They are build inputs, not a blanket source grant.

The pinned manifest and patches identify the upstream sources used for the
rebuilt components. The fixed ramdisk is an explicit binary dependency; this
repository does not claim to rebuild every component contained in that base.
