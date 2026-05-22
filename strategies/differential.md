---
id: differential
name: Differential Testing
category: correctness
applicable_when:
  - "library provides multiple implementations of the same operation"
  - "library has both safe/checked and fast/unchecked API variants"
  - "library supports multiple output formats for the same data"
incompatible_with: []
best_for:
  - "crypto libraries (different cipher modes, padding schemes)"
  - "libraries with legacy and modern API variants"
  - "libraries with platform-specific optimized paths"
---

## Strategy: Differential Testing

Compare multiple implementations or API variants to find inconsistencies:
- Call two different API functions that should produce equivalent results
- Compare outputs of formatted vs unformatted serialization after re-parsing
- Test safe/checked variants against fast/unchecked variants with same input
- Compare behavior of deprecated APIs against their modern replacements
- Use same input with different configuration flags and verify consistent semantics
- Test streaming vs batch processing of the same data
- Verify that error conditions are reported consistently across API variants
- Use FuzzedDataProvider to generate: input data, configuration variants to compare
