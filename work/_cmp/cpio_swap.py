"""Swap one entry's data in v31.cpio and rebuild the TWRP manifests.

Used to inject the locally rebuilt, stderr-teeing libbase.so into the recovery
ramdisk without going through any extract/repack on the filesystem (which would
drop the Unix mode bits -- see cpio_edit.py).

Usage: python work/_cmp/cpio_swap.py <path-in-image> <host-file> [--inplace]
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(PROJ, "tools"))
sys.path.insert(0, HERE)
from cpio_edit import read_cpio, write_cpio                       # noqa: E402

CPIO = os.path.join(HERE, "v31.cpio")


def main():
    target, src = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    entries = read_cpio(open(CPIO, "rb").read())
    by_name = {e["name"]: e for e in entries}
    if target not in by_name:
        raise SystemExit("no such entry: %s" % target)
    e = by_name[target]
    print("  %s\n    %d -> %d B   mode=%s"
          % (target, len(e["data"]), len(data), oct(e["mode"])))
    print("    old md5 %s" % hashlib.md5(e["data"]).hexdigest())
    print("    new md5 %s" % hashlib.md5(data).hexdigest())
    e["data"] = data

    real_files = [x["name"] for x in entries
                  if (x["mode"] & 0o170000) == 0o100000
                  and x["name"] not in ("ramdisk-files.txt", "ramdisk-files.sha256sum")]
    txt = ("\n".join(x["name"] for x in entries
                     if x["name"] not in ("ramdisk-files.txt", "ramdisk-files.sha256sum")) + "\n").encode()
    sha_lines = ["%s  ./%s" % (hashlib.sha256(by_name[n]["data"]).hexdigest(), n)
                 for n in sorted(real_files)]
    sha_lines.append("%s  ./ramdisk-files.txt" % hashlib.sha256(txt).hexdigest())
    by_name["ramdisk-files.txt"]["data"] = txt
    by_name["ramdisk-files.sha256sum"]["data"] = ("\n".join(sha_lines) + "\n").encode()

    out = write_cpio(entries)
    open(CPIO, "wb").write(out)
    print("  wrote %s (%d B, %d entries)" % (CPIO, len(out), len(entries)))
    chk = {x["name"]: x for x in read_cpio(out)}
    for n in ("system/bin/init", "system/bin/recovery", "system/lib64/libbase.so"):
        print("  verify %-34s mode=%s md5=%s"
              % (n, oct(chk[n]["mode"]), hashlib.md5(chk[n]["data"]).hexdigest()))


if __name__ == "__main__":
    main()
