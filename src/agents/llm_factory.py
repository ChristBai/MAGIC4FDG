"""LLM factory for creating ChatOpenAI instances with different configurations."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI


DEFAULT_MODEL = "gpt-4o"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_RETRY_ATTEMPTS = 3


def create_llm(
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """Create a ChatOpenAI instance from environment variables and overrides.

    Supports any OpenAI-compatible API (DeepSeek, Qwen, Ollama, etc.)
    via the base_url parameter or LLM_API_URL environment variable.
    """
    resolved_base_url = base_url or os.environ.get("LLM_API_URL", DEFAULT_BASE_URL)
    resolved_model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    resolved_api_key = api_key or os.environ.get("LLM_API_KEY", "")

    llm = ChatOpenAI(
        base_url=resolved_base_url,
        model=resolved_model,
        api_key=resolved_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=120,
    )
    return llm.with_retry(stop_after_attempt=DEFAULT_RETRY_ATTEMPTS)


def create_variant_llm(model: str, temperature: float) -> ChatOpenAI:
    """Create an LLM instance for a specific variant configuration.

    Uses the model name to determine the appropriate base_url:
    - Models starting with 'deepseek' use DEEPSEEK_API_URL or default DeepSeek endpoint
    - Others use LLM_API_URL or default OpenAI endpoint
    """
    if model.startswith("deepseek"):
        base_url = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
        api_key = os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", ""))
    else:
        base_url = os.environ.get("LLM_API_URL", DEFAULT_BASE_URL)
        api_key = os.environ.get("LLM_API_KEY", "")

    return create_llm(
        model=model,
        temperature=temperature,
        base_url=base_url,
        api_key=api_key,
    )
