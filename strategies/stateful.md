---
id: stateful
name: Stateful Persistent Fuzzing
category: state-exploration
applicable_when:
  - "library maintains session or context state across multiple operations"
  - "API has init/configure/process/finalize lifecycle"
  - "library behavior depends on accumulated state from prior calls"
incompatible_with: []
best_for:
  - "crypto libraries (context-based encryption/decryption)"
  - "network protocol libraries (connection state machines)"
  - "database/storage libraries"
---

## Strategy: Stateful Persistent Fuzzing

Maintain state across multiple fuzz-driven operations to explore deep state spaces:
- Initialize library context/session once, then perform multiple operations driven by fuzz bytes
- Use fuzz bytes to select operation type at each step (read byte → switch to add/remove/query/modify)
- Build up complex internal state through sequences of valid operations
- Interleave valid operations with edge-case operations to test state consistency
- Exercise state machine transitions: init→configure→process→reset→reconfigure→process
- Test concurrent or interleaved use of multiple contexts/sessions
- Verify cleanup works correctly after complex state accumulation
- Use FuzzedDataProvider to control: operation count, operation type per step, parameter values
