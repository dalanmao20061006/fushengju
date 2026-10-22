#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧 .so 体检：判断它到底是 Android(Bionic) 还是 Termux(glibc)，能否被 App 加载。

用法：
    python scripts/check-so.py <文件或目录> [...]

输出：
    - 架构 / 位数 / OSABI
    - DT_NEEDED 依赖
    - 判定：Bionic / glibc / 未知
    - 是否带 OpenCL 迹象、是否被 strip
"""

import os
import struct
import sys

# Windows 控制台默认 GBK，强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

EM = {
    3: "x86",
    8: "MIPS",
    40: "ARM (32-bit)",
    62: "x86-64",
    183: "AArch64 (arm64)",
    243: "RISC-V",
}

OSABI = {
    0: "System V",
    3: "Linux",
    9: "FreeBSD",
}

DT = {
    1: "NEEDED",
    14: "SONAME",
    15: "RPATH",
    29: "RUNPATH",
}


def read_elf(path):
    with open(path, "rb") as f:
        data = f.read()

    if data[:4] != b"\x7fELF":
        return {"error": "不是 ELF 文件"}

    ei_class = data[4]      # 1=32, 2=64
    ei_data = data[5]       # 1=LE, 2=BE
    ei_osabi = data[7]
    endian = "<" if ei_data == 1 else ">"
    is64 = ei_class == 2

    if is64:
        e_type, e_machine = struct.unpack_from(endian + "HH", data, 16)
        e_shoff = struct.unpack_from(endian + "Q", data, 40)[0]
        e_shentsize, e_shnum = struct.unpack_from(endian + "HH", data, 58)
        sh_fmt = endian + "IIQQQQIIQQ"
        sh_size = 64
    else:
        e_type, e_machine = struct.unpack_from(endian + "HH", data, 16)
        e_shoff = struct.unpack_from(endian + "I", data, 32)[0]
        e_shentsize, e_shnum = struct.unpack_from(endian + "HH", data, 46)
        sh_fmt = endian + "IIIIIIIIII"
        sh_size = 40

    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + sh_size > len(data):
            break
        (
            sh_name, sh_type, sh_flags, sh_addr, sh_offset,
            sh_sz, sh_link, sh_info, sh_addralign, sh_entsize,
        ) = struct.unpack_from(sh_fmt, data, off)
        sections.append({
            "name_off": sh_name, "type": sh_type, "offset": sh_offset,
            "size": sh_sz, "link": sh_link, "entsize": sh_entsize,
        })

    # 字符串表（.shstrtab）
    shstr = b""
    if sections:
        s = sections[0]
        strtab_hdr = sections[s["link"]] if s["link"] < len(sections) else None
        # 找 .shstrtab 更可靠：用 e_shstrndx
        # e_shstrndx：ELF64 在偏移 62，ELF32 在偏移 50
        e_shstrndx = struct.unpack_from(endian + "H", data, 62 if is64 else 50)[0]
        if e_shstrndx < len(sections):
            st = sections[e_shstrndx]
            shstr = data[st["offset"]:st["offset"] + st["size"]]

    def name_of(sec):
        off = sec["name_off"]
        end = shstr.find(b"\x00", off)
        return shstr[off:end].decode("utf-8", "replace") if end > 0 else ""

    for sec in sections:
        sec["name"] = name_of(sec)

    need = []
    soname = None
    rpath = []

    dynamic = next((s for s in sections if s["name"] == ".dynamic"), None)
    dynstr = next((s for s in sections if s["name"] == ".dynstr"), None)
    if dynamic and dynstr:
        strtab = data[dynstr["offset"]:dynstr["offset"] + dynstr["size"]]
        ent = 16 if is64 else 8
        fmt = endian + ("qQ" if is64 else "iI")
        count = dynamic["size"] // ent
        for i in range(count):
            tag, val = struct.unpack_from(fmt, data, dynamic["offset"] + i * ent)
            if tag == 0:
                break
            if tag in (1, 14, 15, 29):
                end = strtab.find(b"\x00", val)
                s = strtab[val:end].decode("utf-8", "replace")
                if tag == 1:
                    need.append(s)
                elif tag == 14:
                    soname = s
                else:
                    rpath.append(s)

    has_symtab = any(s["name"] == ".symtab" for s in sections)
    strings = data

    return {
        "class": "ELF64" if is64 else "ELF32",
        "machine": EM.get(e_machine, f"未知({e_machine})"),
        "osabi": OSABI.get(ei_osabi, f"未知({ei_osabi})"),
        "e_type": {1: "REL", 2: "EXEC", 3: "DYN(PIE/SO)", 4: "CORE"}.get(e_type, str(e_type)),
        "needed": need,
        "soname": soname,
        "rpath": rpath,
        "stripped": not has_symtab,
        "opencl": (b"OpenCL" in strings) or (b"libOpenCL" in strings),
        "needs_libcxx": any("libc++_shared" in n for n in need),
        "size": len(data),
    }


def verdict(info):
    needed = info.get("needed", [])
    glibc = any(
        n.endswith("libc.so.6") or n.endswith("libm.so.6") or n.startswith("ld-linux")
        for n in needed
    )
    bionic = any(
        n in ("libc.so", "libm.so", "libdl.so", "liblog.so", "libandroid.so")
        for n in needed
    )
    if glibc:
        return "[FAIL] glibc（Termux / proot / 桌面 Linux 构建）—— Android App 无法 dlopen，只能当跑分参照"
    if bionic:
        if info.get("needs_libcxx"):
            return "[WARN] Bionic（Android），但依赖 libc++_shared.so —— 需要一起打包，漏了会加载失败"
        return "[OK] Bionic（Android）—— 技术上可加载；仍需与 JNI wrapper 的 ggml 版本成套"
    if not needed:
        return "[OK] 静态链接 / 无外部依赖 —— 通常可直接加载"
    return "[WARN] 无法判定（依赖列表为空或非常规）"


def main(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in names:
                    if n.endswith(".so") or ".so." in n:
                        files.append(os.path.join(root, n))
        else:
            files.append(p)

    if not files:
        print("没有找到 .so 文件")
        return 1

    for path in files:
        print("=" * 78)
        print(f"文件: {path}")
        try:
            info = read_elf(path)
        except Exception as e:  # noqa: BLE001
            print(f"  解析失败: {e}")
            continue
        if "error" in info:
            print(f"  {info['error']}")
            continue

        print(f"  类型    : {info['class']} / {info['e_type']}")
        print(f"  架构    : {info['machine']}")
        print(f"  OSABI   : {info['osabi']}")
        print(f"  体积    : {info['size'] / 1024:.1f} KB")
        print(f"  SONAME  : {info['soname'] or '-'}")
        print(f"  RPATH   : {', '.join(info['rpath']) or '-'}")
        print(f"  strip   : {'是（无符号表）' if info['stripped'] else '否'}")
        print(f"  OpenCL  : {'有迹象' if info['opencl'] else '无'}")
        print("  依赖    :")
        if info["needed"]:
            for n in info["needed"]:
                print(f"            - {n}")
        else:
            print("            （无）")
        print(f"  判定    : {verdict(info)}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
