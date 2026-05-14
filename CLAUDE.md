# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Coverage-guided multi-agent fuzz driver generation for C/C++ libraries. Uses LangGraph to orchestrate 5 specialized LLM agents that iteratively generate, compile-fix, and improve LibFuzzer fuzz drivers based on fine-grained coverage feedback.

Core pipeline: Research Agent → Generation Agent (N models × 3 strategies × M temperatures) → Patching Agent → Coverage Agent (fuzz + llvm-cov + LLVM CFG reachability) → Refinement Agent → iterate until target coverage reached.

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
  --max-iterations 3 \
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
                                                          Refinement → Patching → Coverage → ...
```

### Directory structure
```
src/
├── agents/          # LLM agent nodes
│   ├── research.py      # Analyzes target library APIs
│   ├── generation.py    # Produces N fuzz driver variants
│   ├── patching.py      # Fixes compilation errors via LLM
│   ├── coverage.py      # Runs fuzz + coverage + CFG reachability
│   └── refinement.py    # Fuses best variants into improved driver
├── infra/           # Infrastructure
│   ├── llm_factory.py   # ChatOpenAI factory (multi-model)
│   └── docker_runner.py # Docker compile/fuzz/coverage execution
├── pipeline/        # Orchestration
│   ├── state.py         # PipelineState TypedDict
│   ├── graph.py         # StateGraph definition + routing
│   ├── supervisor.py    # CLI entry point + result saving
│   └── report.py        # Markdown/JSON report generation
├── config.py        # ROOT, load_target_config
└── utils.py         # strip_code_fences
```

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
  "variant_matrix": {"strategies": ["parse", "api-chain", "roundtrip"], "temperatures": [0.7]}
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
