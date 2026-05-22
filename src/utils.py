"""通用工具函数。"""

from __future__ import annotations

import re


def strip_code_fences(text: str) -> str:
    """从 LLM 输出中去除 markdown 代码围栏，返回纯源代码。"""
    text = text.strip()
    # If there's a code block in the middle of text (LLM added explanation), extract it
    match = re.search(r"```(?:cpp|c\+\+|c|h)?\s*\n(.+?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Otherwise strip leading/trailing fences
    text = re.sub(r"^```(?:cpp|c\+\+|c|h)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()
