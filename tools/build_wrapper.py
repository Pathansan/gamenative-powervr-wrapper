#!/usr/bin/env python3
"""
Automated ARM64 binary patcher and builder for GameNative libvulkan_wrapper.so.
Targeting Imagination PowerVR D-Series (Tensor G5 / Google Pixel 10 Pro XL).
"""

import os
import io
import sys
import tarfile
import zstandard
import capstone

def b_branch(from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm26 = diff & 0x03ffffff
    return (0x14000000 | imm26).to_bytes(4, 'little')

def b_cond(cond, from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm19 = diff & 0x0007ffff
    return (0x54000000 | (imm19 << 5) | cond).to_bytes(4, 'little')

def cbz(rt, from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm19 = diff & 0x0007ffff
    return (0xb4000000 | (imm19 << 5) | rt).to_bytes(4, 'little')

def patch_wrapper(input_path, output_path=None):
    with open(input_path, 'rb') as f:
        data = bytearray(f.read())

    # Patch 1: Bypass internal AHB allocator in vkAllocateMemory (0xf4da8 -> 0xf4ea8)
    bypass_addr = 0xf4da8
    bypass_off = 0xd3c80 + (bypass_addr - 0xd7c80)
    data[bypass_off : bypass_off + 4] = b_branch(bypass_addr, 0xf4ea8)

    # Patch 2: Gateway to hook at 0xf4ed0 (br x4 -> b 0x281c00)
    hook_addr = 0x281c00
    hook_off = 0xd3c80 + (hook_addr - 0xd7c80)
    br_off = 0xd3c80 + (0xf4ed0 - 0xd7c80)
    data[br_off : br_off + 4] = b_branch(0xf4ed0, hook_addr)

    # Patch 3: Hook at 0x281c00 (strips 0x3b9be342 VkExportMemoryAllocateInfo from pNext)
    hook_insns = [
        cbz(1, 0x281c00, 0x281c38),
        bytes.fromhex('28200091'),          # add x8, x1, #8
        bytes.fromhex('49689c52'),          # mov w9, #0xe342
        bytes.fromhex('6973a772'),          # movk w9, #0x3b9b, lsl #16
        bytes.fromhex('0a0140f9'),          # ldr x10, [x8]
        cbz(10, 0x281c14, 0x281c38),
        bytes.fromhex('4b0140b9'),          # ldr w11, [x10]
        bytes.fromhex('7f01096b'),          # cmp w11, w9
        b_cond(1, 0x281c20, 0x281c30),      # b.ne advance
        bytes.fromhex('4b0540f9'),          # ldr x11, [x10, #8]
        bytes.fromhex('0b0100f9'),          # str x11, [x8] (unlink)
        b_branch(0x281c2c, 0x281c10),       # b loop
        bytes.fromhex('48210091'),          # add x8, x10, #8 (advance)
        b_branch(0x281c34, 0x281c10),       # b loop
        bytes.fromhex('80001fd6'),          # br x4 (call native driver)
    ]
    hook_blob = b''.join(hook_insns)
    assert len(hook_blob) == 60
    data[hook_off : hook_off + len(hook_blob)] = hook_blob

    # Patch 4: Enable unconditional logging in wrapper (mov w0, #1; ret)
    log_addr = 0x1035d0
    log_off = 0xd3c80 + (log_addr - 0xd7c80)
    data[log_off : log_off + 8] = bytes.fromhex('20008052c0035fd6')

    # Patch 5: In 0xf54ec, NOP out `str x8, [sp, #0x28]`
    inject_off = 0xd3c80 + (0xf55bc - 0xd7c80)
    data[inject_off : inject_off + 4] = bytes.fromhex('1f2003d5')

    if output_path:
        with open(output_path, 'wb') as f:
            f.write(data)
        print(f"Saved patched library to {output_path}")

    return data

def build_wcp(so_data, ver_name, ver_code, desc, out_path):
    profile = f'''{{
  "type": "WRAPPER",
  "versionName": "{ver_name}",
  "versionCode": {ver_code},
  "description": "{desc}",
  "files": [
    {{
      "source": "libvulkan_wrapper.so",
      "target": "${{libdir}}/libvulkan_wrapper.so"
    }}
  ]
}}'''
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        pinfo = tarfile.TarInfo(name='profile.json')
        pinfo.size = len(profile.encode('utf-8'))
        pinfo.mode = 0o644
        tar.addfile(pinfo, io.BytesIO(profile.encode('utf-8')))

        sinfo = tarfile.TarInfo(name='libvulkan_wrapper.so')
        sinfo.size = len(so_data)
        sinfo.mode = 0o755
        tar.addfile(sinfo, io.BytesIO(so_data))

    cctx = zstandard.ZstdCompressor(level=10)
    wcp = cctx.compress(buf.getvalue())
    with open(out_path, 'wb') as f:
        f.write(wcp)
    print(f"Generated WCP: {out_path} ({len(wcp)} bytes)")

if __name__ == '__main__':
    base_so = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ['TEMP'], 'wrapper_gn', 'usr', 'lib', 'libvulkan_wrapper.so')
    patched = patch_wrapper(base_so)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else '.'
    build_wcp(patched, '1.1-pvr-fix', 9999, 'PowerVR AHB Bypass Fix v5', os.path.join(out_dir, 'wrapper-powervr-fix-v1.1.wcp'))
    build_wcp(patched, '1.5-pvr-fix', 9999, 'PowerVR AHB Bypass Fix v5', os.path.join(out_dir, 'wrapper-powervr-fix-v5.wcp'))
