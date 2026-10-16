OrangeFox keeps /kernelsu.ko as a directory on purpose. Do not remove this file:
the directory only survives in the ramdisk because it is non-empty.

Why
---
On this device ABL builds the recovery initrd by concatenating three cpio
fragments, and the kernel's initramfs unpacker gives the last write to a path
the win (init/initramfs.c: clean_path() unlinks a same-named entry of a
different type, then a regular file is created with O_TRUNC).  The fragments
are, in order:

    [recovery ramdisk]  [vendor_boot ramdisk]  [init_boot generic ramdisk]

init_boot is applied last, so its /init (KernelSU's ksuinit shim, a regular
file) replaces this ramdisk's /init symlink.  PID 1 in recovery is therefore
ksuinit, which loads ../kernelsu.ko before any recovery code runs.  Loading
that module at that moment kills the boot: init never reaches the point where
it parses init.rc (verified with a USB-gadget-string probe -- the boot dies
before the earliest observable trigger, 'on fs'), and the device sits in a
reset loop with no USB at all.

This was isolated by rebuilding init_boot with one variable at a time:
    stock generic ramdisk                      -> boots
    stock + 0.48 MiB incompressible padding    -> boots   (size is irrelevant)
    stock + ksuinit as /init (no module)       -> boots
    stock + ksuinit as /init + kernelsu.ko     -> hangs   <-- the trigger
    full KernelSU image                        -> hangs

The fix
-------
clean_path() can only rmdir() a same-named directory, and filp_open(O_CREAT)
on a directory fails with EISDIR, after which do_name() silently skips the
entry.  So a *non-empty* directory at /kernelsu.ko cannot be displaced by
init_boot's regular file: the shim's open() fails, it reports "cannot load
kernelsu.ko" and execs /init.real exactly as it does when the module is
absent -- which boots the recovery normally.

This file exists to make the directory non-empty.  An empty directory would be
rmdir()'d and the module would land after all.

Nothing outside the recovery ramdisk is affected: KernelSU root in Android is
provided by init_boot, which is untouched, so root keeps working while the
recovery boots.
