# FuzzForge

Coverage-guided multi-agent fuzz driver generation for C/C++ libraries.

使用 LangGraph 编排 5 个 LLM Agent，自动为 C/C++ 库生成高覆盖率的 LibFuzzer fuzz driver。

## 核心特性

1. **多策略变体生成** — 3 种文献支撑的生成策略（parse/api-chain/roundtrip），每种产生结构差异显著的 driver
2. **细粒度覆盖率反馈** — 基于 llvm-cov 行级/分支级覆盖率引导 LLM 改进 driver
3. **LLVM CFG 可达性分析** — 用 LLVM IR 构建控制流图，过滤不可达分支，避免无效迭代
4. **温度递增迭代** — 每轮使用不同温度（0.4→0.7→0.9），自动遍历完温度列表后结束
5. **Fork 模式容错** — LibFuzzer `-fork=1` 模式确保 ASan 崩溃不影响覆盖率收集
6. **Token 消耗追踪** — 全局追踪各 Agent 的 LLM token 用量，输出到报告中

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     LangGraph StateGraph                        │
├─────────┬─────────────┬──────────┬───────────┬─────────────────┤
│Research │ Generation  │ Patching │ Coverage  │   Refinement    │
│  Agent  │  Agent      │  Agent   │  Agent    │     Agent       │
│         │ (N variants)│(≤3 retry)│(fuzz+cov) │  (fuse best)    │
└────┬────┴──────┬──────┴────┬─────┴─────┬─────┴────────┬────────┘
     │           │           │           │              │
  Analyze    Generate     Fix compile  Docker fuzz   Merge best
  headers    3 strategies  errors      + llvm-cov    variants
```

### 迭代流程

```
温度列表: [0.4, 0.7, 0.9]

Round 1: Research → Generation(temp=0.4, 6变体) → Patching → Coverage
                                                      ↓
                                            达标? → Done
                                                      ↓
Round 2: Refinement → Generation(temp=0.7, 6+1变体) → Patching → Coverage
                                                      ↓
                                            达标? → Done
                                                      ↓
Round 3: Refinement → Generation(temp=0.9, 6+1变体) → Patching → Coverage
                                                      ↓
                                            温度用完 → END
```

## 生成策略

| 策略 | 风格 | 学术依据 | 典型覆盖率 |
|------|------|----------|-----------|
| `api-chain` | 多 API 组合调用链，覆盖状态转换 | CKGFuzzer, Scheduzz | ~60-72% |
| `roundtrip` | 解析→修改→序列化→重解析 | MUTATO, OSS-Fuzz-Gen | ~50-65% |
| `targeted` | 基于覆盖率反馈定向生成，针对可达但未覆盖的行 | FuzzForge (本项目) | ~65-80% |

> 注：覆盖率数据基于 cJSON 库的实测结果。`targeted` 策略利用 LLVM CFG 可达性分析过滤不可达分支，仅引导 LLM 关注有效目标。

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
    {"name": "claude-sonnet-4-6", "api_url": "https://api.example.com/v1", "api_key": "sk-...", "max_tokens": 2048}
  ],
  "variant_matrix": {
    "strategies": ["api-chain", "roundtrip", "targeted"],
    "temperatures": [0.4, 0.7, 0.9]
  },
  "defaults": {
    "timeout": 120,
    "max_retries": 3
  }
}
```

或使用环境变量（单模型回退）：
```bash
export LLM_MODEL="claude-sonnet-4-6"
export LLM_API_KEY="sk-..."
export LLM_API_URL="https://api.example.com/v1"
```

### 运行

```bash
python3 -m src.pipeline \
  --target-config targets/cjson.json \
  --max-iterations 10 \
  --target-coverage 70 \
  --fuzz-seconds 15
```

参数说明：
- `--target-config`：目标库配置 JSON 文件路径
- `--max-iterations N`：最大迭代轮数上限（默认 10，实际由温度列表长度控制）
- `--target-coverage`：目标行覆盖率百分比（默认 70）
- `--fuzz-seconds`：每个变体的 fuzz 时间（秒，默认 15）
- `--max-compile-retries`：每个变体最多编译修复次数（默认 3）

### 输出

```
generated/iterations/cjson/20260515_001137/
├── best_driver.cpp      # 最高覆盖率 driver
├── variants/            # 所有生成的变体
├── report.md            # 可读报告（含覆盖率、token 消耗等）
└── report.json          # 机器可读指标
generated/iterations/cjson/latest -> 20260515_001137
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
├── agents/                  # LLM Agent 节点
│   ├── research.py              分析目标库 API 签名和调用模式
│   ├── generation.py            多策略 × 多模型生成 N 个变体
│   ├── patching.py              LLM 修复编译错误（≤3 次重试）
│   ├── coverage.py              Docker fuzz + llvm-cov + CFG 可达性
│   └── refinement.py            融合最优变体 + 推进温度
├── infra/                   # 基础设施
│   ├── llm_factory.py           ChatOpenAI 工厂（多模型路由）
│   ├── docker_runner.py         Docker 编译/fuzz/覆盖率收集
│   └── token_tracker.py         全局 token 消耗追踪
├── pipeline/                # 编排层
│   ├── state.py                 PipelineState TypedDict 定义
│   ├── graph.py                 StateGraph + 路由逻辑
│   ├── supervisor.py            CLI 入口 + 结果保存
│   └── report.py                Markdown/JSON 报告生成
├── config.py                # 项目根路径 + target config 加载
└── utils.py                 # strip_code_fences 等工具函数
prompts/                     # Prompt 模板
targets/                     # 目标配置 JSON
examples/                    # 目标库源码 + seed corpus
scripts/                     # Docker 构建和运行脚本
```

## Docker

Ubuntu 22.04 + clang/llvm/lld。编排器在宿主机运行，编译/fuzz/覆盖率收集在容器内。

```bash
./scripts/docker_build.sh    # 构建 fuzzforge:latest
```

## 测试

```bash
python3 -m pytest tests/ -v
```

## 已知限制

- Refinement 融合跨策略变体时可能产出"平庸折中"，覆盖率低于最佳单体
- 温度对覆盖率的影响不如策略选择显著
- `targeted` 策略依赖前一轮覆盖率数据，首轮无反馈时回退为 api-chain 行为

## 许可

本项目仅用于防御性软件测试研究。
