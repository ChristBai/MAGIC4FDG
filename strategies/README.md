# FuzzForge Strategy Library

This directory contains the strategy library for fuzz driver generation. Each `.md` file defines a distinct harness generation strategy that the Planner agent can select from.

## How Strategies Work

In Round 1, the Planner agent analyzes the target library's API surface and selects 3 strategies from this library for each entry point. Each strategy produces a structurally different fuzz driver that exercises the library from a different angle.

## File Format

Each strategy file uses YAML frontmatter for metadata and Markdown body for the prompt suffix:

```markdown
---
id: strategy-id          # unique identifier
name: Human Readable Name
category: input-generation | state-exploration | robustness | coverage-guided | correctness | data-integrity
applicable_when:         # conditions where this strategy is effective
  - "condition 1"
incompatible_with:       # strategies that conflict with this one
  - "other-strategy-id"
best_for:                # library types this strategy excels at
  - "library type"
---

## Strategy: Name

Prompt instructions for the LLM...
```

## Adding Custom Strategies

1. Create a new `.md` file in this directory following the format above
2. Choose a unique `id` (lowercase, hyphenated)
3. Fill in `applicable_when` and `best_for` to help the Planner select appropriately
4. Write clear, actionable instructions in the body
5. The strategy will be automatically discovered by the Planner on next run

## Available Strategies

| ID | Category | Best For |
|----|----------|----------|
| parse-centric | input-generation | Parser libraries |
| multi-api-sequence | state-exploration | Data structure libraries |
| roundtrip | data-integrity | Serialization libraries |
| targeted-expansion | coverage-guided | Subsequent iteration rounds |
| structure-aware | input-generation | Complex format parsers |
| error-path | robustness | Security-critical libraries |
| stateful | state-exploration | Crypto/protocol libraries |
| resource-boundary | robustness | Compression/buffer libraries |
| callback-driven | state-exploration | Event-driven libraries |
| differential | correctness | Libraries with multiple API variants |
