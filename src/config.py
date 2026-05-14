"""Target config loading and path resolution.

Provides ROOT (project root path) and utilities to load/validate target library
configuration JSON files used by the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FIELDS = [
    "library_name",
    "header",
    "source_files",
    "include_dirs",
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

    for key in ("source_files", "include_dirs"):
        if key in target:
            if not isinstance(target[key], list) or not all(isinstance(item, str) for item in target[key]):
                raise RuntimeError(f"target config field {key} must be a list of strings")

    return target


def resolve_project_path(path: str | Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return ROOT / value
