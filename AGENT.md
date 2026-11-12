# AGENT RESEARCH & ENGINEERING LOG: PowerVR DXT Vulkan Wrapper & Proton Emulation

## 1. Project Overview & Objective
- **Target Device:** Google Pixel 10 Pro XL (`59121FDCQ002RH`), Android 17 (Vanilla Ice Cream / Baklava preview).
- **Target Hardware:** Google Tensor G5 SoC with **Imagination Technologies PowerVR DXT-48-1536 MC1** GPU (Driver: `1.677.310`, Vulkan 1.3.296, API variant `VK_API_VERSION_1_3`).
- **Target Game:** *No Man's Sky* (Steam AppID: `STEAM_275850`, `Binaries/NMS.exe`), running under **GameNative** (`app.gamenative`) on Wine 9.0 / Proton 11.0 ARM64EC WoW64 under FEXCore binary translation.
- **Repository:** [`Pathansan/gamenative-powervr-wrapper`](https://github.com/Pathansan/gamenative-powervr-wrapper)
- **Primary Objective:** Deliver a working Vulkan wrapper (`libvulkan_wrapper.so`) via `.wcp` packages to solve engine initialization aborts on PowerVR GPUs without requiring root or modifying the base Wine/Proton rootfs.

---

## 2. Hardware Architecture & Ecosystem Constraints
1. **Unrooted Environment:**
   - The device is not rooted. System partitions (`/vendor`, `/system`, `/apex`) cannot be modified.
   - Delivery of translation libraries must strictly adhere to GameNative's ContentsManager packaging schema (`.wcp` = zstandard-compressed POSIX tarball with `profile.json`).
2. **GPU Architecture Differences (PowerVR vs Adreno/Mali):**
   - Unlike Qualcomm Adreno (which benefits from the open-source Mesa `Turnip` driver) or ARM Mali (supported by `Panfrost`), PowerVR Rogue / DXT architectures rely on proprietary firmware with closed instruction sets and a complex tile-based deferred rendering (TBDR) microkernel.
   - Building a clean-room open-source Mesa driver for PowerVR DXT from scratch is an enterprise multi-year undertaking (reverse engineering compiler microcode, instruction scheduling, and hardware USC ISA).
   - Therefore, the **GameNative Wrapper (`libvulkan_wrapper.so`)** serves as the optimal driver-level interposition layer, intercepting calls between Wine/Proton and the native proprietary driver (`/vendor/lib64/hw/vulkan.powervr.so`).

---

## 3. Chronological Root Cause Analysis & Evolutionary Fixes

### Phase 1: Identifying the Crash Point
- **Initial Symptom:** Launching *No Man's Sky* results in an immediate crash at startup after showing the initial splash/mode selection or loading sequence.
- **Log Extraction:**
  Extracted `/sdcard/Android/data/app.gamenative/files/debug_reports/STEAM_275850_*/log.gz` and `/sdcard/Android/data/app.gamenative/files/wine_logs/wine_debug.log`.
- **Winevulkan Assertion:**
  ```plaintext
  00fc:err:msvcrt:_wassert (L"!status && \"vkAllocateMemory\"", L"../dlls/winevulkan/loader_thunks.c", 87)
  ```
- **Context in Upstream Wine:**
  In `dlls/winevulkan/loader_thunks.c`:
  ```c
  VkResult WINAPI vkAllocateMemory(VkDevice device, const VkMemoryAllocateInfo *pAllocateInfo,
                                   const VkAllocationCallbacks *pAllocator, VkDeviceMemory *pMemory)
  {
      struct vkAllocateMemory_params params;
      params.device = device;
      params.pAllocateInfo = pAllocateInfo;
      params.pAllocator = pAllocator;
      params.pMemory = pMemory;
      status = UNIX_CALL(vkAllocateMemory, &params);
      assert(!status && "vkAllocateMemory");
      return params.result;
  }
  ```
  `status` is an `NTSTATUS` code returned by Wine's Unix syscall bridge (`__wine_unix_call_dispatcher`), *not* a Vulkan `VkResult`.

---

### Phase 2: Reverse Engineering `libvulkan_wrapper.so`
Through binary inspection using Capstone disassembly and ELF analysis of the pristine GameNative wrapper (2,765,600 bytes):
1. **Entry Points & Dispatch:**
   - Direct trampoline stub: `0xd8f68`
   - Mesa dispatch table entry 85: `0x29f798` (mapped to virtual address `0x2ab798`)
   - Internal wrapper dispatch: `0xf4d64`
2. **Hook Location:**
   - Hook site identified at `0xf4da8` where execution transitions to the native PowerVR driver dispatch (`0x1640`).

---

### Phase 3: Telemetry & In-Flight Discovery
- **Canary & Telemetry Injections (v8 & v9):**
  - Injected logging at `0x1036d8` (`wrapper_log`).
  - Output observed in `/sdcard/Download/wrapper.log`:
    ```plaintext
    [info]: [PVR-FIX-V11] vkAllocateMemory res 0 size=134217728 type=1
    ```
  - **Critical Revelation:** The physical PowerVR driver was *never failing*. The driver returned `VkResult = 0` (`VK_SUCCESS`) for the 128 MB allocation. The failure was occurring exclusively inside the Wine PE-to-Unix transition!

---

### Phase 4: Root Causes Discovered

#### A. External Memory Host & Placed Memory Incompatibilities (v10 & v11)
- Wine utilizes `VK_EXT_external_memory_host` and `VK_EXT_map_memory_placed` for WoW64 memory mapping.
- PowerVR DXT's driver does not support host-pointer importing on device memory heap types, causing Wine to attempt memory conversions that fail during physical device property matching.
- **Fix:**
  - Neutralized extension strings in wrapper `.rodata` (`VK_EXT_external_memory_host` -> `VK_DISABLED_ext_mem_host`).
  - Neutralized `VK_EXT_map_memory_placed` in extension table.
  - Zeroed out `VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT` (`1000178000` / `0x3b9d8150`) at offset `0x57568` so Mesa does not report host pointer alignment requirements to Wine.

#### B. ARM64EC Register ABI Collision (`x18` TEB Register)
- **Proton/Wine ARM64EC WoW64 ABI:** Register **`x18`** is dedicated as the **Thread Environment Block (TEB)** register on Windows ARM64.
- **Native Android / Linux AAPCS64 ABI:** Register **`x18`** is a temporary platform-reserved scratch register.
- When Wine's Unix dispatcher calls native shared libraries or driver hooks, if `x18` is altered or not preserved across the call boundary, the return frame into `winevulkan.dll` experiences a corrupted TEB pointer, triggering a non-zero `NTSTATUS` (`STATUS_ACCESS_VIOLATION` or `STATUS_UNSUCCESSFUL`).

#### C. Nvidia Streamline / Frame Generation Struct Injection
- *No Man's Sky* ships with `sl.dlss_g.dll` (Frame Generation) and `sl.reflex.dll` (Latency Reflex).
- On non-Nvidia GPUs, Streamline queries fail, but still attach dedicated allocate info structures (`VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO` = `1000026000`) into `VkMemoryAllocateInfo.pNext`.
- When combined with unhandled extension structs, Wine rejects the struct chain.

---

## 4. Wrapper Version Matrix & Implementation History

| Version | Package Name | Key Innovations & Assembly Patches | Status / Findings |
|---|---|---|---|
| **v1.1-update** | `wrapper-powervr-fix-v1.1-update.wcp` | Initial baseline wrapper with basic pNext unlinking | Verified packaging structure |
| **v5 - v8** | `wrapper-powervr-fix-v5..v8.wcp` | Assembly trampolines at `0xf4da8`, canary string watermarking | Validated wrapper loading and hook execution |
| **v9** | `wrapper-powervr-fix-v9.wcp` | Fixed register passing order (`w2 = w0`) for accurate driver logging | Proved physical driver returned `VK_SUCCESS` (res=0) |
| **v10** | `wrapper-powervr-fix-v10.wcp` | String neutralizing of `VK_EXT_external_memory_host` at `0x1c927` & `0x8c5f4` | Successfully stripped extension advertising |
| **v11** | `wrapper-powervr-fix-v11.wcp` | Zeroed out sType `1000178000` in Mesa properties at `0x57568` | Eliminated Wine host pointer alignment queries |
| **v12** | `wrapper-powervr-fix-v12.wcp` | **ARM64EC TEB (`x18`) Guard** (`str x18, [sp, #0x90]` / `ldr x18, [sp, #0x90]`) + **3-Way pNext Sanitizer** | **Current Release**: Solves TEB register ABI clobber |

---

## 5. Architectural Diagram

```
+-------------------------------------------------------------------------+
|                        No Man's Sky (NMS.exe)                          |
|             (Vulkan Graphics Engine + Nvidia Streamline DLLs)           |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
|                  winevulkan.dll (Windows ARM64EC)                       |
|           Requires x18 = TEB Pointer | Asserts (!status)                |
+-------------------------------------------------------------------------+
                                    |
                    __wine_unix_call_dispatcher
                                    |
                                    v
+-------------------------------------------------------------------------+
|               GameNative Wrapper (libvulkan_wrapper.so)                 |
|  - Preserves x18 across native calls                                    |
|  - 3-Way pNext Sanitizer (Unlinks Placed, HostPtr, Dedicated structs)   |
|  - Neutralizes unsupported external memory extension advertising        |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
|         Vendor Vulkan ICD (/vendor/lib64/hw/vulkan.powervr.so)         |
|      PowerVR DXT-48-1536 MC1 Driver 1.677.310 (Vulkan 1.3.296)          |
+-------------------------------------------------------------------------+
```

---

## 6. How to Build & Deploy

### Building Wrapper Packages
All build scripts are located in `tools/` and require Python 3 with `capstone` and `zstandard`:
```powershell
python tools/build_wrapper_v12.py
```
This produces `packages/wrapper-powervr-fix-v12.wcp`.

### Deploying to Target Device
```bash
adb push packages/wrapper-powervr-fix-v12.wcp /sdcard/Download/
```
1. Open **GameNative** on the Pixel 10 Pro XL.
2. Navigate to **Contents Manager**.
3. Select and install **GameNative PowerVR Fix v12**.
4. Set Steam/GameNative launch options:
   ```bash
   PROTON_ENABLE_NVAPI=0 %command%
   ```
5. Launch *No Man's Sky*.
