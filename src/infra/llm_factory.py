"""LLM factory for creating ChatOpenAI instances from config file."""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain_openai import ChatOpenAI

os.environ.setdefault("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")

DEFAULT_MAX_TOKENS = 2048
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_TIMEOUT = 120

_config_cache: dict | None = None


def _load_config() -> dict:
    """Load LLM config from llm_config.json."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = Path(__file__).resolve().parents[2] / "llm_config.json"
    if config_path.exists():
        _config_cache = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        _config_cache = {"models": [], "variant_matrix": {}, "defaults": {}}
    return _config_cache


def _find_model_config(model_name: str) -> dict | None:
    """Find model configuration by name from config file."""
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
    """Create a ChatOpenAI instance.

    Resolution order for each parameter:
    1. Explicit argument
    2. Config file (llm_config.json) matched by model name
    3. Environment variables (LLM_API_URL, LLM_MODEL, LLM_API_KEY)
    4. Defaults
    """
    config = _load_config()
    defaults = config.get("defaults", {})

    models_list = config.get("models", [])
    default_model = models_list[0]["name"] if models_list else "gpt-4o"
    resolved_model = model or os.environ.get("LLM_MODEL", default_model)
    model_config = _find_model_config(resolved_model)

    if model_config:
        resolved_base_url = base_url or model_config.get("api_url") or os.environ.get("LLM_API_URL", "")
        # api_key: direct value; api_key_env: environment variable name
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

    llm = ChatOpenAI(
        base_url=resolved_base_url,
        model=resolved_model,
        api_key=resolved_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return llm.with_retry(stop_after_attempt=retries)


def create_variant_llm(model: str, temperature: float) -> ChatOpenAI:
    """Create an LLM instance for a specific variant configuration.

    Looks up model in llm_config.json to resolve API endpoint and key.
    Falls back to environment variables if model not found in config.
    """
    return create_llm(model=model, temperature=temperature)
