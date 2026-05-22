"""LLM 工厂：从 llm_config.json 创建 ChatOpenAI 实例。

参数解析优先级：
1. 函数显式参数
2. llm_config.json（按 model name 匹配）
3. 环境变量（LLM_MODEL, LLM_API_KEY, LLM_API_URL）
4. 默认值

支持多 API key 轮询：models 数组中同名模型的多个条目会被合并为 key 池，
每次 create_llm() 调用自动轮换 key，分散并发压力。
"""

from __future__ import annotations

import itertools
import json
import os
import threading
from pathlib import Path

os.environ.setdefault("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")

DEFAULT_MAX_TOKENS = 2048
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_TIMEOUT = 120

_config_cache: dict | None = None
_key_pools: dict[str, itertools.cycle] = {}
_key_pool_lock = threading.Lock()


def _load_config() -> dict:
    """加载 llm_config.json 配置（带缓存）。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = Path(__file__).resolve().parents[2] / "llm_config.json"
    if config_path.exists():
        _config_cache = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        _config_cache = {"models": [], "variant_matrix": {}, "defaults": {}}
    return _config_cache


def _build_key_pool(model_name: str) -> itertools.cycle | None:
    """为指定模型构建 API key 轮询池。"""
    config = _load_config()
    entries = [m for m in config.get("models", []) if m["name"] == model_name]
    if not entries:
        return None

    keys = []
    for entry in entries:
        if "api_key" in entry:
            keys.append({
                "api_key": entry["api_key"],
                "api_url": entry.get("api_url", ""),
                "max_tokens": entry.get("max_tokens", DEFAULT_MAX_TOKENS),
            })
        elif "api_key_env" in entry:
            key_val = os.environ.get(entry["api_key_env"], "")
            if key_val:
                keys.append({
                    "api_key": key_val,
                    "api_url": entry.get("api_url", ""),
                    "max_tokens": entry.get("max_tokens", DEFAULT_MAX_TOKENS),
                })

    if not keys:
        return None
    return itertools.cycle(keys)


def _next_key(model_name: str) -> dict | None:
    """线程安全地从 key 池中取下一个 key。"""
    with _key_pool_lock:
        if model_name not in _key_pools:
            pool = _build_key_pool(model_name)
            if pool is None:
                return None
            _key_pools[model_name] = pool
        return next(_key_pools[model_name])


def _find_model_config(model_name: str) -> dict | None:
    """按名称查找第一个模型配置（用于获取默认参数）。"""
    config = _load_config()
    for m in config.get("models", []):
        if m["name"] == model_name:
            return m
    return None


def create_llm(
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """创建 ChatOpenAI 实例，自动从 key 池轮询分配。

    多个同名模型条目的 api_key 会被合并为轮询池，
    每次调用自动切换到下一个 key，提升并发吞吐。
    """
    config = _load_config()
    defaults = config.get("defaults", {})

    models_list = config.get("models", [])
    default_model = models_list[0]["name"] if models_list else "gpt-4o"
    resolved_model = model or os.environ.get("LLM_MODEL", default_model)

    # 从 key 池轮询获取 key（线程安全）
    pool_entry = _next_key(resolved_model) if not api_key else None

    if pool_entry and not api_key:
        resolved_api_key = pool_entry["api_key"]
        resolved_base_url = base_url or pool_entry["api_url"] or os.environ.get("LLM_API_URL", "")
        max_tokens = pool_entry.get("max_tokens", max_tokens)
    else:
        model_config = _find_model_config(resolved_model)
        if model_config:
            resolved_base_url = base_url or model_config.get("api_url") or os.environ.get("LLM_API_URL", "")
            if "api_key" in model_config:
                resolved_api_key = api_key or model_config["api_key"]
            else:
                key_env = model_config.get("api_key_env", "LLM_API_KEY")
                resolved_api_key = api_key or os.environ.get(key_env, os.environ.get("LLM_API_KEY", ""))
            max_tokens = model_config.get("max_tokens", max_tokens)
        else:
            resolved_base_url = base_url or os.environ.get("LLM_API_URL", "https://api.openai.com/v1")
            resolved_api_key = api_key or os.environ.get("LLM_API_KEY", "")

    timeout = defaults.get("timeout", DEFAULT_TIMEOUT)
    retries = defaults.get("max_retries", DEFAULT_RETRY_ATTEMPTS)

    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        base_url=resolved_base_url,
        model=resolved_model,
        api_key=resolved_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=retries,
    )
    return llm
