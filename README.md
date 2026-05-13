# fuzz-driver-gen

Coverage-guided multi-agent fuzz driver generation for C/C++ libraries.

本工具使用 LangGraph 编排 5 个专业化 LLM Agent，自动为 C/C++ 库生成高覆盖率的 LibFuzzer fuzz driver。核心创新：

1. **细粒度覆盖率反馈** — 基于 llvm-cov 的行级/分支级覆盖率数据引导 LLM 改进 driver
2. **多变体生成 + 覆盖率融合** — 2 模型 × 3 提示词策略 × 2 温度的变体矩阵，分析各变体独特覆盖并融合
3. **LLVM CFG 可达性分析** — 用 LLVM opt 构建控制流图，过滤不可达分支，避免无效迭代

## 架构

```text
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph Supervisor                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Research → Generation(N变体) → Patching → Coverage          │
│                                                ↓             │
│                                         达标? → Done         │
│                                                ↓             │
│                                         Refinement(融合)     │
│                                                ↓             │
│                                         → Patching → ...     │
└─────────────────────────────────────────────────────────────┘
```

| Agent | 职责 |
|-------|------|
| Research | 分析目标库源码，识别代码路径和 API 语义 |
| Generation | 按变体矩阵生成多个 fuzz driver |
| Patching | 编译修复，每变体最多 3 轮 |
| Coverage | Docker 内 fuzz + llvm-cov + LLVM CFG 可达性分析 |
| Refinement | 分析各变体独特覆盖，LLM 融合最优 driver |

## 快速开始

### 安装

```bash
pip install -e .
```

### 环境变量

```bash
export LLM_API_KEY="sk-..."
# 可选：切换模型和 API 端点
export LLM_MODEL="gpt-4o"
export LLM_API_URL="https://api.openai.com/v1"
# 可选：DeepSeek 作为第二模型
export DEEPSEEK_API_KEY="sk-..."
export DEEPSEEK_API_URL="https://api.deepseek.com/v1"
```

### 运行完整流程

```bash
# 构建 Docker 镜像（编译和 fuzz 在容器内执行）
./scripts/docker_build.sh

# 运行多 Agent 流程
python3 -m src.agents.supervisor \
  --target-config targets/cjson_parse.json \
  --max-iterations 3 \
  --target-coverage 70 \
  --fuzz-seconds 15
```

### 单独使用各组件

```bash
# 分析头文件生成目标配置
python3 src/analyze_target.py \
  --library-name cjson \
  --header examples/cjson_lib/cJSON.h \
  --source examples/cjson_lib/cJSON.c \
  --include-dir examples/cjson_lib \
  --seed-corpus examples/cjson_lib/seed_corpus \
  --out-dir targets/generated

# 单次 driver 生成（不走多 Agent 流程）
python3 src/generate_driver.py \
  --target-config targets/cjson_parse.json \
  --mode llm

# 覆盖率报告
TARGET_CONFIG=targets/cjson_parse.json FUZZ_SECONDS=10 ./scripts/docker_run_coverage.sh
```

## 项目结构

```text
src/
  agents/                    ← 多 Agent 流程（LangGraph）
    state.py                   状态定义
    graph.py                   图构建和路由
    supervisor.py              CLI 入口
    research.py                Research Agent
    generation.py              Generation Agent
    patching.py                Patching Agent
    coverage.py                Coverage Agent
    refinement.py              Refinement Agent
    llm_factory.py             LLM 实例工厂
    docker_runner.py           Docker 执行封装
  analyze_target.py          ← 头文件分析（规则式）
  generate_driver.py         ← 单次 driver 生成
  generate_coverage_report.py← 覆盖率收集
  target_config.py           ← 共享配置模块
prompts/                     ← Prompt 模板
examples/                    ← 目标库源码
targets/                     ← 目标配置 JSON
scripts/                     ← Docker/构建脚本
```

## 多变体策略

| 维度 | 选项 | 理由 |
|------|------|------|
| 模型 | GPT-4o, DeepSeek-V3 | 不同模型对 C/C++ 代码理解互补 |
| 提示词策略 | basic, research, example | 不同信息量引导不同生成方向 |
| 温度 | 0.4, 0.9 | 低温精确、高温探索 |

## Docker

Ubuntu 22.04 + clang/llvm/lld。编排器在宿主机运行，编译/fuzz/覆盖率收集在容器内。

```bash
./scripts/docker_build.sh
```

## 测试

```bash
python3 -m pytest tests/ -v
```

## 许可

本项目仅用于防御性软件测试研究。
