# MAGIC4FDG

覆盖率引导的多 Agent Fuzz Driver 自动生成系统，面向 C/C++ 库。

使用 LangGraph 编排 7 个专用 Agent，通过 Explore → Exploit 迭代策略，
自动为目标库生成高覆盖率的 LibFuzzer fuzz driver 集合。

## 核心特性

1. **多 Slot 独立演化** — 每个 API 分组独立分配 harness slot，各自演化最佳 driver
2. **Explore → Exploit** — Round 1 用多种策略探索，Round 2+ 基于覆盖率反馈精准改进
3. **细粒度覆盖率反馈** — llvm-cov 行级/分支级覆盖率 + 函数级覆盖率报告
4. **LLVM CFG 可达性分析** — 过滤结构性不可达代码，避免无效迭代
5. **Per-Slot 知识隔离** — 每个 slot 独立积累约束和模式，互不干扰
6. **Fork 模式容错** — LibFuzzer `-fork=1` 确保 ASan 崩溃不影响覆盖率收集
7. **Driver 集合输出** — 输出每个 slot 的最佳 driver，union coverage 衡量整体效果

## 架构

```
Knowledge(clang AST) → Planner(策略匹配) → Generation(N 变体)
    → Patching(≤3 次修复) → Coverage(Docker fuzz + llvm-cov)
    → Checkpoint(持久化) → Analyst(诊断缺口)
    → [Supervisor 决策: 继续/停止] → Planner(exploit) → ...
```

### Agent 职责

| Agent | 职责 | 调用 LLM |
|-------|------|----------|
| Knowledge | clang AST 提取：API、类型、调用图、宏 | 否 |
| Planner | Round 1: LLM 场景推理分配 slot；Round 2+: 迭代管理 | 是 |
| Generation | 从零生成（R1）或增量改进（R2+）driver | 是 |
| Patching | 修复编译错误（≤3 次重试） | 是 |
| Coverage | Docker fuzz + llvm-cov + CFG 可达性 | 否 |
| Checkpoint | 序列化状态到 JSON（支持断点恢复） | 否 |
| Analyst | 诊断覆盖率缺口 → 结构化约束 | 是 |

<!-- PLACEHOLDER_REST -->

### 策略库

| 策略 | 风格 |
|------|------|
| `parse-centric` | 将原始 fuzz 字节喂给解析类 API |
| `multi-api-sequence` | 链式调用多个 API，构建状态 |
| `roundtrip` | 解析 → 修改 → 序列化 → 重解析 |
| `targeted-expansion` | 覆盖率反馈驱动（exploit 轮次） |
| `structure-aware` | 构造合法结构化输入 |
| `error-path` | 聚焦错误处理路径 |
| `stateful` | 跨调用维护状态 |
| `resource-boundary` | 内存/缓冲区边界测试 |
| `callback-driven` | 注册回调 + 触发 |
| `differential` | 对比不同 API 实现 |

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
    {"name": "claude-opus-4-6", "api_url": "https://api.example.com/v1", "api_key": "sk-...", "max_tokens": 4096}
  ],
  "defaults": {"timeout": 120, "max_retries": 5}
}
```

或使用环境变量：
```bash
export LLM_MODEL="claude-opus-4-6"
export LLM_API_KEY="sk-..."
export LLM_API_URL="https://api.example.com/v1"
```

### 运行

```bash
PYTHONUNBUFFERED=1 python3 -m src.pipeline.supervisor \
  --target-config targets/cjson.json \
  --max-rounds 5 \
  --target-coverage 100 \
  --fuzz-seconds 60
```

参数说明：
- `--target-config`：目标库配置 JSON 文件路径
- `--max-rounds N`：最大迭代轮数（默认 10）
- `--target-coverage`：目标行覆盖率百分比（默认 100）
- `--fuzz-seconds`：每个变体的 fuzz 时间（秒，默认 60）
- `--max-compile-retries`：每个变体最多编译修复次数（默认 3）
- `--temperature`：LLM 生成温度（默认 0.4，planner 自动 -0.1）
- `--resume`：从最近的 checkpoint 恢复执行

### 输出

```
generated/iterations/cjson/20260521_215659/
├── best_driver.cpp      # 单一最高覆盖率 driver（兼容）
├── best_drivers/        # 每个 slot 的最佳 driver 集合
│   ├── slot_0.cpp
│   ├── slot_1.cpp
│   └── ...
├── variants/            # 最后一轮所有变体源码
├── report.md            # 可读报告
└── report.json          # 结构化指标
generated/iterations/cjson/latest -> 20260521_215659
generated/checkpoints/cjson/round_N.json  # 断点恢复用
```

## Target 配置格式

所有库统一使用 `build_command` 模式：在 Docker 容器内下载源码、编译、安装到 `/opt/bench/<lib>/`。

```json
{
  "library_name": "cjson",
  "header": "/opt/bench/cjson/include/cjson/cJSON.h",
  "build_command": "benchmarks/libs/cjson/build.sh",
  "static_libs": ["/opt/bench/cjson/lib/libcjson.a"],
  "include_dirs": ["/opt/bench/cjson/include/cjson"],
  "source_files": [],
  "coverage_sources": ["/opt/bench/cjson/src"],
  "seed_corpus": "benchmarks/libs/cjson/seed_corpus",
  "dictionary": "benchmarks/libs/cjson/json.dict",
  "language": "C",
  "description": "Lightweight JSON parser for C"
}
```

字段说明：
- `build_command`：Docker 内执行的构建脚本（下载源码 + 编译 + 安装到 /opt/bench/）
- `header`/`include_dirs`/`static_libs`：Docker 内路径（`/opt/bench/...`）
- `coverage_sources`：覆盖率统计的源码目录
- `seed_corpus`/`dictionary`：相对于项目根的路径（通过 /workspace 挂载访问）
```

## 项目结构

```
src/
├── agents/                  # Agent 节点
│   ├── knowledge.py             clang AST 知识提取（无 LLM）
│   ├── planner.py               策略匹配 + slot 管理（无 LLM）
│   ├── generation.py            多策略 fuzz driver 生成
│   ├── patching.py              编译错误修复（≤3 次重试）
│   ├── coverage.py              Docker fuzz + llvm-cov + 可达性分析
│   └── analyst.py               覆盖率缺口诊断 → 结构化约束
├── knowledge/               # 知识层
│   ├── extractor.py             clang AST 解析（4 阶段提取）
│   ├── context_builder.py       per-agent 上下文组装
│   └── grouping.py              API 分组 + 策略匹配规则引擎
├── infra/                   # 基础设施
│   ├── llm_factory.py           ChatOpenAI 工厂（多模型配置）
│   ├── docker_runner.py         Docker 执行：Knowledge/编译/fuzz/覆盖率
│   └── token_tracker.py         全局 token 消耗追踪
├── pipeline/                # 编排层
│   ├── state.py                 PipelineState 类型定义
│   ├── graph.py                 LangGraph DAG + 条件路由
│   ├── supervisor.py            CLI 入口 + 结果保存
│   ├── checkpoint.py            状态持久化与恢复
│   └── report.py                Markdown/JSON 报告生成
├── baselines/               # 对照基线
│   └── naive.py                 单次 LLM 生成（无迭代）
├── config.py                # 项目根路径 + target 配置加载
└── utils.py                 # 通用工具函数
strategies/                  # 10 个策略 .md 文件（YAML frontmatter）
prompts/                     # LLM prompt 模板
targets/                     # 目标库配置 JSON
scripts/                     # Docker 构建和运行脚本
```

## Docker

Ubuntu 22.04 + clang/llvm/lld。所有编译、fuzz、覆盖率收集和 Knowledge 提取均在容器内执行。

**Build 缓存机制**：Knowledge 阶段在容器内构建库并缓存产物到 `generated/build_cache/<lib>/`，
后续 compile/fuzz 容器挂载缓存为只读（`/opt/bench:ro`），跳过重复构建。

**覆盖率收集**：fuzz 使用 `-fork=1` 模式（crash 隔离），之后用 `-runs=0` replay corpus
收集完整的 profraw 数据（fork worker 被 SIGKILL 时 profraw 不会 flush）。

```bash
./scripts/docker_build.sh    # 构建 magic4fdg:latest
```

## 测试

```bash
python3 -m pytest tests/ -v
```

## 许可

本项目仅用于防御性软件测试研究。
