"""Build the size-reduced crypto OrangeFox recovery image (v31) by editing the
cpio at the BYTE level.

Why byte level: this project runs on Windows. os.stat()/os.lstat() there do not
report the Unix permission bits, so any extract -> edit -> repack round trip
writes every entry as mode 0666. The kernels ramdisk unpacker then creates
/sbin/init and every other binary without the execute bit, so init cannot run
and the device sits in a boot loop. Every earlier attempt (v30, the padded
size probe, v31, the v32 control) failed exactly this way -- not because of
size and not because of content.

So: parse the original cpio, keep each entry's original mode/nlink/uid/gid,
and only change what we actually mean to change.

Base: the v28 crypto ramdisk (built with TW_INCLUDE_CRYPTO).
  * delete  sbin/bash, sbin/magiskboot (+ its system/bin symlink),
            libclang_rt.ubsan_standalone-aarch64-android.so,
            twres/fonts/Chococooky.ttf, libxml2.so, libssh.so
  * subset  twres/fonts/wqy-microhei.ttf -> GB2312 + ASCII + CJK punctuation
            + fullwidth + every character used by twres/languages/*.xml,
            same file name so zh_CN.xml's 28 fontoverride entries still resolve
  * install port/twrp.fstab.new as /system/etc/twrp.fstab
  * rebuild ramdisk-files.txt / ramdisk-files.sha256sum

update_engine_sideload is deliberately KEPT: OrangeFox needs it to flash an
official full OTA from inside recovery.

Usage: python work/_cmp/cpio_edit.py [--no-image]
"""
import hashlib
import io
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(PROJ, "tools"))
import bootimg                                                        # noqa: E402

CPIO = os.path.join(PROJ, "work/_cmp/v28_rd/v28-crypto-gzip/ramdisk.cpio")
BASE_IMG = os.path.join(PROJ, "work/_cmp/v28-crypto-gzip.img")
TWRP_FSTAB = os.path.join(PROJ, "port/twrp.fstab.new")
OUT_CPIO = os.path.join(PROJ, "work/_cmp/v31.cpio")
OUT_RD = os.path.join(PROJ, "work/_cmp/v31_crypto.ramdisk")
OUTIMG = os.path.join(PROJ, "work/v31-crypto-lz4.img")

FONT = "twres/fonts/wqy-microhei.ttf"

CROP = [
    "sbin/bash",
    "sbin/magiskboot",
    "system/bin/magiskboot",
    "system/lib64/libclang_rt.ubsan_standalone-aarch64-android.so",
    "twres/fonts/Chococooky.ttf",
    "system/lib64/libxml2.so",
    "system/lib64/libssh.so",
]

VENDOR_RD = 7340032
INIT_BOOT_RD = 2496676
KNOWN_GOOD_FRAG = 26057856
FORMULA_LIMIT = 37216256


def read_cpio(blob):
    """Yield dicts with the raw header fields plus the entry data."""
    out = []
    off = 0
    while off + 110 <= len(blob):
        if blob[off:off + 6] not in (b"070701", b"070702"):
            break
        raw = [int(blob[off + 6 + i * 8: off + 6 + i * 8 + 8], 16) for i in range(13)]
        namesz = raw[11]
        name = blob[off + 110: off + 110 + namesz - 1].decode("utf-8", "replace")
        d0 = (off + 110 + namesz + 3) & ~3
        data = blob[d0:d0 + raw[6]]
        out.append(dict(name=name, ino=raw[0], mode=raw[1], uid=raw[2], gid=raw[3],
                        nlink=raw[4], mtime=raw[5], data=data))
        off = (d0 + raw[6] + 3) & ~3
        # NOTE: do NOT stop at "TRAILER!!!". The kernel happily processes another
        # cpio archive after a trailer, and the OrangeFox build output really does
        # contain entries past a trailer; breaking here silently dropped them.
    return out


def write_entry(e):
    f = lambda v: b"%08X" % (v & 0xFFFFFFFF)
    body = e["name"].encode("utf-8") + b"\0"
    hdr = b"070701"
    hdr += f(e["ino"]) + f(e["mode"]) + f(e["uid"]) + f(e["gid"]) + f(e["nlink"])
    hdr += f(e["mtime"]) + f(len(e["data"]))
    hdr += f(0) + f(0) + f(0) + f(0)
    hdr += f(len(body)) + f(0)
    out = hdr + body
    out += b"\0" * ((-len(out)) % 4)
    out += e["data"]
    out += b"\0" * ((-len(out)) % 4)
    return out


def write_cpio(entries):
    blob = b"".join(write_entry(e) for e in entries)
    blob += write_entry(dict(name="TRAILER!!!", ino=0, mode=0, uid=0, gid=0,
                             nlink=1, mtime=0, data=b""))
    blob += b"\0" * ((-len(blob)) % 512)
    return blob


def pin_font_head(blob):
    """Zero the three head-table bytes fontTools leaves non-deterministic.

    Setting Options.recalc_timestamp = False and head.created/modified = 0 is
    not enough on this fontTools version: it re-stamps head.modified while
    saving, and checkSumAdjustment then follows it, so two builds from identical
    inputs produced a 3-byte difference -- which propagated into
    ramdisk-files.sha256sum and hence into the image md5.  None of the three
    fields is used for rendering, and the table directory checksum is recomputed
    so the font stays self-consistent.
    """
    import struct
    b = bytearray(blob)
    if len(b) < 12:
        return bytes(b)
    num, = struct.unpack_from(">H", b, 4)
    for i in range(num):
        o = 12 + i * 16
        if bytes(b[o:o + 4]) != b"head":
            continue
        head_off, hlen = struct.unpack_from(">II", b, o + 8)
        for rel, size in ((8, 4), (20, 8), (28, 8)):     # checkSumAdjustment, created, modified
            b[head_off + rel:head_off + rel + size] = b"\0" * size
        words = (hlen + 3) // 4
        csum = sum(struct.unpack_from(">%dI" % words, b, head_off)) & 0xFFFFFFFF
        struct.pack_into(">I", b, o + 4, csum)
        break
    return bytes(b)


def font_subset_bytes(src, xmls):
    from fontTools.ttLib import TTFont
    from fontTools import subset

    cps = set()
    for b1 in range(0xA1, 0xF8):
        for b2 in range(0xA1, 0xFF):
            try:
                cps.add(ord(bytes([b1, b2]).decode("gb2312")))
            except Exception:
                pass
    cps |= set(range(0x20, 0x7F))
    cps |= set(range(0x3000, 0x3040))
    cps |= set(range(0xFF00, 0xFFF0))
    cps |= {0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026,
            0x2190, 0x2192, 0x2191, 0x2193, 0x25A0, 0x25CF, 0x25CB, 0x2713}
    for b in xmls:
        cps |= {ord(c) for c in b.decode("utf-8", "replace")}

    font = TTFont(io.BytesIO(src), fontNumber=0)
    o = subset.Options()
    o.drop_tables += ["DSIG"]
    o.recalc_timestamp = False
    s = subset.Subsetter(options=o)
    s.populate(unicodes=sorted(cps))
    s.subset(font)
    font["head"].created = 0
    font["head"].modified = 0
    buf = io.BytesIO()
    font.save(buf)
    font.close()
    return pin_font_head(buf.getvalue()), len(cps)


def main():
    entries = read_cpio(open(CPIO, "rb").read())
    print("read %d entries from %s" % (len(entries), os.path.basename(CPIO)))
    if not any(e["name"] == "system/bin/init" and e["mode"] == 0o100755 for e in entries):
        raise SystemExit("sanity: system/bin/init is not 0755 in the source cpio")

    by_name = {e["name"]: e for e in entries}
    xmls = [by_name[n]["data"] for n in
            ("twres/languages/zh_CN.xml", "twres/languages/en.xml") if n in by_name]

    drop = set()
    for rel in CROP:
        if rel in by_name:
            drop.add(rel)
            print("  removed  %-58s %9d B  mode=%s" % (rel, len(by_name[rel]["data"]),
                                                       oct(by_name[rel]["mode"])))
        else:
            print("  MISSING  %s" % rel)

    sub, ncp = font_subset_bytes(by_name[FONT]["data"], xmls)
    print("  subset   %-58s %9d -> %d B  (%d codepoints requested)"
          % (FONT, len(by_name[FONT]["data"]), len(sub), ncp))
    by_name[FONT]["data"] = sub

    fstab = open(TWRP_FSTAB, "rb").read()
    if "system/etc/twrp.fstab" in by_name:
        by_name["system/etc/twrp.fstab"]["data"] = fstab
    else:
        base = by_name["system/etc/recovery.fstab"]
        entries.append(dict(name="system/etc/twrp.fstab", ino=base["ino"], mode=0o100644,
                            uid=base["uid"], gid=base["gid"], nlink=1,
                            mtime=base["mtime"], data=fstab))
        by_name["system/etc/twrp.fstab"] = entries[-1]
    print("  installed system/etc/twrp.fstab (%d B)" % len(fstab))

    entries = [e for e in entries if e["name"] not in drop]

    # rebuild the two manifests TWRP feeds to cpio to rebuild the live ramdisk
    real_files = [e["name"] for e in entries
                  if (e["mode"] & 0o170000) == 0o100000
                  and e["name"] not in ("ramdisk-files.txt", "ramdisk-files.sha256sum")]
    txt = ("\n".join(e["name"] for e in entries
                     if e["name"] not in ("ramdisk-files.txt", "ramdisk-files.sha256sum")) + "\n").encode()
    sha_lines = []
    for n in sorted(real_files):
        d = by_name[n]["data"]
        sha_lines.append("%s  ./%s" % (hashlib.sha256(d).hexdigest(), n))
    sha_lines.append("%s  ./ramdisk-files.txt" % hashlib.sha256(txt).hexdigest())
    sha = ("\n".join(sha_lines) + "\n").encode()
    by_name["ramdisk-files.txt"]["data"] = txt
    by_name["ramdisk-files.sha256sum"]["data"] = sha
    print("  rebuilt manifests: txt=%d B, sha256sum=%d B (%d files)"
          % (len(txt), len(sha), len(real_files) + 1))

    cpio = write_cpio(entries)
    open(OUT_CPIO, "wb").write(cpio)
    comp = bootimg.compress_ramdisk(cpio, "lz4-legacy")
    open(OUT_RD, "wb").write(comp)
    frag = len(comp)
    print()
    print("cpio               = %10d B" % len(cpio))
    print("recovery 段 (lz4)  = %10d B (%.2f MiB)" % (frag, frag / 1048576))
    print("已知能启动的最大段 = %10d B  -> 余量 %+d B" % (KNOWN_GOOD_FRAG, KNOWN_GOOD_FRAG - frag))
    print("S = 段 + vendor + init_boot = %d  (公式上限 %d, 余量 %+d)"
          % (frag + VENDOR_RD + INIT_BOOT_RD, FORMULA_LIMIT,
             FORMULA_LIMIT - frag - VENDOR_RD - INIT_BOOT_RD))

    if "--no-image" in sys.argv:
        return
    subprocess.run([sys.executable, os.path.join(PROJ, "tools/bootimg.py"),
                    "replace-ramdisk", BASE_IMG, OUT_RD, OUTIMG], check=True, cwd=PROJ)
    d = open(OUTIMG, "rb").read(0x40)
    ks, rs, osv, hs = struct.unpack_from("<IIII", d, 8)
    print("wrote %s (%d B, header ramdisk_size=%d, signature_size=%d)"
          % (OUTIMG, os.path.getsize(OUTIMG), rs, hs))
    # sanity: the modes survived
    chk = read_cpio(comp and bootimg.decompress(comp)[0])
    bad = [e["name"] for e in chk
           if e["name"] in ("system/bin/init", "system/bin/recovery", "system/bin/sh")
           and e["mode"] != 0o100755]
    print("mode sanity on init/recovery/sh:", "OK" if not bad else ("BROKEN " + str(bad)))


if __name__ == "__main__":
    main()
