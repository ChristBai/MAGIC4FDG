#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ACTIVE_DIR="$PROJECT_ROOT/dev/active"

if [ -d "$ACTIVE_DIR" ] && [ "$(ls -A "$ACTIVE_DIR" 2>/dev/null)" ]; then
    TASKS="$(ls -1 "$ACTIVE_DIR" | tr '\n' ', ' | sed 's/,$//')"
    echo "{\"systemMessage\":\"[TASK-MANAGER] 存在活跃任务: ${TASKS}。用户说'继续'时，读取 dev/active/<任务名>/ 下的 plan.md、context.md、tasks.md 恢复工作。\"}"
fi
