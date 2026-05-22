---
id: resource-boundary
name: Resource Boundary Testing
category: robustness
applicable_when:
  - "library allocates memory dynamically (malloc, realloc, buffer management)"
  - "library has configurable buffer sizes or limits"
  - "API accepts size parameters that control allocation"
incompatible_with: []
best_for:
  - "compression/decompression libraries"
  - "image processing libraries"
  - "buffer management libraries"
  - "libraries with preallocated buffer APIs"
---

## Strategy: Resource Boundary Testing

Test memory allocation boundaries, buffer limits, and resource exhaustion paths:
- Pass size parameters at boundaries: 0, 1, SIZE_MAX, powers of 2, off-by-one values
- Test preallocated buffer APIs with buffers that are too small, exactly right, and oversized
- Trigger reallocation paths by growing data structures incrementally
- Test with inputs that cause exponential memory growth (zip bombs, billion laughs patterns)
- Exercise APIs that accept caller-provided buffers vs internally-allocated buffers
- Test behavior when output buffer is smaller than required
- Verify no buffer overflows when size parameters are near limits
- Use FuzzedDataProvider to generate: buffer sizes, growth patterns, allocation counts
