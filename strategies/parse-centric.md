---
id: parse-centric
name: Parse-Centric Input Fuzzing
category: input-generation
applicable_when:
  - "library has parsing functions that accept raw bytes or strings"
  - "library processes external file formats or protocols"
  - "API includes multiple parse variants (with/without length, with/without options)"
incompatible_with: []
best_for:
  - "parser libraries (JSON, XML, image, audio)"
  - "format decoders"
  - "protocol parsers"
---

## Strategy: Parse-Centric (PromptFuzz-style)

Feed raw fuzz bytes directly to ALL parsing API variants. Maximize parser path coverage:
- Call every parse function variant (with/without length, with/without options)
- Use different option flag combinations to exercise all parser modes
- Exercise error paths by feeding truncated, malformed, and oversized data
- After successful parse, do minimal downstream operations to trigger post-parse paths
- Test both null-terminated and length-delimited input modes
- Do NOT focus on construction APIs — prioritize parser internals
- Use FuzzedDataProvider to split input into: data buffer + option flags
