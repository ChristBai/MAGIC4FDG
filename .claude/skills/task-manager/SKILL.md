---
name: task-manager
description: 跨会话任务持久化管理。当用户发布多步骤开发任务、说"继续"恢复之前的工作、或标记任务完成时触发。也在上下文压缩前自动触发以保存进度。只要涉及"开始任务"、"新任务"、"继续"、"恢复"、"任务完成"、"做完了"的对话，或用户给出一个预计需要多步实现的开发需求，都应使用此 skill。即使用户没有明确说"创建任务"，只要需求复杂度足够（>3步、可能跨会话），也应主动触发。
---

# Task Manager

LLM 的上下文窗口有限，复杂任务经常跨越多个会话。这个 skill 通过文件系统持久化任务状态，让工作可以在任意会话中断点恢复，不丢失进度和上下文。

## 目录结构

```
dev/
├── active/          ← 进行中的任务
│   └── <task-name>/
│       ├── <task-name>-plan.md
│       ├── <task-name>-context.md
│       └── <task-name>-tasks.md
└── done/            ← 已完成的任务（归档）
```

## Hooks

本 skill 配套两个 hook 脚本（位于 `.claude/skills/task-manager/scripts/`）：

- **SessionStart** → `session_start_check.sh`：新会话启动时检测 `dev/active/` 是否有活跃任务，提示用户可以说"继续"恢复
- **PreCompact** → `pre_compact_save.sh`：上下文压缩前提醒保存进度到文件

这些 hook 在 `.claude/settings.local.json` 中注册。

## 工作流程

### 创建任务

当用户给出一个复杂开发需求时：

1. 确定任务名 — 简短英文 kebab-case（如 `multi-agent-impl`、`coverage-feedback-loop`）
2. 创建 `dev/active/<task-name>/` 目录
3. 写入三个初始文件（格式见下方模板）
4. 开始执行第一步

任务名的选择原则：能让未来的自己一眼看出这是什么任务。

### 执行中

每完成一个有意义的步骤，更新 `plan.md` 中的进度标记。这样即使会话中断，下次恢复时能立即看到做到哪了。

### 上下文保存

当收到 PreCompact hook 的提醒（或感觉上下文快满了）时，把以下信息写入文件：
- `context.md`：当前状态、关键决策、重要发现、踩过的坑
- `tasks.md`：剩余步骤的具体清单
- `plan.md`：更新进度标记

写入的信息应该足够让一个全新的 Claude 会话仅凭这三个文件就能无缝接手工作。

### 恢复任务

当用户说"继续"时：
1. 列出 `dev/active/` 中的任务（如果只有一个就直接恢复）
2. 读取该任务的三个文件
3. 从 `tasks.md` 中的第一个未完成项开始工作

### 完成任务

所有步骤完成后：
1. `mv dev/active/<task-name> dev/done/<task-name>`
2. 告知用户

## 文件模板

### plan.md

```markdown
# <task-name> 实施计划

创建: YYYY-MM-DD
更新: YYYY-MM-DD HH:MM

## 目标
一句话描述要达成什么

## 步骤
- [x] 已完成的步骤
- [>] 当前正在进行的步骤
- [ ] 尚未开始的步骤
```

### context.md

```markdown
# <task-name> 上下文

## 当前状态
正在做什么，做到哪里了

## 关键决策
- 选择了 X 方案，因为 Y

## 重要发现
- 发现 A 会影响 B

## 注意事项
- 某个坑或约束
```

### tasks.md

```markdown
# <task-name> 剩余任务

## 进行中
- [ ] 当前子任务的具体描述

## 待完成
- [ ] 下一步
- [ ] 再下一步

## 已完成
- [x] 做完的事
```

## 何时不创建任务

简单请求不需要任务管理的开销：
- 一次性修复（改个 bug、加个字段）
- 纯问答（"这个函数做什么"）
- 预计 3 步以内能完成的小改动

任务管理的价值在于跨会话连续性 — 如果一个会话内就能做完，直接做就好。
