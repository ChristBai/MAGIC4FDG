"""Shared utilities for the fuzz driver generation pipeline."""

from __future__ import annotations

import re


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from LLM output, returning raw source code."""
    text = text.strip()
    text = re.sub(r"^```(?:cpp|c\+\+|c|h)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()
