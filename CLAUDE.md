# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

MAGIC4FDG v2: Coverage-guided multi-agent fuzz driver generation for C/C++ libraries. Uses LangGraph to orchestrate 7 specialized agents (Knowledge, Planner, Generation, Patching, Coverage, Checkpoint, Analyst) that iteratively generate, compile-fix, and improve LibFuzzer fuzz drivers based on fine-grained coverage feedback.

Core design: **Explore → Exploit**. Round 1 uses LLM scene reasoning to allocate harness slots with matched strategies (explore), Round 2+ incrementally improves each slot using Analyst constraints (exploit).

## Commands

### Install dependencies
```bash
pip install -e .
```

### Run tests
```bash
python3 -m pytest tests/ -v
```

### Run v2 multi-agent pipeline
```bash
PYTHONUNBUFFERED=1 python3 -m src.pipeline.supervisor \
  --target-config targets/cjson.json \
  --max-rounds 5 \
  --target-coverage 100 \
  --fuzz-seconds 60 \
  --temperature 0.4
```

### Build Docker image
```bash
./scripts/docker_build.sh
```

## Architecture (v2)

### Pipeline DAG (LangGraph StateGraph)
```
Knowledge(clang AST) → Planner(select strategies) → Generation(N variants)
    → Patching(≤3 retries) → [any compiled?] → Coverage(fuzz+llvm-cov)
    → Checkpoint(save state) → Analyst(diagnose gaps)
    → [Supervisor Decision: continue/stop] → Planner(exploit) → ...
```

### Agent Roles
| Agent | Role | LLM? |
|-------|------|------|
| Knowledge | clang AST extraction: APIs, types, call graph | No |
| Planner | Round 1: LLM scene reasoning for slot allocation; Round 2+: iterate active slots | Yes |
| Generation | From-scratch (R1) or incremental (R2+) harness generation | Yes |
| Patching | Fix compilation errors (≤3 retries) | Yes |
| Coverage | Docker fuzz + llvm-cov + CFG reachability analysis | No |
| Checkpoint | Serialize state to JSON after each coverage round | No |
| Analyst | Diagnose coverage gaps → structured constraints (no code) | Yes |

### Key Design Decisions
- **Single model**: claude-opus-4-6 only (experiment showed no universal model advantage)
- **Explore → Exploit**: Round 1 LLM reasons about API semantics to design fuzz scenarios, Round 2+ iterates each slot independently
- **All-in-Docker**: Knowledge extraction (clang AST), compilation, fuzzing, and coverage all run inside Docker containers
- **Build cache**: Knowledge container builds once → cache to host → compile/fuzz mount read-only (skip rebuild)
- **Corpus replay for coverage**: Fork mode workers get SIGKILL'd at timeout (profraw lost); replay corpus with `-runs=0` (no fork) to collect complete coverage
- **clang AST knowledge**: `clang -Xclang -ast-dump=json` for precise type info and macro expansion
- **Strategy library**: 10 strategies in `strategies/*.md` with YAML frontmatter metadata
- **Knowledge accumulation**: constraints_discovered, positive_patterns, negative_patterns grow across rounds
- **Checkpoint recovery**: state saved after each Coverage round, supports `--resume`
- **Fork mode** (`-fork=1`): LibFuzzer runs each input in subprocess; ASan crashes don't prevent coverage
- **ASAN_OPTIONS**: `halt_on_error=0:exitcode=0:detect_leaks=0` for crash tolerance
- **CFG reachability filtering**: avoids wasting LLM calls on structurally unreachable code paths

### Strategy Library (`strategies/`)
| Strategy | Style |
|----------|-------|
| `parse-centric` | Feed raw fuzz bytes to parser APIs |
| `multi-api-sequence` | Chain multiple API calls with state |
| `roundtrip` | Parse → modify → serialize → re-parse |
| `targeted-expansion` | Coverage-feedback-guided (exploit rounds) |
| `structure-aware` | Construct valid structured inputs |
| `error-path` | Focus on error handling paths |
| `stateful` | Maintain state across calls |
| `resource-boundary` | Memory/buffer boundary testing |
| `callback-driven` | Register callbacks + trigger |
| `differential` | Compare different API implementations |

### LLM Configuration
Single model in `llm_config.json`:
```json
{
  "models": [{"name": "claude-opus-4-6", "api_url": "...", "api_key": "...", "max_tokens": 4096}],
  "defaults": {"timeout": 120, "max_retries": 5}
}
```
Fallback: `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_URL` environment variables.

### Target Config Format
JSON files in `targets/`. All libraries use unified `build_command` mode — Docker container downloads, builds, and installs to `/opt/bench/<lib>/`.

Required fields: `library_name`, `header`, `build_command`, `static_libs`, `include_dirs`, `language`, `description`.
Optional fields: `source_files` (usually empty), `coverage_sources`, `seed_corpus`, `dictionary`, `link_flags`.

Path convention: `header`/`include_dirs`/`static_libs`/`coverage_sources` use Docker-internal paths (`/opt/bench/...`). `seed_corpus`/`dictionary`/`build_command` use project-relative paths (accessed via `/workspace` mount).

### Output
```
generated/iterations/<library_name>/<YYYYMMDD_HHMMSS>/
├── best_driver.cpp
├── best_drivers/
│   ├── slot_0.cpp
│   ├── slot_1.cpp
│   └── ...
├── variants/
├── report.md
└── report.json
generated/iterations/<library_name>/latest -> <YYYYMMDD_HHMMSS>
generated/checkpoints/<library_name>/round_<N>.json
```

### Docker
Ubuntu 22.04 with clang/llvm/lld (image: `magic4fdg:latest`). Orchestrator runs on host; ALL execution (Knowledge/compile/fuzz/coverage) in container.

**Build cache**: Knowledge container builds library → caches artifacts to `generated/build_cache/<lib>/` → subsequent compile/fuzz containers mount cache as `/opt/bench:ro` (skip rebuild).

**Coverage collection**: Fork mode (`-fork=1`) for crash isolation → corpus replay (`-runs=0`, no fork) to flush profraw → `llvm-profdata merge` → `llvm-cov export`.

## File Structure (v2)

```
src/
  pipeline/
    state.py           # v2 TypedDicts: PipelineState, DriverVariant, HarnessSlot, KnowledgeStore
    graph.py           # v2 LangGraph DAG with conditional routing
    supervisor.py      # CLI entry + checkpoint recovery
    checkpoint.py      # Serialize/restore state between rounds
    report.py          # Markdown + JSON report generation
  agents/
    knowledge.py       # clang AST extraction (no LLM)
    planner.py         # LLM scene reasoning (R1) + iteration management (R2+)
    generation.py      # From-scratch + incremental harness generation
    patching.py        # Compilation error fixing
    coverage.py        # Fuzz + coverage + reachability + slot updates
    analyst.py         # Coverage gap diagnosis → structured constraints
  knowledge/
    extractor.py       # clang AST JSON parsing
    context_builder.py # Per-agent knowledge context formatting
    grouping.py        # API grouping + strategy matching rule engine
  infra/
    llm_factory.py     # ChatOpenAI creation with retry
    docker_runner.py   # Docker execution: Knowledge/compile/fuzz/coverage + build cache
    token_tracker.py   # Token usage tracking
  baselines/
    naive.py           # Single-shot LLM baseline (no iteration)
strategies/            # 10 strategy .md files with YAML frontmatter
prompts/               # LLM prompt templates
targets/               # Target library configs
```

## Conventions

- Python 3.10+ (`from __future__ import annotations`, `X | Y` type hints)
- Dependencies: langgraph, langchain-core, langchain-openai, PyYAML
- Coverage: `-fprofile-instr-generate -fcoverage-mapping` with ASan
- Proxy: requires `LANGCHAIN_OPENAI_TCP_KEEPALIVE=0` (set in `__main__.py`)
- Docker proxy: `DOCKER_PROXY` env var (default `http://host.docker.internal:7897`) passed to containers for git clone
- Progress output: all agents print status with `flush=True` for real-time monitoring
- Use `PYTHONUNBUFFERED=1` when running pipeline to see output in real-time

## Known Issues (as of 2026-05-22)

1. **Report only shows last round's variants**: `report.json` and supervisor summary only count variants from the final round, not cumulative.

2. **langchain_openai import is slow**: Takes 40-180s due to network environment (proxy). Not a code bug.

3. **macOS xattr + Docker VirtioFS**: Extended attributes on generated files can cause "Resource deadlock avoided" errors. Mitigated by `_clear_xattr()` calls after file writes.
