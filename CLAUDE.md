# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

FuzzForge: Coverage-guided multi-agent fuzz driver generation for C/C++ libraries. Uses LangGraph to orchestrate 5 specialized LLM agents that iteratively generate, compile-fix, and improve LibFuzzer fuzz drivers based on fine-grained coverage feedback.

Core pipeline: Research Agent → Generation Agent (N models × 3 strategies) → Patching Agent → Coverage Agent (fuzz + llvm-cov + LLVM CFG reachability) → Refinement Agent → iterate with temperature escalation until target coverage reached or temperatures exhausted.

## Commands

### Install dependencies
```bash
pip install -e .
```

### Run tests
```bash
python3 -m pytest tests/ -v
```

### Run multi-agent pipeline
```bash
python3 -m src.pipeline \
  --target-config targets/cjson.json \
  --max-iterations 10 \
  --target-coverage 70 \
  --fuzz-seconds 15
```

### Build Docker image
```bash
./scripts/docker_build.sh
```

## Architecture

### Pipeline flow (LangGraph StateGraph)
```
Research → Generation(N variants) → Patching(≤3 retries each) → Coverage
                                                                    ↓
                                                          target met? → Done
                                                                    ↓
                                                    Refinement → Generation → Patching → Coverage → ...
```

Temperature escalation: each round uses next temperature from schedule (e.g., 0.4 → 0.7 → 0.9). Total iterations = len(temperatures).

### Key design decisions
- **Fork mode** (`-fork=1`): LibFuzzer runs each input in subprocess; ASan crashes don't prevent coverage collection
- **ASAN_OPTIONS**: `halt_on_error=0:exitcode=0:detect_leaks=0` for crash tolerance
- **Strategy matters more than temperature**: api-chain consistently outperforms parse and roundtrip
- **Coverage feedback gap**: Generation currently doesn't use prior coverage data (known limitation)

### Generation strategies (literature-backed)
| Strategy | Style | Academic basis |
|----------|-------|---------------|
| `parse` | Raw fuzz bytes → parser APIs | PromptFuzz, FUDGE |
| `api-chain` | Multi-API call sequences with state transitions | CKGFuzzer, Scheduzz |
| `roundtrip` | Parse → modify → serialize → re-parse | MUTATO, OSS-Fuzz-Gen |

### LLM configuration
Models and variant matrix defined in `llm_config.json`:
```json
{
  "models": [{"name": "...", "api_url": "...", "api_key": "...", "max_tokens": 2048}],
  "variant_matrix": {"strategies": ["parse", "api-chain", "roundtrip"], "temperatures": [0.4, 0.7, 0.9]}
}
```
Fallback: `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_URL` environment variables.

### Target config format
JSON files in `targets/`. Fields: `library_name`, `header`, `source_files`, `include_dirs`, `seed_corpus`, `language`, `description`.

### Output
```
generated/iterations/<library_name>/<YYYYMMDD_HHMMSS>/
├── best_driver.cpp
├── variants/
├── report.md
└── report.json
generated/iterations/<library_name>/latest -> <YYYYMMDD_HHMMSS>
```

### Docker
Ubuntu 22.04 with clang/llvm/lld. Orchestrator runs on host; compile/fuzz/coverage in container.

## Conventions

- Python 3.10+ (`from __future__ import annotations`, `X | Y` type hints)
- Dependencies: langgraph, langchain-core, langchain-openai
- Coverage: `-fprofile-instr-generate -fcoverage-mapping` with ASan
- Proxy: requires `LANGCHAIN_OPENAI_TCP_KEEPALIVE=0` (set in `__main__.py`)
- Progress output: all agents print status with `flush=True` for real-time monitoring
