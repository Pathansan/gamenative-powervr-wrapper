# Imagination PowerVR D-Series & Google Tensor G5 Knowledge Base

## 1. Hardware Specifications

- **SoC:** Google Tensor G5 (`mustang`)
- **GPU Architecture:** Imagination Technologies PowerVR D-Series
- **Core Model:** DXT-48-1536 MC1
- **Driver Version:** `1.662.3024`
- **Vulkan API Support:** Vulkan 1.3 Conformance Profile
- **OS Platform:** Android 17 (ARM64-v8a / Bionic)

---

## 2. PowerVR DXT Mobile Driver Characteristics

Unlike Qualcomm Adreno (which has open-source Turnip / freedreno drivers with full desktop extension faking) and ARM Mali:

1. **Memory Handling:**
   - Unified physical RAM, but strictly partitioned driver memory types.
   - Host-visible coherent memory is supported via standard `vkMapMemory2KHR` / `vkMapMemory`.
   - **External Memory Limits:** The driver implements `VK_ANDROID_external_memory_android_hardware_buffer` exclusively for graphics buffers (surfaces, textures). It **does not support `VK_KHR_external_memory_fd` (opaque POSIX file descriptors)** for arbitrary CPU-side buffers.
2. **Placed Memory Limitation (`VK_EXT_map_memory_placed`):**
   - The PowerVR driver does not allow user-specified virtual address memory placement (`VK_EXT_map_memory_placed`).
   - The wrapper correctly identifies this at startup: `[info]: Disabling VK_EXT_map_memory_placed`.
   - Required container setting: `WRAPPER_DISABLE_PLACED=1`.
3. **Geometry Shader Emulation:**
   - PowerVR hardware lacks dedicated native geometry shader hardware pipeline stages.
   - Translation layers (DXVK/VKD3D) that encounter games with geometry shaders require geometry shader faking (`bionic-vulkan-wrapper` v0.0.5r4+ implements specific PowerVR GS workarounds).
4. **BCn Texture Compression:**
   - PowerVR mobile GPUs do not natively decode desktop Direct3D BCn (BC1–BC7 / DXT1–DXT5) texture formats.
   - The wrapper handles BCn textures via compute shader transcoding (`WRAPPER_BCN_ASTC=1`).

---

## 3. Recommended Environment Variables for PowerVR

Add these variables to the container's `envVars` string:

```bash
# Disable external memory fd paths in DXVK
DISABLE_EXTERNAL_FD=1

# Disable placed memory (not supported on PowerVR)
WRAPPER_DISABLE_PLACED=1

# BCn Texture compute emulation
WRAPPER_BCN_ASTC=1
WRAPPER_SAFE_CREATE_DEVICE=1
WRAPPER_EMULATE_IMAGELESS_FRAMEBUFFER=1

# WSI and Memory Limits
WRAPPER_VMEM_MAX_SIZE=4096
WRAPPER_MAX_IMAGE_COUNT=0
MESA_VK_WSI_PRESENT_MODE=mailbox

# Enable full diagnostic logging to SDCard
WRAPPER_LOG_LEVEL=info,error,validation,bcn,apidump
WRAPPER_LOG_FILE=/sdcard/Download/wrapper.log
```
