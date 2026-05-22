---
id: structure-aware
name: Structure-Aware Input Construction
category: input-generation
applicable_when:
  - "library parses structured formats (JSON, XML, protobuf, image, audio)"
  - "library has schema validation or format verification APIs"
  - "input format has a known grammar or specification"
incompatible_with:
  - "callback-driven"
best_for:
  - "parser libraries with complex input grammars"
  - "image/media format decoders"
  - "protocol message parsers"
---

## Strategy: Structure-Aware Input Construction

Construct syntactically valid structured inputs to reach deep parsing paths:
- Use FuzzedDataProvider to generate valid structure skeletons with fuzz-controlled field values
- Build inputs that pass initial format validation to reach deeper processing logic
- Mutate specific fields while keeping overall structure valid (valid header + fuzzed payload)
- Test boundary cases within valid structure (max nesting depth, max field count, max string length)
- Alternate between valid and slightly-invalid inputs to cover error recovery paths
- Exercise all format variants and optional features the library supports
- For binary formats: construct valid magic bytes/headers, fuzz the payload sections
- For text formats: maintain syntactic validity while fuzzing semantic content
