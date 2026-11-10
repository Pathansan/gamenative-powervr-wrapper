# Disassembly & Binary Reconstruction: libvulkan_wrapper.so

## 1. Memory Segments & ELF Offsets

From `libvulkan_wrapper.so` ELF Program Headers:

| Segment | Type | File Offset Range | Virtual Address (VA) Range | Permissions | Content |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PH 1** | `PT_LOAD` | `0x000000 - 0x0d3c80` | `0x000000 - 0x0d3c80` | `R` (Read-only) | `.rodata`, strings, metadata |
| **PH 2** | `PT_LOAD` | `0x0d3c80 - 0x278470` | `0x0d7c80 - 0x27c470` | `R E` (Executable code) | `.text`, functions |
| **Padding** | (RX) | `0x27dc00 - 0x27e000` | `0x281c00 - 0x282000` | `R E` | Unused code padding (hook cave) |
| **PH 3** | `PT_LOAD` | `0x278470 - 0x29f4f0` | `0x280470 - 0x2a8000` | `RW` (Data) | `.data`, `.got`, `.bss` |

**VA to File Offset Conversion:**
```python
file_offset = 0xd3c80 + (virtual_address - 0xd7c80)
```

---

## 2. Reconstructed C Pseudocode

### `vkAllocateMemory` Entry Logic
```c
VkResult vkAllocateMemory(VkDevice device, const VkMemoryAllocateInfo* pAllocateInfo,
                          const VkAllocationCallbacks* pAllocator, VkDeviceMemory* pMemory) {
    uint32_t memoryTypeIndex = pAllocateInfo->memoryTypeIndex;
    uint32_t propertyFlags = device->memoryTypes[memoryTypeIndex].propertyFlags;

    // ORIGINAL CHECK at 0xf4da8:
    // if (!(propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)) goto passthrough;
    //
    // PATCHED LOGIC:
    // Unconditional branch directly to passthrough:
    goto passthrough;

    // --- DEAD / UNPATCHED WRAPPER PATH ---
    if (device->flag_0x370 == 1 && device->flag_0x11d == 1) {
        VkExportMemoryAllocateInfo* exportInfo = find_pnext_struct(pAllocateInfo->pNext, VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO);
        if (exportInfo != NULL) {
            goto passthrough;
        }
        // If export struct not found, diverts into AHB wrapper:
        return allocate_ahb_wrapped_memory(device, pAllocateInfo, pAllocator, pMemory);
    }

passthrough:
    VkDevice nativeDevice = device->nativeDeviceHandle;
    PFN_vkAllocateMemory nativeAlloc = device->nativeVkAllocateMemory;
    
    // Hook at 0x281c00:
    sanitize_pnext_export_info((void**)&pAllocateInfo->pNext);
    
    return nativeAlloc(nativeDevice, pAllocateInfo, pAllocator, pMemory);
}
```

### The `pNext` Sanitizer Hook (`0x281c00`)
```c
void sanitize_pnext_export_info(void** pNextHead) {
    if (!pNextHead) return;
    
    void** current = pNextHead;
    while (*current != NULL) {
        VkBaseInStructure* node = (VkBaseInStructure*)(*current);
        if (node->sType == VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO) { // 0x3b9be342
            // Unlink node from chain
            *current = (void*)node->pNext;
        } else {
            current = (void**)&(node->pNext);
        }
    }
}
```
