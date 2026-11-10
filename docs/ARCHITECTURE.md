# GameNative & Winlator Architecture on Android (Tensor G5 / PowerVR)

## 1. System Execution Stack

The execution path from game binary down to the Pixel 10 Pro XL hardware GPU:

```
+-------------------------------------------------------------------+
|                     No Man's Sky (NMS.exe)                        |
|             Windows x86_64 PE (Steam AppID 275850)                |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|               Wine 9.0 / Proton 9.0 (ARM64EC Mode)                |
|  Translates Win32/NT APIs to POSIX Bionic / Linux syscalls         |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                    DXVK 2.4.1 / WineVulkan                        |
|  - D3D11 / D3D9 -> Vulkan translation                             |
|  - Wine loader thunks (dlls/winevulkan/loader_thunks.c)           |
|  - Communicates with Vulkan ICD through libvulkan.so              |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                    libvulkan_wrapper.so                           |
|  - Vulkan-on-Vulkan translation layer (bionic-vulkan-wrapper)     |
|  - Intercepts vkCreateInstance, vkCreateDevice, vkAllocateMemory  |
|  - Handles texture format emulation (BCn transcoding)            |
|  - WSI display presentation (Android AHardwareBuffer / X11)      |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|             Native PowerVR Vulkan Driver (libvulkan.so)           |
|  - Imagination DXT-48-1536 MC1 Driver (v1.662.3024)              |
|  - Vendor HAL: /vendor/lib64/hw/vulkan.powerVR...                 |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|            Hardware: Google Tensor G5 PowerVR GPU Core            |
+-------------------------------------------------------------------+
```

---

## 2. Package Distribution & Container Management (.wcp)

GameNative manages drivers, wrappers, and tools via `.wcp` (Winlator Content Package) archives.

### Anatomy of a `.wcp` Package
A `.wcp` package is a **tar archive compressed with Zstandard (`.tar.zst`)**.

It contains:
1. `profile.json`:
   ```json
   {
     "type": "WRAPPER",
     "versionName": "1.5-pvr-fix",
     "versionCode": 9999,
     "description": "PowerVR Vulkan Wrapper Fix",
     "files": [
       {
         "source": "libvulkan_wrapper.so",
         "target": "${libdir}/libvulkan_wrapper.so"
       }
     ]
   }
   ```
2. The payload file(s), e.g., `libvulkan_wrapper.so` at `755` permissions.

### The Versioning Trap in GameNative
When a user installs a `.wcp` through **Settings -> Contents Manager -> Wrappers**:
1. `ContentsManager` checks whether a package with `versionName` already exists.
2. If `new.versionCode <= existing.versionCode`, GameNative **skips extraction** or leaves existing files untouched.
3. Crucially, **existing game containers do not automatically migrate to newly installed wrappers**. The container's shortcut configuration (`No_Mans_Sky_PowerVR.json`) stores an explicit `graphicsDriverVersion` string (e.g., `"1.1-pvr-fix"`).
4. If a newer wrapper is imported under a different `versionName` (e.g., `"1.5-pvr-fix"`), the container **continues running the old wrapper** until:
   - The user explicitly changes the dropdown in Container Settings to `"1.5-pvr-fix"`, OR
   - The update package is released using the **exact same `versionName`** (e.g., `"1.1-pvr-fix"`) but with an elevated `versionCode` (e.g., `9999`), forcing GameNative to overwrite the container's active library in place.
