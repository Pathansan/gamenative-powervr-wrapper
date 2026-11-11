import os, io, tarfile, zstandard, capstone

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

# 1. Base pristine libvulkan_wrapper.so
so_path = os.path.join(os.environ['TEMP'], 'wrapper_gn', 'usr', 'lib', 'libvulkan_wrapper.so')
with open(so_path, 'rb') as f:
    data = bytearray(f.read())

# CANARY: Replace "Disabling VK_EXT_map_memory_placed\0" (36 bytes)
# with            "[PVR-FIX-V5] Disabling placed mem\0"  (36 bytes)
canary_target = b'Disabling VK_EXT_map_memory_placed'
canary_replacement = b'[PVR-FIX-V5] Disabling placed mem!'
assert len(canary_target) == len(canary_replacement) == 34
idx = data.find(canary_target)
assert idx != -1, "Canary target not found!"
data[idx : idx + 34] = canary_replacement
print(f"Patched canary string at file offset {hex(idx)}")

# Patch 1: Bypass internal AHB allocator in vkAllocateMemory (0xf4da8 -> 0xf4ea8)
bypass_addr = 0xf4da8
bypass_off = 0xd3c80 + (bypass_addr - 0xd7c80)
assert data[bypass_off : bypass_off + 4] == bytes.fromhex('08080836')
data[bypass_off : bypass_off + 4] = b_branch(bypass_addr, 0xf4ea8)

# Patch 2: Gateway to hook at 0xf4ed0 (br x4 -> b 0x281c00)
hook_addr = 0x281c00
hook_off = 0xd3c80 + (hook_addr - 0xd7c80)
br_off = 0xd3c80 + (0xf4ed0 - 0xd7c80)
assert data[br_off : br_off + 4] == bytes.fromhex('80001fd6')
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
assert data[inject_off : inject_off + 4] == bytes.fromhex('e81700f9')
data[inject_off : inject_off + 4] = bytes.fromhex('1f2003d5')

# Package into .wcp with HIGH versionCode (9999)
def make_wcp(ver_name, ver_code, desc, out_name):
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
        sinfo.size = len(data)
        sinfo.mode = 0o755
        tar.addfile(sinfo, io.BytesIO(data))

    cctx = zstandard.ZstdCompressor(level=10)
    wcp = cctx.compress(buf.getvalue())
    out_path = os.path.join(os.environ['TEMP'], out_name)
    with open(out_path, 'wb') as f:
        f.write(wcp)
    print(f'Wrote {out_path} ({len(wcp)} bytes)')
    return out_path

make_wcp('1.1-pvr-fix', 9999, 'PowerVR AHB Bypass Fix v5', 'wrapper-powervr-fix-v1.1-update.wcp')
make_wcp('1.1-pvr-fix', 9999, 'PowerVR AHB Bypass Fix v5', 'wrapper-powervr-fix.wcp')
make_wcp('1.5-pvr-fix', 9999, 'PowerVR AHB Bypass Fix v5', 'wrapper-powervr-fix-v5.wcp')
make_wcp('System', 9999, 'PowerVR AHB Bypass Fix v5', 'wrapper-powervr-fix-system.wcp')
