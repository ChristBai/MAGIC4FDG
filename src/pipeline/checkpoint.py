"""Checkpoint：流水线状态的持久化与恢复。

在每轮 Coverage 执行完毕后保存完整的 PipelineState 到 JSON 文件，
防止超时/崩溃导致数据丢失。支持从最近的 checkpoint 恢复继续执行。

保存位置：generated/checkpoints/<library_name>/round_<N>.json
同时保存变体源码：generated/checkpoints/<library_name>/round_<N>_variants/

恢复逻辑：按文件名排序取最新的 checkpoint，由 supervisor 加载后
传入 graph.invoke 重新执行（从 knowledge 入口开始，round +1 进入 exploit）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from src.config import ROOT
from src.pipeline.state import PipelineState


def _clear_xattr(path: Path) -> None:
    try:
        subprocess.run(["xattr", "-c", str(path)], capture_output=True, timeout=5)
    except Exception:
        pass


def save_checkpoint(state: PipelineState) -> Path:
    """将当前 pipeline 状态序列化为 JSON 文件。"""
    target_name = state["target_config"].get("library_name", "unknown")
    round_num = state.get("round", 0)

    checkpoint_dir = ROOT / "generated" / "checkpoints" / target_name
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    checkpoint_path = checkpoint_dir / f"round_{round_num}.json"

    serializable = _make_serializable(state)
    checkpoint_path.write_text(
        json.dumps(serializable, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    variants_dir = checkpoint_dir / f"round_{round_num}_variants"
    variants_dir.mkdir(exist_ok=True)
    for v in state.get("variants", []):
        if v.get("source_code"):
            fpath = variants_dir / f"{v['id']}.cpp"
            fpath.write_text(v["source_code"], encoding="utf-8")
            _clear_xattr(fpath)

    return checkpoint_path


def load_latest_checkpoint(target_name: str) -> PipelineState | None:
    """加载指定目标库的最新 checkpoint，不存在则返回 None。"""
    checkpoint_dir = ROOT / "generated" / "checkpoints" / target_name
    if not checkpoint_dir.exists():
        return None

    checkpoints = sorted(checkpoint_dir.glob("round_*.json"))
    if not checkpoints:
        return None

    latest = checkpoints[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    return data


def checkpoint_node(state: PipelineState) -> dict:
    """LangGraph 节点：保存 checkpoint，不修改状态。"""
    path = save_checkpoint(state)
    print(f"[Checkpoint] Saved round {state.get('round', 0)} → {path}", flush=True)
    return {}


def _make_serializable(state: PipelineState) -> dict:
    """将 state 转换为 JSON 可序列化的 dict，精简大字段避免文件过大。"""
    result = {}
    skip_in_variants = {"covered_lines", "uncovered_lines", "function_coverage"}

    for key, value in state.items():
        if isinstance(value, Path):
            result[key] = str(value)
        elif key == "all_variants":
            result[key] = [
                {k: v for k, v in var.items() if k not in skip_in_variants}
                for var in (value or [])
            ]
        elif key == "variants":
            result[key] = [
                {k: v for k, v in var.items() if k not in skip_in_variants}
                for var in (value or [])
            ]
        else:
            result[key] = value
    return result
