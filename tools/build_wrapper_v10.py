import os, io, json, struct, tarfile, zstandard, capstone

def b_branch(from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm26 = diff & 0x03ffffff
    return (0x14000000 | imm26).to_bytes(4, 'little')

def bl_call(from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm26 = diff & 0x03ffffff
    return (0x94000000 | imm26).to_bytes(4, 'little')

def b_cond(cond, from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm19 = diff & 0x0007ffff
    return (0x54000000 | (imm19 << 5) | cond).to_bytes(4, 'little')

def cbz(rt, from_addr, to_addr):
    diff = (to_addr - from_addr) // 4
    imm19 = diff & 0x0007ffff
    return (0xb4000000 | (imm19 << 5) | rt).to_bytes(4, 'little')

def adrp(rd, pc, target):
    pc_page = pc & ~0xfff
    target_page = target & ~0xfff
    diff = (target_page - pc_page) >> 12
    immlo = diff & 3
    immhi = (diff >> 2) & 0x7ffff
    return (0x90000000 | (immlo << 29) | (immhi << 5) | rd).to_bytes(4, 'little')

def add_imm(rd, rn, imm):
    return (0x91000000 | (imm << 10) | (rn << 5) | rd).to_bytes(4, 'little')

def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    so_path = os.path.join(os.environ['TEMP'], 'wrapper_gn', 'usr', 'lib', 'libvulkan_wrapper.so')
    
    with open(so_path, 'rb') as f:
        data = bytearray(f.read())
    print(f"Loaded pristine libvulkan_wrapper.so: {len(data)} bytes")

    # 1. CANARY WATERMARK
    canary_target = b'Disabling VK_EXT_map_memory_placed'
    canary_replacement = b'[PVR-FIX-V10] Placed & HostMem Off'
    assert len(canary_target) == len(canary_replacement) == 34
    idx = data.find(canary_target)
    assert idx != -1
    data[idx : idx + 34] = canary_replacement
    print(f"1. Embedded canary watermark: {canary_replacement.decode()}")

    # 2. LOGGING FORMAT STRING at 0x46608
    fmt_str = b'[PVR-FIX-V10] vkAllocateMemory res %d size=%llu type=%u\n\x00'
    fmt_off = 0x46608
    data[fmt_off : fmt_off + len(fmt_str)] = fmt_str
    print(f"2. Injected format string at vaddr {hex(fmt_off)}: {fmt_str.decode().strip()}")

    # 3. COMPLETE SANITIZING + ACCURATE LOGGING HOOK AT 0xf4da8
    fmt_vaddr = 0x46608
    info_vaddr = 0x6760
    log_func = 0x1036d8
    hook_vaddr = 0xf4da8
    hook_file_off = hook_vaddr - 0x4000

    insns = []
    def emit(b):
        global pc
        insns.append(b)
        pc += 4

    global pc
    pc = hook_vaddr

    # Step A: Prologue + Save incoming arguments on free stack space
    emit(bytes.fromhex('ff4302d1')) # sub sp, sp, #0x90
    emit(bytes.fromhex('fd7b43a9')) # stp x29, x30, [sp, #0x30]
    emit(bytes.fromhex('fc6f44a9')) # stp x28, x27, [sp, #0x40]
    emit(bytes.fromhex('fa6745a9')) # stp x26, x25, [sp, #0x50]
    emit(bytes.fromhex('f85f46a9')) # stp x24, x23, [sp, #0x60]
    emit(bytes.fromhex('f65747a9')) # stp x22, x21, [sp, #0x70]
    emit(bytes.fromhex('f44f48a9')) # stp x20, x19, [sp, #0x80]
    emit(bytes.fromhex('fd030091')) # mov x29, sp

    emit(bytes.fromhex('e00300f9')) # str x0, [sp]
    emit(bytes.fromhex('e10700f9')) # str x1, [sp, #8]
    emit(bytes.fromhex('e20b00f9')) # str x2, [sp, #0x10]
    emit(bytes.fromhex('e30f00f9')) # str x3, [sp, #0x18]

    # Step B: pNext sanitizer
    cbz_input_pc = pc
    emit(b'TEMP') # cbz x1, call_driver

    emit(bytes.fromhex('28200091')) # add x8, x1, #8
    emit(bytes.fromhex('49689c52')) # mov w9, #0xe342
    emit(bytes.fromhex('69738772')) # movk w9, #0x3b9b, lsl #16
    emit(bytes.fromhex('2a439752')) # mov w10, #0xba19
    emit(bytes.fromhex('8a73a772')) # movk w10, #0x3b9c, lsl #16

    loop_pc = pc
    emit(bytes.fromhex('0b0140f9')) # ldr x11, [x8]
    cbz_loop_pc = pc
    emit(b'TEMP') # cbz x11, call_driver
    emit(bytes.fromhex('6c0140b9')) # ldr w12, [x11]
    emit(bytes.fromhex('9f01096b')) # cmp w12, w9
    beq1_pc = pc
    emit(b'TEMP') # b.eq unlink
    emit(bytes.fromhex('9f010a6b')) # cmp w12, w10
    beq2_pc = pc
    emit(b'TEMP') # b.eq unlink
    emit(bytes.fromhex('68210091')) # add x8, x11, #8
    emit(b_branch(pc, loop_pc))     # b loop

    unlink_pc = pc
    emit(bytes.fromhex('6d0540f9')) # ldr x13, [x11, #8]
    emit(bytes.fromhex('0d0100f9')) # str x13, [x8]
    emit(b_branch(pc, loop_pc))     # b loop

    call_driver_pc = pc
    # Fixup forward jumps
    insns[12] = cbz(1, cbz_input_pc, call_driver_pc)
    insns[19] = cbz(11, cbz_loop_pc, call_driver_pc)
    insns[22] = b_cond(0, beq1_pc, unlink_pc)
    insns[24] = b_cond(0, beq2_pc, unlink_pc)

    # Step C: Load native dispatch and call driver
    emit(bytes.fromhex('e00340f9')) # ldr x0, [sp]
    emit(bytes.fromhex('e10740f9')) # ldr x1, [sp, #8]
    emit(bytes.fromhex('e20b40f9')) # ldr x2, [sp, #0x10]
    emit(bytes.fromhex('e30f40f9')) # ldr x3, [sp, #0x18]
    emit(bytes.fromhex('08bc4af9')) # ldr x8, [x0, #0x1578] (native device)
    emit(bytes.fromhex('04204bf9')) # ldr x4, [x0, #0x1640] (native vkAllocateMemory)
    emit(bytes.fromhex('e00308aa')) # mov x0, x8
    emit(bytes.fromhex('80003fd6')) # blr x4

    # w0 is driver VkResult! Save it in [sp, #0x20]
    emit(int(0xb90023e0).to_bytes(4, 'little')) # str w0, [sp, #0x20]

    # Step D: Log allocation details with ACCURATE w2 = w0!
    emit(bytes.fromhex('e203002a'))              # mov w2, w0 (ACCURATELY PASS DRIVER RESULT)
    emit(adrp(0, pc, fmt_vaddr))                 # adrp x0, fmt
    emit(add_imm(0, 0, fmt_vaddr & 0xfff))       # add x0, x0, fmt_off
    emit(adrp(1, pc, info_vaddr))                # adrp x1, info
    emit(add_imm(1, 1, info_vaddr & 0xfff))      # add x1, x1, info_off
    emit(bytes.fromhex('e80740f9'))              # ldr x8, [sp, #8] (pAllocateInfo)
    emit(bytes.fromhex('030940f9'))              # ldr x3, [x8, #0x10] (allocationSize)
    emit(int(0xb9401904).to_bytes(4, 'little'))  # ldr w4, [x8, #0x18] (memoryTypeIndex)
    emit(bl_call(pc, log_func))                  # bl wrapper_log

    # Step E: Restore driver result and epilogue
    emit(int(0xb94023e0).to_bytes(4, 'little'))  # ldr w0, [sp, #0x20]
    emit(bytes.fromhex('f44f48a9'))              # ldp x20, x19, [sp, #0x80]
    emit(bytes.fromhex('f65747a9'))              # ldp x22, x21, [sp, #0x70]
    emit(bytes.fromhex('f85f46a9'))              # ldp x24, x23, [sp, #0x60]
    emit(bytes.fromhex('fa6745a9'))              # ldp x26, x25, [sp, #0x50]
    emit(bytes.fromhex('fc6f44a9'))              # ldp x28, x27, [sp, #0x40]
    emit(bytes.fromhex('fd7b43a9'))              # ldp x29, x30, [sp, #0x30]
    emit(bytes.fromhex('ff430291'))              # add sp, sp, #0x90
    emit(bytes.fromhex('c0035fd6'))              # ret

    assembled = b''.join(insns)
    print(f"3. Assembled {len(insns)} instructions ({len(assembled)} bytes) into hook")

    # Write hook into binary at 0xf4da8
    data[hook_file_off : hook_file_off + len(assembled)] = assembled

    # 4. HOOK 0xd8f68 (Mesa direct dispatch trampoline stub)
    stub_file_off = 0xd8f68 - 0x4000
    data[stub_file_off : stub_file_off + 4] = b_branch(0xd8f68, hook_vaddr)
    print(f"4. Patched Mesa direct stub at 0xd8f68 -> b {hex(hook_vaddr)}")

    # 5. HOOK 0x29f798 (Mesa dispatch table entry 85)
    table_file_off = 0x29f798
    struct.pack_into('<Q', data, table_file_off, hook_vaddr)
    print(f"5. Patched Mesa dispatch table entry at {hex(table_file_off)} -> {hex(hook_vaddr)}")

    # 6. HOOK 0xf4d64 (Internal wrapper dispatch entry)
    internal_file_off = 0xf4d64 - 0x4000
    data[internal_file_off : internal_file_off + 4] = b_branch(0xf4d64, hook_vaddr)
    print(f"6. Patched wrapper internal entry at 0xf4d64 -> b {hex(hook_vaddr)}")

    # 7. STRIP VK_EXT_external_memory_host FROM EXTENSION ADVERTISING
    # Offset 0x8c5f4: VkExtensionProperties table entry
    # Offset 0x1c927: wrapper extension string table
    ext_target = b'VK_EXT_external_memory_host'
    ext_disabled = b'VK_DISABLED_ext_mem_host\x00\x00\x00'
    assert len(ext_target) == len(ext_disabled) == 27

    patches = [0x1c927, 0x8c5f4]
    for p in patches:
        assert data[p : p + 27] == ext_target, f"Mismatch at {hex(p)}: {data[p:p+27]}"
        data[p : p + 27] = ext_disabled
        print(f"7. Neutralized {ext_target.decode()} at offset {hex(p)} -> {ext_disabled.decode(errors='ignore')}")

    # 8. ALSO STRIP VK_EXT_map_memory_placed FROM EXTENSION TABLE AT 0x8e05c
    placed_target = b'VK_EXT_map_memory_placed'
    placed_disabled = b'VK_DISABLED_map_mem_plac'
    assert len(placed_target) == len(placed_disabled) == 24
    assert data[0x8e05c : 0x8e05c + 24] == placed_target
    data[0x8e05c : 0x8e05c + 24] = placed_disabled
    print(f"8. Neutralized {placed_target.decode()} at offset 0x8e05c -> {placed_disabled.decode()}")

    # Build package with verified GameNative schema
    profile = {
        "type": "WRAPPER",
        "versionName": "1.1-pvr-fix-v10",
        "versionCode": 99999999,
        "description": "GameNative PowerVR Fix v10 (No External Host Mem)",
        "files": [
            {
                "source": "libvulkan_wrapper.so",
                "target": "${libdir}/libvulkan_wrapper.so"
            }
        ]
    }
    profile_bytes = json.dumps(profile, indent=2).encode('utf-8')

    pkg_dir = os.path.join(repo_root, 'packages')
    out_wcp = os.path.join(pkg_dir, 'wrapper-powervr-fix-v10.wcp')

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
    print(f"\n9. Successfully built .wcp package: {out_wcp} ({len(compressed)} bytes)")

if __name__ == '__main__':
    main()
