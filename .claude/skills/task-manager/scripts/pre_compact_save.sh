#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ACTIVE_DIR="$PROJECT_ROOT/dev/active"

if [ -d "$ACTIVE_DIR" ] && [ "$(ls -A "$ACTIVE_DIR" 2>/dev/null)" ]; then
    TASKS="$(ls -1 "$ACTIVE_DIR" | tr '\n' ', ' | sed 's/,$//')"
    echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PreCompact\",\"additionalContext\":\"[TASK-MANAGER] 活跃任务: ${TASKS}。压缩前请更新 dev/active/ 中对应任务的 context.md（当前状态和关键发现）和 tasks.md（剩余步骤），确保新会话能无缝恢复。\"}}"
fi
