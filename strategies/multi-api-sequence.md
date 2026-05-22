---
id: multi-api-sequence
name: Multi-API Call Sequence
category: state-exploration
applicable_when:
  - "library has create/modify/query/delete lifecycle APIs"
  - "library maintains internal state across API calls"
  - "API has ownership transfer semantics (detach/attach)"
incompatible_with: []
best_for:
  - "data structure libraries"
  - "container/collection libraries"
  - "stateful protocol libraries"
---

## Strategy: Multi-API Sequence (CKGFuzzer-style)

Use fuzz bytes to drive multi-API call sequences covering state transitions:
- Use first bytes as path selectors (switch on data[0] % N)
- Each path exercises a different API combination chain (create→add→query→modify→delete)
- Cover ownership transfer (DetachItem→AddItem to different parent)
- Cover edge cases: empty containers, index out of bounds, NULL keys
- Allocate via Create* APIs, modify via Add*/Replace*/Delete*, query via Get*/Has*, cleanup via Delete
- Ensure proper resource cleanup on all paths (no leaks)
- Use FuzzedDataProvider to consume bytes for: path selection, string keys, numeric indices, array sizes
- IMPORTANT: Do NOT call hook-setting or init APIs unless you provide valid function pointers
