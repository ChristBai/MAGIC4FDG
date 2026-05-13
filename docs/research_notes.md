# 文献调研笔记

本文件记录毕设项目（LLM 辅助 fuzz driver 生成 + 细粒度覆盖率反馈闭环）相关的学术文献。

---

### Codex / Pass@k

- **完整标题**: Evaluating Large Language Models Trained on Code
- **作者**: Mark Chen, Jerry Tworek, Heewoo Jun et al. (OpenAI)
- **发表**: arXiv 2107.03374, 2021
- **链接**: https://arxiv.org/abs/2107.03374
- **要点**:
  - 提出 pass@k 指标衡量代码生成的多次采样成功率；Codex pass@1=28.8%, pass@100=70.2%
  - 核心发现：多次采样显著提升成功率但存在边际递减；低温适合 k 小，高温适合 k 大
  - 与本项目关系：为多变体生成策略提供理论基础，但该论文针对通用代码生成，非 fuzz driver 领域
- **标签**: #pass-at-k #code-generation #sampling-strategy

---

### Coverage-Guided Multi-Agent Harness Generation

- **完整标题**: Coverage-Guided Multi-Agent Harness Generation for Java Library Fuzzing
- **作者**: Nils Loose, Nico Winkel, Kristoffer Hempel, Felix Mächtle, Julian Hans, Thomas Eisenbarth (吕贝克大学)
- **发表**: arXiv:2603.08616, 2026.03
- **链接**: https://arxiv.org/html/2603.08616v1
- **要点**:
  - 5 个 ReAct Agent 协作：Research → Generation → Patching → Coverage Analysis → Refinement
  - 创新点：Method-Targeted Coverage（仅在目标方法执行期间记录覆盖率）+ Agent 自主判断终止条件
  - 单变体迭代精化，不做多变体生成；平均 $3.20/harness，982K tokens
  - 与本项目关系：覆盖率分析思路相似（分析 WHY 未覆盖），但它用 Java/JaCoCo/多 Agent，我们用 C/C++/llvm-cov/单流程；它没有多变体融合
  - 本项目优势：多变体比较融合是独特贡献；轻量静态分析替代昂贵的 LLM Agent 判断
- **标签**: #coverage-feedback #multi-agent #Java #harness-generation #iterative-refinement

---

### OSS-Fuzz-Gen

- **完整标题**: Fuzz Target Generation using LLMs (OSS-Fuzz-Gen)
- **作者**: Google OSS-Fuzz Team
- **发表**: Google 官方文档 / 实践报告, 2024
- **链接**: https://google.github.io/oss-fuzz/research/llms/target_generation/
- **要点**:
  - 每个目标函数生成 8-16 个候选 harness，迭代修复编译错误，按覆盖率排序选最优
  - 选择策略：多个 LLM 响应中优先选最长代码；验证 harness 是否调用了目标函数
  - 与本项目关系：也做多候选生成，但只做 best-of-n 选择，不做融合；没有覆盖率反馈迭代
  - 本项目优势：不只选最优，而是分析各变体独特覆盖并融合；加入覆盖率反馈闭环
- **标签**: #fuzz-driver #multi-candidate #best-of-n #Google #OSS-Fuzz

---

### PromptFuzz

- **完整标题**: PromptFuzz: Harnessing Fuzzing Techniques for Robust Testing of Prompt Injection in LLMs (注：此处指 CCS'24 的 fuzz driver 生成工具 PromptFuzz)
- **作者**: Yunlong Lyu et al.
- **发表**: CCS 2024
- **链接**: (待补充具体链接)
- **要点**:
  - 编译失败直接丢弃，不做修复；有覆盖率反馈但粒度为 API 组合级
  - 与本项目关系：反馈粒度粗（哪些 API 覆盖率低），不涉及代码行/分支级
  - 本项目优势：细粒度行级/分支级反馈 + 可达性分析
- **标签**: #fuzz-driver #coverage-feedback #API-level #no-compile-fix

---

### CKGFuzzer

- **完整标题**: LLM-Based Fuzz Driver Generation Enhanced By Code Knowledge Graph
- **作者**: (待补充)
- **发表**: arXiv:2411.11532, 2024
- **链接**: https://arxiv.org/abs/2411.11532v2
- **要点**:
  - 利用代码知识图谱增强 LLM 生成 fuzz driver；5 轮编译修复；有覆盖率反馈
  - 反馈粒度：API 组合级（哪些 API 序列覆盖率低）
  - 与本项目关系：编译修复 + 覆盖率反馈的基本框架相似，但反馈粒度粗
  - 本项目优势：行级/分支级反馈 + 可达性分析 + 多变体融合
- **标签**: #fuzz-driver #knowledge-graph #compile-fix #coverage-feedback #API-level

---

### Constraint-based Dual Scheduling

- **完整标题**: Constraint-based Fuzz Driver Generation with Dual Scheduling
- **作者**: (待补充)
- **发表**: arXiv:2507.18289, 2025
- **链接**: https://arxiv.org/abs/2507.18289
- **要点**:
  - 基于约束求解的 fuzz driver 生成，双调度机制：API 序列调度 + 资源调度
  - 覆盖率对比：分别达到 CKGFuzzer 的 1.62x、PromptFuzz 的 1.50x、OSS-Fuzz 的 1.89x
  - 与本项目关系：直接竞品，同为 fuzz driver 自动生成工具；采用静态约束求解而非 LLM 迭代改进
  - 本项目优势：LLM 驱动的覆盖率反馈闭环支持自适应改进，而非静态约束求解的一次性生成；多变体融合可组合不同路径的覆盖
- **标签**: #fuzz-driver #constraint-solving #static-analysis #dual-scheduling

---

### TitanFuzz

- **完整标题**: Fuzzing Deep-Learning Libraries via Large Language Models
- **作者**: Yinlin Deng, Chunqiu Steven Xia, Haoran Peng, Chenyuan Yang, Lingming Zhang et al.
- **发表**: ISSTA 2023
- **链接**: https://arxiv.org/abs/2212.14834
- **要点**:
  - 利用 LLM 对 API 用法模式的内在知识，生成类型正确、语义有效的 API 调用序列，对 TensorFlow/PyTorch 进行 fuzz；在 TF/PyTorch 上分别比 SOTA 高 30.38%/50.84% 覆盖率，发现 65 个 bug
  - 与本项目关系：同样利用 LLM 生成 fuzz 输入，验证了 LLM 理解 API 语义的能力；但目标是 Python DL 库 API 级 fuzzing，非 C/C++ 库函数级 driver 生成
  - 本项目优势：TitanFuzz 无覆盖率反馈闭环、无多变体融合、无编译修复；本项目针对 C/C++ 做细粒度行级反馈 + 多变体融合，技术路线互补但不重叠
- **标签**: #LLM #fuzz-driver #DL-library #Python #no-coverage-feedback

---

### Temperature Scaling for Test-Time Reasoning

- **完整标题**: On the Role of Temperature Sampling in Test-Time Scaling
- **作者**: (待补充)
- **发表**: arXiv 2510.02611, 2025
- **链接**: https://arxiv.org/abs/2510.02611
- **要点**:
  - 核心发现：不同温度解决不同子集的问题，没有单一最优温度；单温度下增加样本数到 1024→13312 不再解决新问题
  - 多温度采样（0.0~1.2 分布）比单温度平均提升 7.3 个百分点；代码生成（LiveCodeBench）上 Qwen3-8B 从 32.6%→40.0%
  - 低温（0.1~0.3）可排除，因为低温 trace 在高温下也能生成
  - 与本项目关系：直接支撑多变体生成策略——不同温度生成的 fuzz driver 覆盖解空间不同区域，理论上应组合多温度而非固定单一温度
  - 对本项目启示：建议采用 3~4 个温度档位（如 0.4, 0.7, 1.0, 1.2）生成变体，而非单一温度多次采样
- **标签**: #multi-variant #sampling-strategy #temperature #test-time-scaling #code-generation

---

### Test-Time Compute Optimal Scaling

- **完整标题**: Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters
- **作者**: (待补充)
- **发表**: arXiv 2408.03314, 2024
- **链接**: https://arxiv.org/abs/2408.03314
- **要点**:
  - 对模型有一定成功率的问题，test-time compute 可超越 14 倍大的模型
  - 计算最优策略比均匀 Best-of-N 效率高 4 倍（64 样本 ≈ 256 均匀采样）
  - 难度依赖：简单问题少量样本即可，中等难度受益最大，极难问题增加样本帮助有限
  - 与本项目关系：fuzz driver 生成属于"中等难度"任务（模型有一定成功率但非 trivial），正好处于 test-time scaling 收益最大的区间
  - 对本项目启示：应根据目标函数复杂度自适应调整变体数量，简单函数 3~5 个，复杂函数 10~20 个
- **标签**: #test-time-scaling #best-of-n #compute-optimal #multi-variant

---

### Multi-LLM Repeated Sampling

- **完整标题**: Do We Truly Need So Many Samples? Multi-LLM Repeated Sampling Efficiently Scales Test-Time Compute
- **作者**: (待补充)
- **发表**: arXiv 2504.00762, 2025
- **链接**: https://arxiv.org/abs/2504.00762
- **要点**:
  - 使用 2~3 个不同模型/策略时，仅需 16~35 个样本即可达到单模型 512 个样本的效果（14 倍效率提升）
  - 单模型 self-consistency 快速遇到瓶颈；2~6 个模型中 2~3 个即为最优，更多反而下降
  - 核心信号：一致性意味着正确，混乱意味着错误（consistency implies correct）
  - 与本项目关系：支撑"多提示词变体"策略——不同 prompt 相当于不同"模型视角"，比同一 prompt 重复采样更高效
  - 对本项目启示：3~5 个结构不同的 prompt 模板 × 少量采样，优于单一 prompt × 大量采样
- **标签**: #multi-variant #sampling-strategy #multi-model #efficiency

---

### Stratified Sampling for LLM Diversity

- **完整标题**: Diversifying Language Model Generation with Stratification
- **作者**: (待补充)
- **发表**: arXiv 2410.09038, 2024
- **链接**: https://arxiv.org/abs/2410.09038
- **要点**:
  - 提出分层采样：将生成空间结构化划分为不同层（strata），每层独立采样，确保覆盖多样性
  - 比朴素 temperature sampling 产生更高的输出多样性，同时保持质量
  - 与本项目关系：为"多提示词模板"提供理论支撑——不同 prompt 结构（如强调边界条件 vs 强调 API 组合 vs 强调错误处理）相当于对解空间的分层
  - 对本项目启示：设计 prompt 变体时应有意识地覆盖不同"层"（不同编码策略/侧重点），而非仅做措辞微调
- **标签**: #multi-variant #sampling-strategy #diversity #stratification
