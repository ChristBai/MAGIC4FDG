---
id: targeted-expansion
name: Targeted Coverage Expansion
category: coverage-guided
applicable_when:
  - "prior coverage data is available from previous rounds"
  - "specific uncovered functions or branches have been identified"
  - "analyst has provided structured constraints for reaching uncovered code"
incompatible_with: []
best_for:
  - "second and subsequent iteration rounds"
  - "libraries with identified but unreached code paths"
  - "any library after initial exploration round"
---

## Strategy: Targeted Coverage Expansion

Based on coverage feedback and analyst constraints, specifically target uncovered functions and branches:
- For each zero-coverage function listed, construct valid arguments and call it
- For each uncovered line with source context, reason about what API call sequence reaches it
- Chain API calls that lead to the uncovered code paths
- Use fuzz bytes to vary arguments and trigger different branches within targeted functions
- Prioritize functions with 0% coverage over those with partial coverage
- Follow analyst-provided preconditions exactly (e.g., "must call init before process")
- If analyst identified required parameter constraints, use those specific values
- IMPORTANT: Do NOT call hook-setting APIs unless you provide valid function pointers
