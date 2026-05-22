"""项目配置：根路径定义和目标库配置加载。

提供 ROOT（项目根目录路径）和目标库 JSON 配置文件的加载/校验工具。
Pipeline 中所有路径解析都基于 ROOT。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FIELDS = [
    "library_name",
    "header",
    "include_dirs",
]

LIST_OF_STRINGS_FIELDS = [
    "source_files",
    "include_dirs",
    "static_libs",
    "link_flags",
    "coverage_sources",
]


def load_target_config(
    path: Path,
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    if required_fields is None:
        required_fields = REQUIRED_FIELDS

    try:
        target = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid target config JSON: {path}: {exc}") from exc

    missing = [key for key in required_fields if key not in target]
    if missing:
        raise RuntimeError(f"target config {path} is missing required fields: {', '.join(missing)}")

    if not target.get("source_files") and not target.get("static_libs"):
        raise RuntimeError(f"target config {path} must have source_files or static_libs")

    for key in LIST_OF_STRINGS_FIELDS:
        if key in target:
            if not isinstance(target[key], list) or not all(isinstance(item, str) for item in target[key]):
                raise RuntimeError(f"target config field {key} must be a list of strings")

    return target


def resolve_project_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return ROOT / value
