---
id: error-path
name: Error Path Focused
category: robustness
applicable_when:
  - "library has error handling code paths (error codes, exceptions, cleanup routines)"
  - "library validates input parameters before processing"
  - "API functions return error codes or NULL on failure"
incompatible_with: []
best_for:
  - "libraries with complex error handling"
  - "security-critical libraries (crypto, TLS)"
  - "libraries with resource cleanup on error"
---

## Strategy: Error Path Focused

Specifically target error handling code paths that normal fuzzing rarely reaches:
- Pass NULL pointers where non-NULL is expected
- Pass zero-length buffers, negative sizes, INT_MAX values
- Call APIs in wrong order (process before init, use after free)
- Trigger allocation failures by requesting extremely large sizes
- Pass invalid enum values, out-of-range indices
- Test double-free scenarios and use-after-free patterns
- Verify error cleanup paths release all resources
- Call APIs with mismatched types or incompatible flag combinations
- Use FuzzedDataProvider to select which error condition to trigger per iteration
