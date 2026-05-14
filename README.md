# FuzzForge

Coverage-guided multi-agent fuzz driver generation for C/C++ libraries.

使用 LangGraph 编排 5 个 LLM Agent，自动为 C/C++ 库生成高覆盖率的 LibFuzzer fuzz driver。核心创新：

1. **细粒度覆盖率反馈** — 基于 llvm-cov 行级/分支级覆盖率引导 LLM 改进 driver
2. **文献驱动的多策略生成** — 基于 PromptFuzz/CKGFuzzer/MUTATO 等研究的 3 种结构化策略
3. **LLVM CFG 可达性分析** — 用 LLVM opt 构建控制流图，过滤不可达分支，避免无效迭代

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     LangGraph StateGraph                         │
├─────────┬─────────────┬──────────┬───────────┬─────────────────┤
│Research │ Generation  │ Patching │ Coverage  │   Refinement    │
│  Agent  │   Agent     │  Agent   │  Agent    │     Agent       │
│         │(N variants) │(≤3 retry)│(fuzz+cov) │  (fuse best)    │
└────┬────┴──────┬──────┴────┬─────┴─────┬─────┴────────┬────────┘
     │           │           │           │              │
  Analyze    Generate     Fix compile  Docker fuzz   Merge best
  headers    3 strategies  errors      + llvm-cov    variants
```

**迭代循环**: Coverage Agent 检查是否达标 → 未达标则 Refinement Agent 融合最优变体 → 重新 Patching → Coverage。

## 生成策略（文献支撑）

| 策略 | 风格 | 学术依据 |
|------|------|----------|
| `parse` | 原始 fuzz 字节直接喂给解析 API | PromptFuzz, FUDGE |
| `api-chain` | 多 API 组合调用链，覆盖状态转换 | CKGFuzzer, Scheduzz |
| `roundtrip` | 解析→修改→序列化→重解析 | MUTATO, OSS-Fuzz-Gen |

## 快速开始

### 安装

```bash
pip install -e .
./scripts/docker_build.sh
```

### 配置 LLM

编辑 `llm_config.json`：
```json
{
  "models": [
    {"name": "gpt-4o", "api_url": "https://api.openai.com/v1", "api_key": "sk-...", "max_tokens": 2048}
  ],
  "variant_matrix": {
    "strategies": ["parse", "api-chain", "roundtrip"],
    "temperatures": [0.7]
  }
}
```

或使用环境变量（单模型回退）：
```bash
export LLM_MODEL="gpt-4o"
export LLM_API_KEY="sk-..."
export LLM_API_URL="https://api.openai.com/v1"
```

### 运行

```bash
python3 -m src.pipeline \
  --target-config targets/cjson.json \
  --max-iterations 3 \
  --target-coverage 70 \
  --fuzz-seconds 15
```

参数说明：
- `--max-iterations N`：最多 N 轮 coverage 测量（N-1 次 refinement）
- `--target-coverage`：目标行覆盖率百分比
- `--fuzz-seconds`：每个变体的 fuzz 时间（秒）
- `--max-compile-retries`：每个变体最多编译修复次数（默认 3）

### 输出

```
generated/iterations/cjson/20260514_153000/
├── best_driver.cpp      # 最高覆盖率 driver
├── variants/            # 所有生成的变体
├── report.md            # 可读报告
└── report.json          # 机器可读指标
generated/iterations/cjson/latest -> 20260514_153000
```

## Target 配置格式

```json
{
  "library_name": "cjson",
  "header": "examples/cjson_lib/cJSON.h",
  "source_files": ["examples/cjson_lib/cJSON.c"],
  "include_dirs": ["examples/cjson_lib"],
  "seed_corpus": "examples/cjson_lib/seed_corpus",
  "language": "C",
  "description": "Lightweight JSON parser for C"
}
```

## 项目结构

```
src/
├── agents/              # LLM Agent 节点
│   ├── research.py          分析目标库 API
│   ├── generation.py        多策略生成 N 个变体
│   ├── patching.py          LLM 修复编译错误
│   ├── coverage.py          Fuzz + 覆盖率 + CFG 可达性
│   └── refinement.py        融合最优变体
├── infra/               # 基础设施
│   ├── llm_factory.py       ChatOpenAI 工厂（多模型）
│   └── docker_runner.py     Docker 编译/fuzz/覆盖率
├── pipeline/            # 编排层
│   ├── state.py             PipelineState 类型定义
│   ├── graph.py             StateGraph + 路由逻辑
│   ├── supervisor.py        CLI 入口 + 结果保存
│   └── report.py            报告生成
├── config.py            # ROOT, load_target_config
└── utils.py             # strip_code_fences
prompts/                 # Prompt 模板
targets/                 # 目标配置 JSON
examples/                # 目标库源码
scripts/                 # Docker 构建脚本
```

## Docker

Ubuntu 22.04 + clang/llvm/lld。编排器在宿主机运行，编译/fuzz/覆盖率收集在容器内。

## 测试

```bash
python3 -m pytest tests/ -v
```

## 许可

本项目仅用于防御性软件测试研究。
