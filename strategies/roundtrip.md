---
id: roundtrip
name: Roundtrip Serialization
category: data-integrity
applicable_when:
  - "library has both parse and serialize/print functions"
  - "library supports encode/decode or compress/decompress cycles"
  - "data can be transformed and re-consumed by the same library"
incompatible_with: []
best_for:
  - "serialization libraries (JSON, XML, YAML)"
  - "compression libraries"
  - "encoding/decoding libraries"
---

## Strategy: Round-Trip (MUTATO-style)

Parse → Modify → Serialize → Re-parse to cover both directions:
- Parse fuzz input into internal representation
- Apply mutations driven by fuzz bytes (add fields, delete fields, replace values, change types)
- Serialize back to string (both formatted and compact variants)
- Re-parse the serialized output to verify roundtrip consistency
- Compare or further manipulate the re-parsed result
- This exercises serializer formatting, escaping, buffer management paths that parse-only never reaches
- Test edge cases: empty structures, deeply nested, maximum field counts
- IMPORTANT: Do NOT call hook-setting APIs unless you provide valid function pointers
