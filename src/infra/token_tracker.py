"""Token 用量追踪：记录 pipeline 中所有 LLM 调用的 token 消耗。

提供全局 TokenTracker，按 agent 和 model 累计 prompt/completion token 数。
所有 agent 通过 get_tracker().record() 记录，最终输出到 report。
"""

from __future__ import annotations

import threading


class TokenTracker:
    """线程安全的 token 用量累加器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[dict] = []

    def record(self, agent: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self._records.append({
                "agent": agent,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            })

    def summary(self) -> dict:
        with self._lock:
            total_prompt = sum(r["prompt_tokens"] for r in self._records)
            total_completion = sum(r["completion_tokens"] for r in self._records)
            by_agent: dict[str, dict] = {}
            for r in self._records:
                a = r["agent"]
                if a not in by_agent:
                    by_agent[a] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
                by_agent[a]["prompt_tokens"] += r["prompt_tokens"]
                by_agent[a]["completion_tokens"] += r["completion_tokens"]
                by_agent[a]["calls"] += 1
            return {
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
                "by_agent": by_agent,
            }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


_global_tracker = TokenTracker()


def get_tracker() -> TokenTracker:
    return _global_tracker


def extract_token_usage(response) -> tuple[int, int]:
    """从 langchain AIMessage 响应中提取 prompt/completion token 数。"""
    meta = getattr(response, "response_metadata", {}) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return prompt, completion
