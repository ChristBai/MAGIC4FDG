---
id: callback-driven
name: Callback/Hook Driven
category: state-exploration
applicable_when:
  - "library supports user-defined callbacks or hooks"
  - "library has event-driven or plugin architecture"
  - "API allows registering custom memory allocators or handlers"
incompatible_with:
  - "structure-aware"
best_for:
  - "event-driven libraries"
  - "libraries with custom allocator support"
  - "plugin/extension frameworks"
---

## Strategy: Callback/Hook Driven

Register callbacks and hooks to exercise event-driven code paths:
- Implement simple callback functions that exercise different behaviors (return success, return error, modify state)
- Register custom memory allocators that track allocations and can simulate failures
- Use fuzz bytes to control callback behavior (return value, whether to modify passed data)
- Test callback invocation order and frequency
- Register and unregister callbacks during operation to test lifecycle management
- Test with callbacks that trigger re-entrant API calls
- Verify library handles callback errors gracefully
- IMPORTANT: All function pointers must point to valid, implemented functions
- Use FuzzedDataProvider to control: which callbacks to register, callback return values, when to unregister
