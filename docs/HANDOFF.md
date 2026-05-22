# FuzzForge v2 重构交接文档

> 生成时间: 2026-05-20（最后更新: 2026-05-22）
> 分支: main
> 上次成功运行: cjson 86.6% union coverage, harfbuzz 33.2% union coverage

---

## 1. 项目目标

### 最终目标
毕设项目：构建一个覆盖率引导的多 Agent 模糊测试驱动生成系统（FuzzForge），通过细粒度覆盖率反馈闭环，自动为 C/C++ 库生成高覆盖率的 LibFuzzer fuzz driver。区别于 PromptFuzz/CKGFuzzer 的核心创新点是迭代式覆盖率反馈 + 多策略探索。

### 当前正在解决的核心问题
v1 架构（Research → Generation × N → Patching → Coverage → Refinement）在 12 库实验中暴露严重问题：
- Refinement 融合产出"中庸平均"（覆盖率极差收窄，多样性丧失）
- 策略排序跨轮次稳定（库级特征），后续轮次多策略并行浪费资源
- Research 太浅（只读头文件写摘要），无结构化知识累积
- 超时 kill 丢失数据（无中间 checkpoint）

**v2 重构已完成**：Explore → Exploit 架构，用 Analyst 替代 Refinement，用 clang AST 替代 Research，加入 Checkpoint 和 Knowledge 累积。所有操作（Knowledge/编译/fuzz/覆盖率）在 Docker 容器内执行，build 缓存机制避免重复构建。

---

## 2. 当前进展

### 已完成的工作
| 组件 | 状态 | 说明 |
|------|------|------|
| `strategies/` (10个策略文件) | ✅ 完成 | YAML frontmatter + 策略 prompt body |
| `src/knowledge/extractor.py` | ✅ 完成+修复 | Docker 内 clang AST 提取，不再在宿主机执行 |
| `src/knowledge/context_builder.py` | ✅ 完成 | 按 agent 角色裁剪知识子集 |
| `src/pipeline/state.py` | ✅ 完成 | v2 TypedDicts 全部定义 |
| `src/pipeline/checkpoint.py` | ✅ 完成 | 序列化/反序列化/LangGraph node |
| `src/pipeline/graph.py` | ✅ 完成 | v2 DAG + 条件路由 |
| `src/pipeline/supervisor.py` | ✅ 完成 | 新 CLI + checkpoint 恢复 |
| `src/agents/knowledge.py` | ✅ 完成 | 调用 extractor，无 LLM |
| `src/agents/planner.py` | ✅ 完成 | explore/exploit 双模式 |
| `src/agents/analyst.py` | ✅ 完成+修复 | 使用历史最佳 driver 分析 |
| `src/agents/generation.py` | ✅ 完成 | from-scratch + incremental 双模式 |
| `src/agents/coverage.py` | ✅ 完成 | 更新为 v2 round-based + slot 更新 |
| `src/agents/patching.py` | ✅ 兼容 | 无需修改，接口已兼容 v2 |
| `llm_config.json` | ✅ 完成 | 单模型 opus-4-6, max_tokens=4096 |
| `prompts/planner_prompt.txt` | ✅ 完成 | |
| `prompts/analyst_prompt.txt` | ✅ 完成 | |
| `src/pipeline/report.py` | ✅ 修复 | iteration → round |

### 已验证
- 所有 v2 模块可正确导入（`from src.pipeline.graph import compile_graph` 等）
- LangGraph 编译成功，节点列表正确：`knowledge, planner, generation, patching, coverage, checkpoint, analyst`
- **cjson 端到端运行成功**（1轮，30s fuzz）：
  - 12 个 variant 全部编译通过，覆盖率 32-54%
  - Union coverage: 86.6% (2006/2316 lines)
  - 总 token: ~400K
- **harfbuzz 端到端运行成功**（1轮，30s fuzz）：
  - 20 个 variant 全部编译通过，覆盖率 0-14%
  - Union coverage: 33.2% (12077/36418 lines)
  - Knowledge: 378 APIs, 245 types
- **Docker build cache 机制验证通过**：
  - Knowledge 容器构建后缓存到 `generated/build_cache/<lib>/`
  - 后续运行挂载缓存跳过 build，Knowledge 提取正常
- **Coverage 0% bug 已修复**：corpus replay 确保 profraw 完整

### 被放弃的方案
| 方案 | 原因 |
|------|------|
| 双模型 (sonnet + opus) | 实验显示无普遍占优模型，简化变量 |
| Tree-sitter 知识提取 | 无法处理宏展开、typedef 链，改用 clang AST |
| Refinement 代码融合 | 产出中庸平均，改为 Analyst 只输出约束 |
| 温度递增 schedule | v2 用固定 0.4，策略多样性由 Planner 控制 |
| 知识图谱 (Neo4j) | 过重，API 数量 <500 可全量放入 context |

---

## 3. 当前代码状态

### 分支/目录
- 分支: `main`
- 工作目录: `/Users/christbai/Documents/New project/FuzzForge`
- **所有修改均未提交**

### 已修改文件 (git tracked)
| 文件 | 改动 |
|------|------|
| `llm_config.json` | 去掉 sonnet + variant_matrix，单模型 opus-4-6, max_tokens 2048→4096, retries 3→5 |
| `src/pipeline/state.py` | 完全重写：新增 APIEntry, KnowledgeStore, StrategySelection, HarnessSlot, VariantConfig; PipelineState 新增 round/knowledge/harness_slots 等字段 |
| `src/pipeline/graph.py` | 完全重写：7 节点 DAG，条件路由 _route_after_patching + _route_after_analyst |
| `src/pipeline/supervisor.py` | 完全重写：新 CLI 参数 (--max-rounds, --resume)，checkpoint 恢复 |
| `src/pipeline/report.py` | 小改：iteration → round |
| `src/agents/generation.py` | 完全重写：from-scratch + incremental 双模式，策略库加载 |
| `src/agents/coverage.py` | 重写 coverage_node：round-based，harness_slots 更新，plateau 检测 |
| `src/agents/refinement.py` | 不确定改了什么（v2 不再使用此文件，但 git 显示有修改） |
| `src/infra/llm_factory.py` | 小改：fallback config 去掉 variant_matrix |
| `scripts/run_all_pipeline.sh` | 不确定（可能是实验脚本更新） |

### 新增文件 (untracked)
| 文件/目录 | 说明 |
|-----------|------|
| `strategies/` (11 files) | 10 个策略 .md + README.md |
| `src/knowledge/__init__.py` | 包初始化 |
| `src/knowledge/extractor.py` | clang AST 解析 |
| `src/knowledge/context_builder.py` | 按 agent 裁剪知识 |
| `src/agents/knowledge.py` | Knowledge agent node |
| `src/agents/planner.py` | Planner agent node |
| `src/agents/analyst.py` | Analyst agent node |
| `src/pipeline/checkpoint.py` | Checkpoint 序列化 |
| `prompts/planner_prompt.txt` | Planner LLM prompt |
| `prompts/analyst_prompt.txt` | Analyst LLM prompt |
| `benchmarks/` | 横向实验数据 |
| `data/oss_fuzz_official/` | OSS-Fuzz 官方 driver 参考 |
| `docs/` | 交接文档 + 毕设论文 |

> 注：`prompts/planner_prompt.txt` 已删除（Planner 是纯规则引擎，不调 LLM）。
> `src/agents/refinement.py` 和 `src/agents/research.py` 已删除（v1 残余）。

### 不要覆盖的内容
- `generated/checkpoints/cjson/` — 已有成功运行的 checkpoint 数据
- `generated/iterations/cjson/20260520_145743/` — 首次 v2 成功运行结果
- `strategies/` 目录 — 10 个精心设计的策略文件
- `src/knowledge/` 包 — 已验证可正确提取 78 个 cjson API

---

## 4. 关键设计决策

### 架构
- **Explore → Exploit**: Round 1 用 3 个不同策略生成 3 个 harness（explore），Round 2+ 只保留最佳 slot 做增量改进（exploit）
- **HarnessSlot 概念**: 每个 slot 是一个持久化的 harness 演化轨迹，跨轮次保持 best_source 和 coverage_history
- **Analyst 不产出代码**: 只输出结构化约束（constraints + uncovered_clusters），由 Generator 根据约束做增量改进
- **Knowledge 累积**: constraints_discovered / positive_patterns / negative_patterns 跨轮次增长

### 数据结构
```python
# 核心 TypedDicts (src/pipeline/state.py)
PipelineState.round: int          # 当前轮次，analyst 递增
PipelineState.harness_slots: list[HarnessSlot]  # 持久化 slot
PipelineState.variants: list[DriverVariant]      # 当前轮的变体（每轮被替换）
PipelineState.best_coverage: float  # 项目级 union coverage（只升不降）
PipelineState.best_driver: str      # 单一最佳 driver（兼容）
PipelineState.best_drivers: dict[str, str]  # 每个 slot 的最佳 driver 集合
PipelineState.coverage_plateau_count: int  # 连续无提升轮次
```

### 接口约定
- `create_llm(temperature=X)` — 不传 model 时用 config 第一个模型
- `_docker_run(command, timeout, memory, run_id, extra_volumes)` — Docker 执行接口
- `build_and_extract(target_config)` — Knowledge 阶段：Docker 内 build + clang AST
- `compile_driver(source, config)` — 编译 fuzz driver（自动挂载 build cache）
- `run_fuzz_with_coverage(source, config, fuzz_seconds)` — fuzz + corpus replay + 覆盖率
- 策略文件格式: YAML frontmatter (`id`, `name`, `applicable_when`, `incompatible_with`, `best_for`) + Markdown body
- Agent node 返回 dict，LangGraph 合并到 state（list 字段是替换而非追加）

### 停止条件 (`_route_after_analyst`)
1. `best_coverage >= target_coverage` → END
2. `round >= max_rounds` → END
3. `coverage_plateau_count >= 3` → END
4. 无 active slots → END
5. 否则 → 继续到 planner

### 不能破坏的行为
- Docker 容器内编译/fuzz/coverage 的隔离
- Fork mode + ASAN_OPTIONS 的 crash tolerance
- llm_factory 的 5 次指数退避重试
- Checkpoint 在每轮 Coverage 后自动保存

---

## 5. 已解决的历史问题

| 问题 | 根因 | 修复 |
|------|------|------|
| Coverage 0% (fork mode) | fork worker 被 SIGKILL，profraw 未 flush | 添加 corpus replay (`-runs=0`) 收集完整覆盖率 |
| Knowledge 二次运行 0 APIs | `build_and_extract()` 不复用 build cache，build 失败时 `set -e` 跳过 AST | 检测 cache 存在时挂载并跳过 build |
| Checkpoint 写入超时 | macOS xattr + Docker VirtioFS 冲突 | 添加 `_clear_xattr()` |
| `llvm-cov export -summary-only` 无行级数据 | 只输出 totals，无 per-file segments | 移除 `-summary-only` flag |
| Round 2 覆盖率回退 | Analyst 使用当前轮变体而非历史最佳 | 改用 `state["best_driver"]` 分析 |

### 当前待解决

1. **Report 只显示最后一轮 variants** — `report.json` 不累积历史轮次数据
2. **多轮迭代收敛行为未充分验证** — 需要 3+ 轮实验观察 exploit 效果
3. **12 库全量实验** — 需要用 v2 架构重跑所有库，对比 v1/PromptFuzz/oss-fuzz-gen

---

## 6. 测试与验证

### 已运行的命令
```bash
# 模块导入测试 — 全部通过
python3 -c "from src.pipeline.graph import compile_graph; g = compile_graph(); print(list(g.get_graph().nodes.keys()))"
# 输出: ['__start__', 'knowledge', 'planner', 'generation', 'patching', 'coverage', 'checkpoint', 'analyst', '__end__']

# Knowledge 提取测试 — 修复后通过
python3 -c "from src.knowledge.extractor import extract_knowledge; ..."
# 输出: APIs: 78, Types: 63, Call edges: 346

# cjson 端到端测试 — 通过（exit code 1 = 未达标，预期行为）
python3 -m src.pipeline.supervisor --target-config targets/cjson.json --max-rounds 2 --target-coverage 70 --fuzz-seconds 15
# 结果: Round 1 最佳 65.4%, Round 2 回退到 45.6%, best_coverage 保持 65.4%
```

### 通过的验证
- [x] 所有 v2 模块可导入
- [x] LangGraph 编译成功
- [x] Knowledge 正确提取 78 个 cjson API（修复后）
- [x] Planner 正确选择 3 个策略
- [x] Generation 生成 3 个变体
- [x] Patching 全部编译成功（3/3 first try）
- [x] Coverage 正确计算覆盖率
- [x] Checkpoint 正确保存
- [x] Analyst 正确输出约束
- [x] Round 2 正确进入 exploit 模式
- [x] Pipeline 正常终止（max_rounds 达到）

### 未通过/未验证
- [ ] 修复后的 Knowledge (78 APIs) 对 Round 2 增量改进的效果
- [ ] fuzz-seconds=60 的覆盖率提升
- [ ] 多轮迭代（5轮）的收敛行为
- [ ] `--resume` checkpoint 恢复功能
- [ ] 其他库（非 cjson）的兼容性

### 如何复现
```bash
cd "/Users/christbai/Documents/New project/FuzzForge"
# 确保 Docker 运行且 fuzzforge:latest 镜像存在
docker images fuzzforge:latest
# 运行 pipeline
PYTHONUNBUFFERED=1 .venv/bin/python3 -m src.pipeline.supervisor \
  --target-config targets/cjson.json --max-rounds 5 --target-coverage 100 --fuzz-seconds 60
```

### 如何确认修复成功
- Round 1: 3 个变体全部编译，覆盖率 >40%
- Knowledge: 打印 "Done: 78 APIs" (不是 0)
- Round 2: 覆盖率 >= Round 1 最佳（不回退）
- 最终 best_coverage > 65.4%（超过首次运行）

---

## 7. 后续任务清单

### P0: 立即需要（用户明确要求）
1. **运行 cjson 完整测试**
   - 文件: 无需修改
   - 命令: `PYTHONUNBUFFERED=1 python3 -m src.pipeline.supervisor --target-config targets/cjson.json --max-rounds 5 --target-coverage 100 --fuzz-seconds 60`
   - 验证: pipeline 完成，best_coverage > 65.4%，Round 2+ 不回退

### P1: 影响正确性的 bug
2. **variants 列表不累积问题**
   - 文件: `src/agents/generation.py` 的 `generation_node` 返回值
   - 预期改动: 在返回 variants 时合并 `state.get("variants", [])` 中历史变体（标记为非当前轮）
   - 或者: 在 `src/pipeline/state.py` 中用 `Annotated[list[DriverVariant], operator.add]` 定义 reducer
   - 验证: Round 2 的 report.json 包含所有轮次的 variants
   - **不确定**: LangGraph 对 TypedDict 的 Annotated reducer 支持程度

3. **report.json 只显示最后一轮**
   - 文件: `src/pipeline/supervisor.py` 的 `_save_results`
   - 预期改动: 从 checkpoint 目录加载所有轮次 variants 合并到 report
   - 验证: report.json 的 variants 数组包含所有轮次数据

### P2: 提升效果
4. **incremental prompt 质量优化**
   - 文件: `src/agents/generation.py` 的 `_build_incremental_prompt`
   - 问题: 当前 prompt 可能导致 LLM 重写而非增量改进
   - 预期改动: 强调 "保留现有代码，只添加新路径"，提供更具体的 API 调用示例
   - 验证: Round 2 覆盖率 >= Round 1

5. **Planner exploit 模式增强**
   - 文件: `src/agents/planner.py` 的 `_plan_exploit`
   - 问题: 当前固定用 targeted-expansion，不考虑 Analyst 输出
   - 预期改动: 根据 coverage_analysis 的 uncovered_clusters 类型选择更合适的策略
   - 验证: 不同类型的 gap 使用不同策略

6. **Coverage node 传递 best variant 的 uncovered_lines 给 Analyst**
   - 文件: `src/agents/coverage.py`
   - 问题: 如果 Round 2 变体覆盖率低于历史最佳，Analyst 拿到的 uncovered_lines 数据不准确
   - 预期改动: 在 coverage_node 中，如果历史最佳更好，重新用 best_driver 跑一次 coverage 获取最新 uncovered_lines
   - 验证: Analyst 分析的 uncovered_lines 对应 65.4% 的 driver 而非 45.6% 的
   - **不确定**: 是否值得额外的 fuzz 时间开销

### P3: 功能完善
7. **Git 提交 v2 重构**
   - 所有修改 + 新文件一次性提交
   - commit message: `refactor: implement v2 Explore→Exploit architecture with Knowledge/Planner/Analyst agents`

8. **12 库全量实验**
   - 用 v2 架构重跑 12 库，对比 v1 数据
   - 需要 `scripts/run_all_pipeline.sh` 适配新 CLI 参数

9. **单元测试**
   - `tests/test_extractor.py`: 验证 cjson.h 提取结果
   - `tests/test_planner.py`: 验证策略选择逻辑
   - `tests/test_graph.py`: 验证 DAG 路由

---

## 附录: 首次 v2 运行数据 (cjson, 2026-05-20)

```
配置: max_rounds=2, target_coverage=70%, fuzz_seconds=15
Knowledge: 0 APIs (bug), 63 types, 346 call edges

Round 1 (explore):
  slot_0 parse-centric:       47.0% line, first-try compile
  slot_1 roundtrip:           65.4% line, first-try compile  ← BEST
  slot_2 multi-api-sequence:  58.8% line, first-try compile

Round 2 (exploit slot_1 with targeted-expansion):
  slot_1 targeted-expansion:  45.6% line (regression)

Final: best_coverage=65.4%, tokens=67,297, exit_code=1 (target not reached)
```
