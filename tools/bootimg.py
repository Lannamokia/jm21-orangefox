#!/usr/bin/env python3
"""
Android boot image toolkit for the FiiO JM21 (bengal_515 / khaje, GKI 5.15, header v4).

Parses, extracts and rebuilds:
  * boot / init_boot / recovery  (ANDROID! header v3/v4)
  * vendor_boot                  (VNDRBOOT header v3/v4, ramdisk fragment table)
  * vbmeta / AVB hash footer     (libavb 1.0 fixed + 1.1 length-prefixed descriptors)
  * dtbo                         (dt_table_header, big-endian)
  * ramdisks                     (gzip / lz4-legacy / lz4-frame / xz / zstd) -> cpio
  * image assembly               (cpio writer, boot image builder, ramdisk swap)

Usage:
  python bootimg.py info    <image> [image...]
  python bootimg.py unpack  <image> <outdir>
  python bootimg.py ramdisk <image> <outdir>              # only the ramdisk(s)

  python bootimg.py pack-cpio <dir> <out.ramdisk> [algo]
  python bootimg.py replace-ramdisk <stock.img> <ramdisk|dir> <out.img> \
        [--cmdline C] [--pad N] [--keep-avb]
  python bootimg.py build <out.img> (--kernel K | --no-kernel) [--ramdisk R] \
        [--cmdline C] [--header 4] [--algo lz4-legacy] [--pad N]
"""

import io
import lzma
import os
import struct
import sys
import zlib

BOOT_MAGIC = b"ANDROID!"
VNDR_MAGIC = b"VNDRBOOT"
DTB_MAGIC = 0xD7B7AB1E
PAGE = 4096

DT_TABLE_MAGIC = 0xD7B7AB1E


def align(n, a=PAGE):
    return (n + a - 1) // a * a


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def cstr(b, o, n):
    raw = b[o:o + n]
    return raw.split(b"\0")[0].decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# decompression
# --------------------------------------------------------------------------- #

def _lz4_legacy_decompress(data):
    import lz4.block
    out = bytearray()
    off = 0
    if data[:4] == b"\x02\x21\x4c\x18":
        off = 4
    while off + 4 <= len(data):
        (chunk,) = struct.unpack_from("<I", data, off)
        off += 4
        if chunk == 0:
            break
        blk = data[off:off + chunk]
        off += chunk
        out += lz4.block.decompress(blk, uncompressed_size=8 * 1024 * 1024)
    return bytes(out)


def decompress(data, label=""):
    """Return (plain_bytes, algorithm_name)."""
    if not data:
        return b"", "empty"
    if data[:2] == b"\x1f\x8b":
        return zlib.decompress(data, 16 + zlib.MAX_WBITS), "gzip"
    if data[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(data), "xz"
    if data[:4] == b"\x28\xb5\x2f\xfd":
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(data), "zstd"
        except ImportError:
            raise RuntimeError(f"{label}: zstd ramdisk but no 'zstandard' module")
    if data[:4] == b"\x04\x22\x4d\x18":
        import lz4.frame
        return lz4.frame.decompress(data), "lz4-frame"
    if data[:4] == b"\x02\x21\x4c\x18":
        return _lz4_legacy_decompress(data), "lz4-legacy"
    if data[:4] == b"\x89LZO":
        return data, "lzop(unsupported)"
    # Uncompressed cpio?
    if data[:6] == b"070701" or data[:6] == b"070702":
        return data, "none(cpio)"
    raise RuntimeError(f"{label}: unknown compression, magic={data[:8]!r}")


# --------------------------------------------------------------------------- #
# cpio
# --------------------------------------------------------------------------- #

def cpio_list(blob):
    """Yield (name, mode, size, data_offset) for a newc/crc cpio archive."""
    off = 0
    while off + 110 <= len(blob):
        if blob[off:off + 6] not in (b"070701", b"070702"):
            break
        f = lambda i: int(blob[off + 6 + i * 8: off + 6 + (i + 1) * 8], 16)
        mode, size, namesize = f(1), f(6), f(11)
        name_off = off + 110
        name = blob[name_off:name_off + namesize - 1].decode("utf-8", "replace")
        data_off = align(name_off + namesize, 4)
        if name == "TRAILER!!!":
            break
        yield name, mode, size, data_off
        off = align(data_off + size, 4)


def cpio_extract(blob, outdir, verbose=True):
    os.makedirs(outdir, exist_ok=True)
    n = 0
    links = []
    for name, mode, size, data_off in cpio_list(blob):
        rel = name.lstrip("/")
        if rel in ("", ".", ".."):
            continue
        target = os.path.join(outdir, rel)
        target = os.path.normpath(target)
        if not target.startswith(os.path.normpath(outdir)):
            print(f"  !! refusing path escape: {name}")
            continue
        ftype = mode & 0o170000
        if ftype == 0o040000:                      # directory
            os.makedirs(target, exist_ok=True)
        elif ftype == 0o120000:                    # symlink
            os.makedirs(os.path.dirname(target), exist_ok=True)
            link = blob[data_off:data_off + size].decode("utf-8", "replace")
            if os.path.lexists(target):
                os.remove(target)
            try:
                os.symlink(link, target)
            except OSError:
                links.append((target, link))
        elif ftype in (0o100000, 0):               # regular file / hardlink
            if size == 0:
                links.append((target, ""))
                continue
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(blob[data_off:data_off + size])
            os.chmod(target, mode & 0o7777)
        n += 1
    if verbose:
        print(f"  extracted {n} entries ({len(links)} empty/symlink stubs) -> {outdir}")


# --------------------------------------------------------------------------- #
# boot image (ANDROID!)
# --------------------------------------------------------------------------- #

def parse_boot(data):
    if data[:8] != BOOT_MAGIC:
        raise ValueError("not an ANDROID! image")
    info = {
        "kind": "boot",
        "kernel_size": u32(data, 8),
        "ramdisk_size": u32(data, 12),
        "os_version": u32(data, 16),
        "header_size": u32(data, 20),
        "header_version": u32(data, 40),
        "cmdline": cstr(data, 44, 1536),
    }
    osv = info["os_version"]
    info["android_release"] = f"{(osv >> 25) & 0x7f}.{(osv >> 18) & 0x7f}.{(osv >> 11) & 0x7f}"
    info["boot_patch_level"] = f"{osv & 0x7ff:04d}-{(osv >> 11) & 0xf:02d}"
    hv = info["header_version"]
    if hv >= 4:
        info["signature_size"] = u32(data, 1580)
    else:
        info["signature_size"] = 0

    off = align(info["header_size"])
    sections = []
    for name, size in (("kernel", info["kernel_size"]),
                       ("ramdisk", info["ramdisk_size"]),
                       ("boot_signature", info["signature_size"])):
        if size:
            sections.append((name, off, size))
        off = align(off + size)
    info["_sections"] = sections
    info["_data"] = data
    return info


# --------------------------------------------------------------------------- #
# vendor_boot (VNDRBOOT)
# --------------------------------------------------------------------------- #

def parse_vendor_boot(data):
    if data[:8] != VNDR_MAGIC:
        raise ValueError("not a VNDRBOOT image")
    info = {
        "kind": "vendor_boot",
        "header_version": u32(data, 8),
        "page_size": u32(data, 12),
        "kernel_addr": u32(data, 16),
        "ramdisk_addr": u32(data, 20),
        "vendor_ramdisk_size": u32(data, 24),
        "cmdline": cstr(data, 28, 2048),
        "tags_addr": u32(data, 2076),
        "name": cstr(data, 2080, 16),
        "header_size": u32(data, 2096),
        "dtb_size": u32(data, 2100),
        "dtb_addr": u64(data, 2104),
    }
    hv = info["header_version"]
    if hv >= 4:
        info["vendor_ramdisk_table_size"] = u32(data, 2112)
        info["vendor_ramdisk_table_entry_num"] = u32(data, 2116)
        info["vendor_ramdisk_table_entry_size"] = u32(data, 2120)
        info["bootconfig_size"] = u32(data, 2124)
    else:
        info.update(vendor_ramdisk_table_size=0, vendor_ramdisk_table_entry_num=0,
                    vendor_ramdisk_table_entry_size=0, bootconfig_size=0)

    page = info["page_size"]
    off = align(info["header_size"], page)
    sections = []
    for name, size in (("vendor_ramdisk", info["vendor_ramdisk_size"]),
                       ("dtb", info["dtb_size"]),
                       ("vendor_ramdisk_table", info["vendor_ramdisk_table_size"]),
                       ("bootconfig", info["bootconfig_size"])):
        if size:
            sections.append((name, off, size))
        off = align(off + size, page)
    info["_sections"] = sections
    info["_data"] = data
    return info


def vnd_ramdisk_table(info):
    """Decode the vendor ramdisk fragment table (v4)."""
    ent_size = info["vendor_ramdisk_table_entry_size"]
    num = info["vendor_ramdisk_table_entry_num"]
    if not ent_size or not num:
        return []
    base = None
    for name, off, size in info["_sections"]:
        if name == "vendor_ramdisk_table":
            base = off
            break
    if base is None:
        return []
    data = info["_data"]
    out = []
    TYPES = {0: "none", 1: "platform", 2: "recovery", 3: "dlkm"}
    for i in range(num):
        o = base + i * ent_size
        out.append({
            "size": u32(data, o),
            "offset": u32(data, o + 4),
            "type": TYPES.get(u32(data, o + 8), u32(data, o + 8)),
            "name": cstr(data, o + 12, 32),
        })
    return out


def parse_vbmeta(data):
    """AVB vbmeta image header + descriptors (all integers big-endian)."""
    def be32(o):
        return struct.unpack_from(">I", data, o)[0]

    def be64(o):
        return struct.unpack_from(">Q", data, o)[0]

    if data[:4] != b"AVB0":
        raise ValueError("not an AVB0 image")
    flags = be32(120)
    info = {
        "kind": "vbmeta",
        "required_major": be32(4),
        "required_minor": be32(8),
        "auth_block_size": be64(12),
        "aux_block_size": be64(20),
        "algorithm_type": be32(28),
        "hash_offset": be64(32), "hash_size": be64(40),
        "signature_offset": be64(48), "signature_size": be64(56),
        "public_key_offset": be64(64), "public_key_size": be64(72),
        "public_key_metadata_offset": be64(80), "public_key_metadata_size": be64(88),
        "descriptors_offset": be64(96), "descriptors_size": be64(104),
        "rollback_index": be64(112),
        "flags": flags,
        "rollback_index_location": be32(124),
        "release_string": cstr(data, 128, 47),
        "algorithms": {
            0: "NONE", 1: "SHA256_RSA2048", 2: "SHA256_RSA4096",
            3: "SHA256_RSA8192", 4: "SHA512_RSA2048",
            5: "SHA512_RSA4096", 6: "SHA512_RSA8192",
        }.get(be32(28), f"unknown({be32(28)})"),
        "verification_disabled": bool(flags & 2),
        "hashtree_disabled": bool(flags & 1),
        "descriptors": [],
    }

    # descriptors live in the auxiliary block
    base = 256 + info["auth_block_size"] + info["descriptors_offset"]
    end = base + info["descriptors_size"]
    TAGS = {0: "property", 1: "hashtree", 2: "hash", 3: "kernel_cmdline",
            4: "chain_partition", 5: "fdt"}

    def txt(off, n):
        return data[off:off + n].decode("utf-8", "replace")

    # libavb 1.0 uses fixed-size descriptor fields; 1.1 switched to
    # length-prefixed variable-size fields. Pick the layout from the
    # required_libavb_version the image was written for. This device is 1.0.
    v10 = info["required_minor"] == 0

    o = base
    while o + 16 <= end:
        tag = be64(o) & 0xFFFFFFFF
        n = be64(o + 8)
        if n == 0 or o + 16 + n > len(data):
            break
        d = {"tag": TAGS.get(tag, f"tag{tag:#x}")}
        p = o + 16
        if tag == 0:                          # AvbPropertyDescriptor
            klen = be64(p)
            d["key"] = txt(p + 16, klen).rstrip("\x00")
            # avbtool emits a NUL separator after the key; the value is the
            # last field in the descriptor, so read to the descriptor end.
            vstart = p + 16 + klen
            while vstart < o + 16 + n and data[vstart] == 0:
                vstart += 1
            raw = data[vstart:o + 16 + n].rstrip(b"\x00")
            if raw and all(32 <= c < 127 for c in raw):
                d["value"] = raw.decode("ascii")[:96]
            else:
                d["value_hex"] = raw.hex()[:96]
        elif tag == 4:                        # AvbChainPartitionDescriptor
            d["rollback_index_location"] = be32(p)
            nl = be32(p + 4)
            d["public_key_len"] = be32(p + 8)
            d["partition_name"] = txt(p + 76, nl).rstrip("\x00")   # reserved[64]
        elif tag == 2:                        # AvbHashDescriptor
            if v10:
                d["image_size"] = be64(p)
                d["hash_algorithm"] = txt(p + 8, 32).rstrip("\x00")
                plen = be32(p + 40)
                d["salt_len"] = be32(p + 44)
                d["digest_len"] = be32(p + 48)
                d["flags"] = be32(p + 52)
                d["partition_name"] = txt(p + 116, plen).rstrip("\x00")
            else:
                d["image_size"] = be64(p)
                alen, plen = be32(p + 8), be32(p + 12)
                d["hash_algorithm"] = txt(p + 36, alen)
                d["partition_name"] = txt(p + 36 + alen, plen)
        elif tag == 1:                        # AvbHashtreeDescriptor
            d["dm_verity_version"] = be32(p)
            d["image_size"] = be64(p + 4)
            d["tree_offset"] = be64(p + 12)
            d["tree_size"] = be64(p + 20)
            d["data_block_size"] = be32(p + 28)
            d["hash_block_size"] = be32(p + 32)
            d["fec_num_roots"] = be32(p + 36)
            d["fec_offset"] = be64(p + 40)
            d["fec_size"] = be64(p + 48)
            if v10:
                d["hash_algorithm"] = txt(p + 56, 32).rstrip("\x00")
                plen = be32(p + 88)
                d["salt_len"] = be32(p + 92)
                d["root_digest_len"] = be32(p + 96)
                d["flags"] = be32(p + 100)
                d["partition_name"] = txt(p + 164, plen).rstrip("\x00")
            else:
                alen, plen = be32(p + 56), be32(p + 60)
                d["hash_algorithm"] = txt(p + 84, alen)
                d["partition_name"] = txt(p + 84 + alen, plen)
        elif tag == 3:                        # AvbKernelCmdlineDescriptor
            d["flags"] = be32(p)
            clen = be32(p + 4)
            d["cmdline"] = txt(p + 8, clen)
        info["descriptors"].append(d)
        o += 16 + n
    return info


# --------------------------------------------------------------------------- #
# dtbo
# --------------------------------------------------------------------------- #

def parse_dtbo(data):
    """dt_table_header fields are stored big-endian (AOSP mkdtimg convention)."""
    def be(o):
        return struct.unpack_from(">I", data, o)[0]

    if be(0) != DT_TABLE_MAGIC:
        raise ValueError("not a dt_table image")
    info = {
        "kind": "dtbo",
        "total_size": be(4),
        "header_size": be(8),
        "dt_entry_size": be(12),
        "dt_entry_count": be(16),
        "dt_entries_offset": be(20),
        "page_size": be(24),
        "version": be(28),
        "entries": [],
    }
    for i in range(info["dt_entry_count"]):
        o = info["dt_entries_offset"] + i * info["dt_entry_size"]
        info["entries"].append({
            "dt_size": be(o),
            "dt_offset": be(o + 4),
            "id": be(o + 8),
            "rev": be(o + 12),
            "custom": [be(o + 16 + 4 * k) for k in range(4)],
        })
    info["_sections"] = [("dtbo_entry_%d" % i, e["dt_offset"], e["dt_size"])
                         for i, e in enumerate(info["entries"])]
    info["_data"] = data
    return info


def identify(data):
    if data[:8] == BOOT_MAGIC:
        return parse_boot(data)
    if data[:8] == VNDR_MAGIC:
        return parse_vendor_boot(data)
    if data[:4] == b"AVB0":
        return parse_vbmeta(data)
    if len(data) >= 4 and struct.unpack_from(">I", data, 0)[0] == DT_TABLE_MAGIC:
        return parse_dtbo(data)
    raise ValueError("unrecognised image")


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def human(n):
    if n is None:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0


# --------------------------------------------------------------------------- #
# packing: ramdisk compression / cpio writing / boot image building
# --------------------------------------------------------------------------- #

LZ4_LEGACY_MAGIC = b"\x02\x21\x4c\x18"
LZ4_BLOCK_MAX = 8 * 1024 * 1024


def compress_ramdisk(data, algo="lz4-legacy"):
    """Compress a cpio the way the stock FiiO images are compressed."""
    if algo == "none":
        return data
    if algo == "lz4-legacy":
        import lz4.block
        out = bytearray(LZ4_LEGACY_MAGIC)
        for i in range(0, len(data), LZ4_BLOCK_MAX):
            chunk = data[i:i + LZ4_BLOCK_MAX]
            comp = lz4.block.compress(chunk, mode="high_compression", store_size=False)
            out += struct.pack("<I", len(comp)) + comp
        return bytes(out)
    if algo == "gzip":
        import gzip as _gzip
        return _gzip.compress(data, 9)
    if algo == "xz":
        return lzma.compress(data)
    raise ValueError(f"unknown ramdisk compression {algo!r}")


def detect_algo(blob):
    """Which algorithm was used, so a repack can keep it identical."""
    if blob[:4] == LZ4_LEGACY_MAGIC:
        return "lz4-legacy"
    if blob[:4] == b"\x04\x22\x4d\x18":
        return "lz4-frame"
    if blob[:2] == b"\x1f\x8b":
        return "gzip"
    if blob[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if blob[:6] in (b"070701", b"070702"):
        return "none"
    return "unknown"


def _cpio_entry(name, mode, data, ino=1, nlink=1, mtime=0):
    """One 'newc' (070701) cpio entry.

    The header is the 6-byte magic followed by 13 8-hex-digit fields:
    ino, mode, uid, gid, nlink, mtime, filesize, devmajor, devminor,
    rdevmajor, rdevminor, namesize, check.
    """
    f = lambda v: b"%08X" % (v & 0xFFFFFFFF)
    body = name.encode("utf-8") + b"\0"
    hdr = b"070701"
    hdr += f(ino) + f(mode) + f(0) + f(0) + f(nlink) + f(mtime) + f(len(data))
    hdr += f(0) + f(0) + f(0) + f(0)
    hdr += f(len(body)) + f(0)
    assert len(hdr) == 110, len(hdr)
    out = hdr + body
    out += b"\0" * ((-len(out)) % 4)
    out += data
    out += b"\0" * ((-len(out)) % 4)
    return out


def cpio_pack(root, out, algo="lz4-legacy"):
    """Build a ramdisk from a directory tree. Device nodes are skipped."""
    entries = []
    ino = [0]

    def nxt():
        ino[0] += 1
        return ino[0]

    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for d in [""] + sorted(filenames):
            full = os.path.join(dirpath, d) if d else dirpath
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel == ".":
                rel = "."
            st = os.lstat(full)
            if os.path.isdir(full):
                entries.append(_cpio_entry(rel, 0o40755, b"", nxt(),
                                           nlink=2 + len(dirnames), mtime=int(st.st_mtime)))
            elif os.path.islink(full):
                entries.append(_cpio_entry(rel, 0o120777, os.readlink(full).encode(),
                                           nxt(), mtime=int(st.st_mtime)))
            elif os.path.isfile(full):
                with open(full, "rb") as fh:
                    data = fh.read()
                entries.append(_cpio_entry(rel, (st.st_mode & 0o7777) | 0o100000, data,
                                           nxt(), mtime=int(st.st_mtime)))
    blob = b"".join(entries)
    # TRAILER
    blob += _cpio_entry("TRAILER!!!", 0, b"", 0, nlink=1)
    blob += b"\0" * ((-len(blob)) % 512)
    comp = compress_ramdisk(blob, algo)
    with open(out, "wb") as fh:
        fh.write(comp)
    print(f"  cpio {human(len(blob))} -> {algo} {human(len(comp))}  ({out})")
    return comp


def build_boot_image(kernel=None, ramdisk=None, cmdline="", header_version=4,
                     page=4096, ramdisk_algo="lz4-legacy", signature=b"",
                     os_version=0x1a000172, img_version=0, pad_to=None):
    """Assemble a boot image (header v3/v4). kernel/ramdisk are raw bytes."""
    k = kernel or b""
    r = compress_ramdisk(ramdisk, ramdisk_algo) if ramdisk else b""
    sig = signature or b""
    hdr_size = 1584 if header_version >= 4 else 1580

    hdr = bytearray(hdr_size)
    hdr[0:8] = BOOT_MAGIC
    struct.pack_into("<I", hdr, 8, len(k))
    struct.pack_into("<I", hdr, 12, len(r))
    struct.pack_into("<I", hdr, 16, os_version)
    struct.pack_into("<I", hdr, 20, hdr_size)
    struct.pack_into("<I", hdr, 40, header_version)
    cb = cmdline.encode("utf-8")[:1536]
    hdr[44:44 + len(cb)] = cb
    if header_version >= 4:
        struct.pack_into("<I", hdr, 1580, len(sig))

    out = bytearray()
    for blob in (bytes(hdr), k, r, sig):
        if blob is bytes(hdr) or blob:
            out.extend(blob)
            out.extend(b"\0" * ((-len(out)) % page))
    if pad_to:
        if len(out) > pad_to:
            raise ValueError(f"image {len(out)} exceeds pad_to {pad_to}")
        out.extend(b"\0" * (pad_to - len(out)))
    return bytes(out)


def find_avb_trailer(src, content_end):
    """Locate the AVB trailer that avbtool appends after the image content.

    On this device a stock recovery image is laid out as:
        [0 .. content_end)          boot image (header + ramdisk) + page pad
        [al .. al+blob_len)         embedded vbmeta blob  ("AVB0", algorithm NONE)
        [footer_off .. +64)         AvbFooter             ("AVBf")
    where al == align(content_end, page). Returns (al, blob_len, footer_off)
    or None. footer_off may be None if no matching footer is present.
    """
    al = align(content_end)
    if src[al:al + 4] != b"AVB0":
        return None

    def be64(o):
        return struct.unpack_from(">Q", src, o)[0]

    blob_len = 256 + be64(al + 12) + be64(al + 20)

    footer_off = None
    o = al + blob_len
    while True:
        o = src.find(b"AVBf", o)
        if o < 0 or o + 64 > len(src):
            break
        orig, vboff, vbsize = be64(o + 12), be64(o + 20), be64(o + 28)
        if orig == al and vboff == al and vbsize == blob_len:
            footer_off = o
            break
        o += 1
    return (al, blob_len, footer_off)


def replace_ramdisk(src_img, new_ramdisk, out_img, cmdline=None, pad_to=None,
                    keep_avb=False):
    """Swap the ramdisk of an existing image, preserving its exact layout.

    This is the safe way to build a recovery image for this device: the stock
    header fields, page size, os_version, kernel and AVB trailer positions are
    carried over verbatim -- only the ramdisk bytes change.

    new_ramdisk may be a raw .cpio, or an already-compressed ramdisk (in which
    case its own algorithm is kept). A directory is packed with cpio_pack()
    using the same algorithm as the source image.

    keep_avb copies the source's AVB trailer across untouched. Only use that
    when the ramdisk is unchanged, because the digest then still matches;
    otherwise the trailer is dropped (see the note printed below).
    """
    src = open(src_img, "rb").read()
    info = identify(src)
    if info["kind"] != "boot":
        raise ValueError("source must be an ANDROID! image")

    kernel = b""
    old_rd = b""
    sig = b""
    content_end = info["header_size"]
    for name, off, size in info["_sections"]:
        if name == "kernel":
            kernel = src[off:off + size]
        elif name == "ramdisk":
            old_rd = src[off:off + size]
        elif name == "boot_signature":
            sig = src[off:off + size]
        content_end = max(content_end, off + size)

    trailer = find_avb_trailer(src, content_end)
    algo = detect_algo(old_rd) if old_rd else "lz4-legacy"

    if os.path.isdir(new_ramdisk):
        tmp = out_img + ".ramdisk"
        new_rd = cpio_pack(new_ramdisk, tmp, algo)
        os.remove(tmp)
    else:
        raw = open(new_ramdisk, "rb").read()
        # An already-compressed ramdisk is passed through untouched so the
        # caller's own compressor (or the AOSP build) is authoritative.
        if detect_algo(raw) in ("lz4-legacy", "lz4-frame", "gzip", "xz"):
            new_rd = raw
            algo = detect_algo(raw)
        else:
            new_rd = compress_ramdisk(raw, algo)

    cmd = info["cmdline"] if cmdline is None else cmdline
    hdr = bytearray(info["header_size"])
    hdr[0:8] = BOOT_MAGIC
    struct.pack_into("<I", hdr, 8, len(kernel))
    struct.pack_into("<I", hdr, 12, len(new_rd))
    struct.pack_into("<I", hdr, 16, info["os_version"])
    struct.pack_into("<I", hdr, 20, info["header_size"])
    struct.pack_into("<I", hdr, 40, info["header_version"])
    cb = cmd.encode("utf-8")[:1536]
    hdr[44:44 + len(cb)] = cb
    if info["header_version"] >= 4:
        struct.pack_into("<I", hdr, 1580, len(sig))

    out = bytearray()
    for blob in (bytes(hdr), kernel, new_rd, sig):
        if blob is bytes(hdr) or blob:
            out.extend(blob)
            out.extend(b"\0" * ((-len(out)) % PAGE))

    if trailer:
        al, blob_len, footer_off = trailer
        if keep_avb:
            while len(out) < al:
                out.append(0)
            out.extend(src[al:al + blob_len])
            if footer_off:
                while len(out) < footer_off:
                    out.append(0)
                out.extend(src[footer_off:footer_off + 64])
        else:
            print(f"    AVB trailer dropped (was blob@{al:,} {blob_len}B"
                  f"{'' if not footer_off else ', footer@%d' % footer_off})")
            print("      -> an unlocked bootloader does not enforce it. To rebuild it"
                  " properly use avbtool:")
            print("         avbtool add_hash_footer --image <img> --partition_size"
                  " 100663296 --partition_name recovery --algorithm NONE")

    if pad_to:
        if len(out) > pad_to:
            raise ValueError(f"image is {len(out)} B, exceeds pad_to {pad_to}")
        out.extend(b"\0" * (pad_to - len(out)))

    with open(out_img, "wb") as fh:
        fh.write(out)
    print(f"  wrote {out_img}")
    print(f"    header   v{info['header_version']}  os_version={info['os_version']:#010x}")
    print(f"    kernel   {human(len(kernel))}  (unchanged)")
    print(f"    ramdisk  {human(len(old_rd))} -> {human(len(new_rd))}  [{algo}]")
    print(f"    cmdline  {cmd!r}")
    print(f"    total    {human(len(out))}")
    return bytes(out)


def show(path):
    data = open(path, "rb").read()
    info = identify(data)
    print(f"\n=== {os.path.basename(path)}  ({human(len(data))}) ===")
    print(f"  kind            : {info['kind']}")
    print(f"  header_version  : {info.get('header_version')}")
    if info["kind"] == "dtbo":
        print(f"  entries         : {info['dt_entry_count']}  (entry_size={info['dt_entry_size']})")
        for i, e in enumerate(info["entries"]):
            print(f"    [{i:3d}] id={e['id']:#x} rev={e['rev']:#x} size={human(e['dt_size'])}"
                  f" off={e['dt_offset']}")
    elif info["kind"] == "vbmeta":
        print(f"  libavb required : {info['required_major']}.{info['required_minor']}")
        print(f"  avbtool         : {info['release_string']!r}")
        print(f"  algorithm       : {info['algorithms']}")
        print(f"  flags           : {info['flags']}"
              f"  verification_disabled={info['verification_disabled']}"
              f"  hashtree_disabled={info['hashtree_disabled']}")
        print(f"  rollback_index  : {info['rollback_index']}")
        print(f"  auth_block      : {human(info['auth_block_size'])}"
              f"  aux_block={human(info['aux_block_size'])}")
        print(f"  public_key      : off={info['public_key_offset']} size={human(info['public_key_size'])}")
        print(f"  descriptors     : {len(info['descriptors'])}"
              f"  ({human(info['descriptors_size'])})")
        for d in info["descriptors"]:
            extra = " ".join(f"{k}={v!r}" for k, v in d.items() if k != "tag")
            print(f"    - {d['tag']:16s} {extra}")
    else:
        if info["kind"] == "boot":
            print(f"  os_version      : {info['os_version']:#010x}  Android {info['android_release']}")
            print(f"  header_size     : {info['header_size']}")
            print(f"  ramdisk_size    : {human(info['ramdisk_size'])}")
            if info.get("signature_size"):
                print(f"  signature_size  : {human(info['signature_size'])}")
        else:
            print(f"  page_size       : {info['page_size']}")
            print(f"  header_size     : {info['header_size']}")
            print(f"  name            : {info['name']!r}")
            print(f"  vendor_ramdisk  : {human(info['vendor_ramdisk_size'])}")
            print(f"  dtb_size        : {human(info['dtb_size'])}")
            if info["header_version"] >= 4:
                print(f"  vnd_rd_table    : {info['vendor_ramdisk_table_entry_num']} entries"
                      f" x {info['vendor_ramdisk_table_entry_size']}B"
                      f" ({human(info['vendor_ramdisk_table_size'])})")
                print(f"  bootconfig_size : {human(info['bootconfig_size'])}")
                for e in vnd_ramdisk_table(info):
                    print(f"    - {e['name']:24s} type={e['type']:8s}"
                          f" size={human(e['size'])} off={e['offset']}")
        print(f"  kernel_size     : {human(info.get('kernel_size'))}")
        print(f"  cmdline         : {info['cmdline']!r}")
        print("  sections:")
        for name, off, size in info["_sections"]:
            print(f"    - {name:22s} off={off:#010x} size={human(size)}")


def unpack(path, outdir, only_ramdisk=False):
    data = open(path, "rb").read()
    info = identify(data)
    base = os.path.splitext(os.path.basename(path))[0]
    root = os.path.join(outdir, base)
    os.makedirs(root, exist_ok=True)
    print(f"\n=== unpack {os.path.basename(path)} -> {root} ===")

    if info["kind"] == "dtbo":
        d = os.path.join(root, "dtb")
        for name, off, size in info["_sections"]:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{name}.dtb"), "wb") as fh:
                fh.write(data[off:off + size])
            print(f"  wrote dtb/{name}.dtb ({human(size)})")
        with open(os.path.join(root, "dtbo_header.txt"), "w") as fh:
            for k, v in info.items():
                if k.startswith("_"):
                    continue
                fh.write(f"{k}: {v}\n")
        return

    if info["cmdline"]:
        with open(os.path.join(root, "cmdline.txt"), "w") as fh:
            fh.write(info["cmdline"] + "\n")

    # vendor_boot bootconfig is plain text
    for name, off, size in info["_sections"]:
        if name == "bootconfig":
            with open(os.path.join(root, "bootconfig.txt"), "wb") as fh:
                fh.write(data[off:off + size])
            print(f"  wrote bootconfig.txt ({human(size)})")

    vnd_frags = vnd_ramdisk_table(info) if info["kind"] == "vendor_boot" else []

    for name, off, size in info["_sections"]:
        blob = data[off:off + size]
        if name == "kernel":
            with open(os.path.join(root, "kernel"), "wb") as fh:
                fh.write(blob)
            print(f"  wrote kernel ({human(size)})")
        elif name == "dtb" or name.startswith("dtbo_entry"):
            with open(os.path.join(root, f"{name}.dtb"), "wb") as fh:
                fh.write(blob)
            print(f"  wrote {name}.dtb ({human(size)})")
        elif name.endswith("ramdisk"):
            plain, alg = decompress(blob, name)
            raw = os.path.join(root, f"{name}.cpio")
            with open(raw, "wb") as fh:
                fh.write(plain)
            print(f"  {name}: {alg}, {human(len(blob))} -> {human(len(plain))} cpio")
            if not only_ramdisk:
                cpio_extract(plain, os.path.join(root, f"{name}.d"))

    # For vendor_boot v4 the ramdisk is a concatenation of fragments; split them.
    if vnd_frags:
        sub = os.path.join(root, "vendor_ramdisk_fragments")
        os.makedirs(sub, exist_ok=True)
        print("  vendor ramdisk fragments:")
        for fr in vnd_frags:
            blob = data[fr["offset"]:fr["offset"] + fr["size"]]
            fn = os.path.join(sub, f"{fr['name']}.cpio")
            with open(fn, "wb") as fh:
                fh.write(blob)
            print(f"    - {fr['name']:24s} type={fr['type']:8s} {human(fr['size'])} -> {fn}")


def main():
    # Windows consoles default to a legacy code page; descriptor values and
    # partition names can contain bytes that are not representable there.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "info":
        for p in sys.argv[2:]:
            show(p)
    elif cmd == "unpack":
        if len(sys.argv) < 4:
            print("unpack needs <image> <outdir>")
            return 1
        for p in sys.argv[2:-1]:
            unpack(p, sys.argv[-1])
    elif cmd == "ramdisk":
        unpack(sys.argv[2], sys.argv[3], only_ramdisk=True)
    elif cmd == "pack-cpio":
        # pack-cpio <dir> <out.ramdisk> [algo]
        algo = sys.argv[4] if len(sys.argv) > 4 else "lz4-legacy"
        cpio_pack(sys.argv[2], sys.argv[3], algo)
    elif cmd == "replace-ramdisk":
        # replace-ramdisk <stock.img> <new.ramdisk|dir> <out.img>
        #                 [--cmdline C] [--pad N] [--keep-avb]
        if len(sys.argv) < 5:
            print("replace-ramdisk needs <stock.img> <ramdisk> <out.img>")
            return 1
        args = sys.argv[5:]
        opts = {}
        i = 0
        while i < len(args):
            if args[i] == "--keep-avb":
                opts["keep_avb"] = True
            elif args[i] in ("--cmdline", "--pad"):
                opts[args[i][2:]] = args[i + 1]
                i += 1
            i += 1
        replace_ramdisk(
            sys.argv[2], sys.argv[3], sys.argv[4],
            cmdline=opts.get("cmdline"),
            pad_to=int(opts["pad"], 0) if "pad" in opts else None,
            keep_avb=opts.get("keep_avb", False))
    elif cmd == "build":
        # build <out.img> --kernel K | --no-kernel --ramdisk R [--cmdline C] [--pad N]
        args = sys.argv[2:]
        out = args[0]
        opts = {}
        i = 1
        while i < len(args):
            a = args[i]
            if a == "--no-kernel":
                opts["no_kernel"] = True
            elif a.startswith("--"):
                opts[a[2:]] = args[i + 1]
                i += 1
            i += 1
        kernel = None
        if not opts.get("no_kernel"):
            kp = opts.get("kernel")
            if not kp:
                print("build: need --kernel <Image> or --no-kernel")
                return 1
            kernel = open(kp, "rb").read()
        ramdisk = open(opts["ramdisk"], "rb").read() if opts.get("ramdisk") else None
        img = build_boot_image(
            kernel=kernel, ramdisk=ramdisk, cmdline=opts.get("cmdline", ""),
            header_version=int(opts.get("header", "4")),
            ramdisk_algo=opts.get("algo", "lz4-legacy"),
            pad_to=int(opts["pad"], 0) if opts.get("pad") else None)
        with open(out, "wb") as fh:
            fh.write(img)
        print(f"  wrote {out}  ({human(len(img))})")
        show(out)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
