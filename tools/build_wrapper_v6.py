import os, io, json, tarfile, zstandard, capstone

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

def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    so_path = os.path.join(os.environ['TEMP'], 'wrapper_gn', 'usr', 'lib', 'libvulkan_wrapper.so')
    
    with open(so_path, 'rb') as f:
        data = bytearray(f.read())

    # 1. Canary
    canary_target = b'Disabling VK_EXT_map_memory_placed'
    canary_replacement = b'[PVR-FIX-V6] Disabling placed mem!'
    assert len(canary_target) == len(canary_replacement) == 34
    idx = data.find(canary_target)
    assert idx != -1
    data[idx : idx + 34] = canary_replacement

    # 2. Inline hook at 0xf4da8
    hook_vaddr = 0xf4da8
    hook_file_off = hook_vaddr - 0x4000

    insns = [
        cbz(1, 0xf4da8, 0xf4df0),           # 0xf4da8: cbz x1, done
        bytes.fromhex('28200091'),          # 0xf4dac: add x8, x1, #8
        bytes.fromhex('49689c52'),          # 0xf4db0: mov w9, #0xe342
        bytes.fromhex('69738772'),          # 0xf4db4: movk w9, #0x3b9b, lsl #16
        bytes.fromhex('2a439752'),          # 0xf4db8: mov w10, #0xba19
        bytes.fromhex('8a73a772'),          # 0xf4dbc: movk w10, #0x3b9c, lsl #16
        bytes.fromhex('0b0140f9'),          # 0xf4dc0: ldr x11, [x8]
        cbz(11, 0xf4dc4, 0xf4df0),          # 0xf4dc4: cbz x11, done
        bytes.fromhex('6c0140b9'),          # 0xf4dc8: ldr w12, [x11]
        bytes.fromhex('9f01096b'),          # 0xf4dcc: cmp w12, w9
        b_cond(0, 0xf4dd0, 0xf4de4),        # 0xf4dd0: b.eq unlink
        bytes.fromhex('9f010a6b'),          # 0xf4dd4: cmp w12, w10
        b_cond(0, 0xf4dd8, 0xf4de4),        # 0xf4dd8: b.eq unlink
        bytes.fromhex('68210091'),          # 0xf4ddc: add x8, x11, #8
        b_branch(0xf4de0, 0xf4dc0),         # 0xf4de0: b loop
        bytes.fromhex('6d0540f9'),          # 0xf4de4: ldr x13, [x11, #8]
        bytes.fromhex('0d0100f9'),          # 0xf4de8: str x13, [x8]
        b_branch(0xf4dec, 0xf4dc0),         # 0xf4dec: b loop
        b_branch(0xf4df0, 0xf4ea8),         # 0xf4df0: b 0xf4ea8
    ]
    assembled = b''.join(insns)
    data[hook_file_off : hook_file_off + len(assembled)] = assembled
    nop = bytes.fromhex('1f2003d5')
    rem = (0xf4ea8 - 0xf4da8) - len(assembled)
    for i in range(rem // 4):
        data[hook_file_off + len(assembled) + i*4 : hook_file_off + len(assembled) + (i+1)*4] = nop

    # Verify 0xf4ed0 is br x4
    br_off = 0xf4ed0 - 0x4000
    assert data[br_off : br_off + 4] == bytes.fromhex('80001fd6')

    # EXACT GameNative ContentsManager WRAPPER schema:
    profile = {
        "type": "WRAPPER",
        "versionName": "1.1-pvr-fix-v6",
        "versionCode": 99999,
        "description": "GameNative PowerVR Fix v6 (Inline Sanitizer)",
        "files": [
            {
                "source": "libvulkan_wrapper.so",
                "target": "${libdir}/libvulkan_wrapper.so"
            }
        ]
    }
    profile_bytes = json.dumps(profile, indent=2).encode('utf-8')

    pkg_dir = os.path.join(repo_root, 'packages')
    os.makedirs(pkg_dir, exist_ok=True)
    out_wcp = os.path.join(pkg_dir, 'wrapper-powervr-fix-v6.wcp')

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode='w') as tf:
        ti = tarfile.TarInfo(name='profile.json')
        ti.size = len(profile_bytes)
        ti.mode = 0o644
        tf.addfile(ti, io.BytesIO(profile_bytes))

        ti = tarfile.TarInfo(name='libvulkan_wrapper.so')
        ti.size = len(data)
        ti.mode = 0o755
        tf.addfile(ti, io.BytesIO(data))

    tar_bytes = tar_buf.getvalue()
    cctx = zstandard.ZstdCompressor(level=19)
    compressed = cctx.compress(tar_bytes)

    with open(out_wcp, 'wb') as f:
        f.write(compressed)
    print(f"Successfully generated {out_wcp} ({len(compressed)} bytes) with verified profile.json schema.")

if __name__ == '__main__':
    main()
