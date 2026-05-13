# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Coverage-guided multi-agent fuzz driver generation for C/C++ libraries. Uses LangGraph to orchestrate 5 specialized LLM agents that iteratively generate, compile-fix, and improve LibFuzzer fuzz drivers based on fine-grained coverage feedback.

Core pipeline: Research Agent analyzes target → Generation Agent produces N driver variants (2 models × 3 strategies × 2 temperatures) → Patching Agent fixes compilation errors → Coverage Agent runs fuzz + llvm-cov + LLVM CFG reachability analysis → Refinement Agent fuses best coverage into improved driver → iterate.

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
python3 -m src.agents.supervisor \
  --target-config targets/cjson_parse.json \
  --max-iterations 3 \
  --target-coverage 70 \
  --fuzz-seconds 15
```

### Single-shot driver generation (legacy)
```bash
python3 src/generate_driver.py --target-config targets/cjson_parse.json --mode llm
```

### Build and fuzz (Docker)
```bash
./scripts/docker_build.sh
TARGET_CONFIG=targets/cjson_parse.json ./scripts/docker_run_fuzz.sh
```

### Coverage report (Docker)
```bash
TARGET_CONFIG=targets/cjson_parse.json FUZZ_SECONDS=10 ./scripts/docker_run_coverage.sh
```

## Architecture

### Multi-agent pipeline (LangGraph)
```
Research → Generation(N variants) → Patching(≤3 retries each) → Coverage
                                                                    ↓
                                                          target met? → Done
                                                                    ↓
                                                          Refinement → Patching → Coverage → ...
```

### Key modules
- `src/agents/` — LangGraph multi-agent pipeline
  - `state.py` — PipelineState TypedDict (shared state across all nodes)
  - `graph.py` — StateGraph definition with conditional routing
  - `llm_factory.py` — ChatOpenAI factory (supports multiple models/endpoints)
  - `docker_runner.py` — Docker execution wrapper for compile/fuzz/coverage
- `src/target_config.py` — Shared config loading (ROOT, load_target_config, resolve_project_path)
- `src/generate_driver.py` — Legacy single-shot generation (render_prompt, strip_code_fences)
- `src/generate_coverage_report.py` — Coverage collection (run_command, summarize_export, metric_percent)

### LLM integration
Uses `langchain-openai` ChatOpenAI with OpenAI-compatible APIs. Environment variables:
- `LLM_API_KEY` — Primary API key
- `LLM_API_URL` — API base URL (default: OpenAI)
- `LLM_MODEL` — Model name (default: gpt-4o)
- `DEEPSEEK_API_KEY` / `DEEPSEEK_API_URL` — Secondary model endpoint

### Target config format
JSON files in `targets/`. Key fields: `target_name`, `function_name`, `signature`, `header`, `source_files`, `include_dirs`, `seed_corpus`, `cleanup_function`, `description`, `language`.

### Output directories
- `generated/iterations/<target>/` — Multi-agent pipeline output (variants, reports)
- `generated/fuzz_driver.cpp` — Legacy single-shot output
- `generated/runs/` — Per-run corpus, artifacts, logs
- `generated/coverage/` — Coverage reports

### Docker
Ubuntu 22.04 with clang/llvm/lld/python3/pip. Orchestrator runs on host; compile/fuzz/coverage in container.

## Conventions

- Python 3.10+ (type hints with `X | Y` syntax, `from __future__ import annotations`)
- Dependencies: langgraph, langchain-core, langchain-openai
- Shell scripts use `set -euo pipefail`
- Coverage uses `-fprofile-instr-generate -fcoverage-mapping` with ASan
- `FUZZ_USE_CMP=0` (default) for stable runs; `FUZZ_USE_CMP=1` for crash discovery
