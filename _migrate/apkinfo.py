#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时工具：从 APK 里解析 AndroidManifest 的 package / versionCode / versionName / minSdk。"""
import struct
import sys
import zipfile


def read_string_pool(data, off):
    typ, hsize, size = struct.unpack_from("<HHI", data, off)
    assert typ == 0x0001, "not a string pool: %#x" % typ
    count, style_count, flags, strings_start, styles_start = struct.unpack_from("<IIIII", data, off + 8)
    utf8 = bool(flags & (1 << 8))
    offsets = struct.unpack_from("<%dI" % count, data, off + hsize)
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        if utf8:
            n = data[p]
            p += 2 if n & 0x80 else 1
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 2
            else:
                p += 1
            out.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p:p + n * 2].decode("utf-16-le", "replace"))
    return out


def parse_axml(data):
    strings = []
    found = {}
    off = 8
    while off < len(data) - 8:
        typ, hsize, size = struct.unpack_from("<HHI", data, off)
        if size == 0:
            break
        if typ == 0x0001:
            strings = read_string_pool(data, off)
        elif typ == 0x0102:  # START_ELEMENT
            ns_i, name_i, attr_start, attr_size, attr_count = struct.unpack_from("<IIHHH", data, off + 16)
            if strings[name_i] in ("manifest", "uses-sdk"):
                attrs = {}
                p = off + 16 + attr_start
                for _ in range(attr_count):
                    a_ns, a_name, a_raw, t_size, t_res0, t_type, t_data = struct.unpack_from(
                        "<IIIHBBI", data, p)
                    nm = strings[a_name] if a_name < len(strings) else ""
                    if t_type == 0x03:                    # TYPE_STRING
                        attrs[nm] = strings[t_data]
                    elif t_type in (0x10, 0x11, 0x12):    # INT_DEC / INT_HEX / INT_BOOLEAN
                        attrs[nm] = t_data
                    elif a_raw != 0xFFFFFFFF:
                        attrs[nm] = strings[a_raw]
                    p += attr_size
                found.update(attrs)
        off += size
    return found


def inspect(path):
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise SystemExit("  ! zip 损坏：%s" % bad)
        if "AndroidManifest.xml" not in z.namelist():
            raise SystemExit("  ! 没有 AndroidManifest.xml")
        attrs = parse_axml(z.read("AndroidManifest.xml"))
    return attrs


if __name__ == "__main__":
    for p in sys.argv[1:]:
        a = inspect(p)
        print("%s\n  package=%s versionName=%s versionCode=%s minSdk=%s" % (
            p, a.get("package"), a.get("versionName"), a.get("versionCode"), a.get("minSdkVersion")))
