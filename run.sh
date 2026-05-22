#!/bin/bash
# FuzzForge 启动脚本：确保使用 .venv 中的 Python
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/python" -m src.pipeline.supervisor "$@"
