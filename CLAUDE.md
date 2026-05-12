# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

LLM-powered LibFuzzer fuzz driver generator for C/C++ libraries. The pipeline: analyze a C header to identify fuzz candidates → generate a target config JSON → render a prompt → call an LLM to produce a `LLVMFuzzerTestOneInput` driver → compile and fuzz with LibFuzzer → collect LLVM source-based coverage.

## Commands

### Run tests
```bash
python3 -m pytest tests/ -v
# Single test:
python3 -m pytest tests/test_analyze_target.py -v
```

### Generate a fuzz driver (dry-run prompt only)
```bash
python3 src/generate_driver.py --target-config targets/cjson_parse.json --mode prompt
```

### Generate a fuzz driver (call LLM)
```bash
export OPENAI_API_KEY="sk-..."
python3 src/generate_driver.py --target-config targets/cjson_parse.json --mode llm
```

### Analyze a header to auto-generate target configs
```bash
python3 src/analyze_target.py \
  --library-name cjson \
  --header examples/cjson_lib/cJSON.h \
  --source examples/cjson_lib/cJSON.c \
  --include-dir examples/cjson_lib \
  --seed-corpus examples/cjson_lib/seed_corpus \
  --out-dir targets/generated
```

### Build and fuzz (Docker, recommended)
```bash
./scripts/docker_build.sh
TARGET_CONFIG=targets/cjson_parse.json ./scripts/docker_run_fuzz.sh
```

### Coverage report (Docker)
```bash
TARGET_CONFIG=targets/cjson_parse.json FUZZ_SECONDS=10 ./scripts/docker_run_coverage.sh
```

### Build and fuzz (local, requires clang with LibFuzzer)
```bash
./scripts/build_and_run.sh
# Fallback (no LibFuzzer runtime):
./scripts/build_and_run_fallback.sh
```

### Compare coverage runs
```bash
python3 src/compare_coverage_reports.py generated/coverage/*/coverage_report.json
```

### Clean up run artifacts
```bash
./scripts/clean_runs.sh
```

## Architecture

### Pipeline flow
```
analyze_target.py → target config JSON → generate_driver.py → fuzz_driver.cpp
                                                                     ↓
                                              build_and_run.sh / docker_run_fuzz.sh
                                                                     ↓
                                              generate_coverage_report.py → coverage report
```

### Shared module: `src/target_config.py`
All Python scripts import from this module for config loading and path resolution. `ROOT` is the project root. Paths in target configs are relative to ROOT and resolved via `resolve_project_path()`. Shell scripts use `scripts/parse_target_config.py` (outputs shell variable assignments via `eval`).

### Target config format
JSON files in `targets/` describe a fuzz target. Key fields: `target_name`, `function_name`, `signature`, `header`, `source_files`, `include_dirs`, `seed_corpus`, `cleanup_function`, `description`, `language`. The coverage reporter needs only `REQUIRED_FIELDS_COMMON`; the driver generator needs `REQUIRED_FIELDS_DRIVER` (adds language, signature, header, description).

### LLM integration
Uses OpenAI Responses API (not Chat Completions). Endpoint is hardcoded in `src/generate_driver.py` as `OPENAI_RESPONSES_URL`. Model defaults to env `OPENAI_MODEL` or `gpt-5.4`.

### Output directories
- `generated/fuzz_driver.cpp` — latest generated driver
- `generated/runs/<target>-<timestamp>/` — per-run corpus, artifacts, logs
- `generated/coverage/<target>-<timestamp>/` — coverage reports (JSON, Markdown, HTML)
- Seed corpus directories are read-only; scripts copy them into run dirs before fuzzing

### Docker
Ubuntu 22.04 with clang/llvm/lld. Project is mounted at `/workspace`. Use `docker_build.sh` once, then `docker_run_fuzz.sh` or `docker_run_coverage.sh`.

## Conventions

- Python 3.10+ (type hints with `X | Y` syntax, `from __future__ import annotations`)
- No external Python dependencies; stdlib only (urllib for HTTP, json, subprocess, etc.)
- Shell scripts use `set -euo pipefail` and expect `CC`/`CXX` env vars (default: `clang`/`clang++`)
- `FUZZ_USE_CMP=0` (default) for stable 10-second runs; `FUZZ_USE_CMP=1` to allow crash discovery
- Coverage uses `-fprofile-instr-generate -fcoverage-mapping` with ASan
