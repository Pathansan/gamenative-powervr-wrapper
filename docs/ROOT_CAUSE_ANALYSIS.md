# Detailed Root Cause Analysis: Vulkan Memory Allocation Crash

## 1. The Symptom

During the startup of *No Man's Sky* on the Google Pixel 10 Pro XL, Wine terminates execution immediately with:

```text
00f8:err:msvcrt:_wassert (L"!status && \"vkAllocateMemory\"", L"../dlls/winevulkan/loader_thunks.c", 87)
```

Wine's internal Vulkan loader thunks enforce strict validation:
```c
VkResult result = thunk64_vkAllocateMemory(device, pAllocateInfo, pAllocator, pMemory);
assert(!result && "vkAllocateMemory");
```
Any non-zero `VkResult` returned by the underlying driver or wrapper triggers an unrecoverable process assertion abort.

---

## 2. Allocation Sequence & Call #7 Analysis

By monitoring `thunk64_vkAllocateMemory` calls in logcat:

| Call Index | Target Object | Memory Type Properties | sType / pNext | Result in Baseline |
| :--- | :--- | :--- | :--- | :--- |
| **Call #1-3** | WSI Swapchain Images (1280x720) | Device Local (`0x0001`) | `VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID` | `VK_SUCCESS` (0) |
| **Call #4-6** | Staging Buffers / Textures | Device Local (`0x0001`) | `NULL` | `VK_SUCCESS` (0) |
| **Call #7** | DXVK QueryPool Buffer | Host Visible + Coherent (`0x0006`) | `VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO` (`0x3b9be342`) | **FAILED** (`VK_ERROR_...`) |

---

## 3. Disassembly of `libvulkan_wrapper.so`

### The Entry Dispatch (`0xf4d64` - `0xf4ea8`)
```arm64
0xf4d94: ldr   x8, [x0, #0x1608]
0xf4d98: ldr   w9, [x1, #0x18]            ; memoryTypeIndex
0xf4d9c: add   x8, x8, x9, lsl #3
0xf4da0: mov   w9, #0x185c
0xf4da4: ldrb  w8, [x8, x9]               ; load propertyFlags
0xf4da8: tbz   w8, #1, #0xf4ea8           ; Check bit 1 (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)
                                          ; If bit 1 == 0 (Device Local) -> JUMP to 0xf4ea8 (Direct Passthrough)
; Host Visible Memory path:
0xf4dac: ldrb  w8, [x0, #0x370]
0xf4db0: cmp   w8, #1
0xf4db4: b.ne  #0xf4ea8
0xf4db8: ldrb  w8, [x0, #0x11d]
0xf4dbc: cmp   w8, #1
0xf4dc0: b.ne  #0xf4ea8
...
0xf4e7c: mov   w1, #0xe342
0xf4e88: movk  w1, #0x3b9b, lsl #16       ; w1 = 0x3b9be342 (VkExportMemoryAllocateInfo)
0xf4e8c: bl    #0xf534c                   ; Search pNext chain
0xf4ea4: cbz   x8, #0xf4ed4               ; IF NOT FOUND -> Branch to 0xf4ed4 (AHB WRAPPER)
                                          ; IF FOUND -> Fallthrough to 0xf4ea8 (Passthrough)
```

### The AHB Wrapping Allocator (`0xf54ec`)
```arm64
0xf55bc: str   x8, [sp, #0x28]            ; Actively injects VkExportMemoryAllocateInfo (OPAQUE_FD = 0x400)
0xf5608: blr   x8                         ; Calls native driver vkAllocateMemory
0xf560c: cbz   w0, #0xf5630               ; Checks native driver result
...
0xf5634: ldr   x9, [x21, #0x1de0]         ; vkGetMemoryAndroidHardwareBufferANDROID func ptr
0xf5654: blr   x9                         ; Calls vkGetMemoryAndroidHardwareBufferANDROID
0xf5658: cbz   w0, #0xf56a4               ; IF RESULT != 0 -> ABORT AND RETURN ERROR CODE!
```

---

## 4. The PowerVR Catch-22

1. **Path A (Passthrough with Export Struct):**
   If Wine passes `VkExportMemoryAllocateInfo` (`handleTypes = 0x400 = OPAQUE_FD`), the Imagination PowerVR Vulkan driver on Tensor G5 **rejects the allocation** because PowerVR's mobile driver does not support POSIX `OPAQUE_FD` memory exports on Android.
2. **Path B (AHB Wrapping Subsystem):**
   If `VkExportMemoryAllocateInfo` is not present, the wrapper diverts the allocation into `0xf4ed4`, which calls `vkGetMemoryAndroidHardwareBufferANDROID`. PowerVR **does not support creating an Android Hardware Buffer on internal CPU host-visible buffer memory**, causing `vkGetMemoryAndroidHardwareBufferANDROID` to return an error.
3. **The Solution:**
   - **Bypass the AHB allocator entirely:** Change `0xf4da8` (`tbz w8, #1, #0xf4ea8`) to an unconditional branch `b #0xf4ea8`.
   - **Sanitize `pNext` in Passthrough Hook:** Hook `0xf4ed0` to jump to padding at `0x281c00`, walk the `pNext` chain, unlink `0x3b9be342` (`VkExportMemoryAllocateInfo`), and call native `vkAllocateMemory`.
   - **Environment Guard:** Pass `DISABLE_EXTERNAL_FD=1` to prevent DXVK from even attempting to attach external memory descriptors to query pools.
