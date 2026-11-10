# GameNative & Winlator PowerVR Compatibility Layer

> **Target Platform:** Google Pixel 10 Pro XL (`mustang`, Android 17)  
> **GPU Architecture:** Google Tensor G5 / Imagination PowerVR D-Series DXT-48-1536 MC1  
> **Target Application:** No Man's Sky (Steam AppID `275850`, `Binaries/NMS.exe`) via GameNative / Winlator Bionic  

---

## Overview

This repository documents the architecture, root cause analysis, binary disassembly reconstruction, and automated packaging tools for running modern Windows Vulkan/DirectX games on **Google Tensor G5 (Imagination PowerVR D-Series DXT-48-1536)** using **GameNative** and **Wine/Proton ARM64EC**.

---

## Repository Contents

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): Complete execution stack, translation layers, and GameNative `.wcp` packaging lifecycle.
- [`docs/ROOT_CAUSE_ANALYSIS.md`](docs/ROOT_CAUSE_ANALYSIS.md): Comprehensive teardown of the Wine `loader_thunks.c:87` assertion (`!status && "vkAllocateMemory"`), DXVK Call #7 QueryPool allocation, and the PowerVR AHB Catch-22.
- [`docs/POWERVR_TENSOR_G5_KNOWLEDGE_BASE.md`](docs/POWERVR_TENSOR_G5_KNOWLEDGE_BASE.md): Hardware specifications, driver constraints, geometry shader emulation, placed memory (`VK_EXT_map_memory_placed`), and critical environment variables.
- [`docs/WRAPPER_DISASSEMBLY_RECONSTRUCTION.md`](docs/WRAPPER_DISASSEMBLY_RECONSTRUCTION.md): Exact disassembly maps, register states, and C pseudocode reconstruction of `libvulkan_wrapper.so`.
- [`tools/build_wrapper.py`](tools/build_wrapper.py): Automated Python patcher that modifies ARM64 instructions, injects `pNext` sanitization into RX padding, and generates `.wcp` packages.
- [`configs/No_Mans_Sky_PowerVR.json`](configs/No_Mans_Sky_PowerVR.json): Validated container configuration profile with `DISABLE_EXTERNAL_FD=1` and PowerVR-specific flags.

---

## The Core Fix Summary

```
                       Original Crash Flow:
DXVK QueryPool (Host-Visible) 
  --> libvulkan_wrapper.so 
  --> 0xf4da8 (Checks HOST_VISIBLE) 
  --> 0xf4ed4 (Diverts to AHB Allocator)
  --> 0xf5654 (vkGetMemoryAndroidHardwareBufferANDROID)
  --> PowerVR Rejects AHB on Host Memory (res != 0)
  --> Wine asserts: !status && "vkAllocateMemory" (loader_thunks.c:87)

                       Patched Flow:
DXVK QueryPool (Host-Visible)
  --> libvulkan_wrapper.so
  --> 0xf4da8 patched to "b 0xf4ea8" (Bypasses AHB Subsystem)
  --> 0xf4ed0 patched to "b 0x281c00" (Jumps to Hook Cave)
  --> 0x281c00 (Strips VkExportMemoryAllocateInfo 0x3b9be342 from pNext)
  --> Calls Native PowerVR Driver vkAllocateMemory (br x4)
  --> PowerVR Driver Allocates Memory Successfully (VK_SUCCESS = 0)
  --> DXVK Binds, Maps (vkMapMemory2KHR), and Compiles Shaders!
```

---

## Quick Start: Building & Packaging

```bash
# Generate patched .wcp packages
python tools/build_wrapper.py <path_to_pristine_libvulkan_wrapper.so> <output_dir>
```

This creates:
- `wrapper-powervr-fix-v1.1.wcp` (with `versionCode: 9999` to force-update existing containers)
- `wrapper-powervr-fix-v5.wcp` (with `versionCode: 9999`)
