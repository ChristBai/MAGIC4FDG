#!/usr/bin/env python3
"""Build the prompt that will later be sent to an LLM."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "prompts" / "libfuzzer_driver_prompt.txt"
OUTPUT_PATH = ROOT / "generated" / "prompt.txt"


def render_prompt(function_signature: str, header: str, target_description: str) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{FUNCTION_SIGNATURE}}", function_signature)
        .replace("{{HEADER}}", header)
        .replace("{{TARGET_DESCRIPTION}}", target_description)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render the LibFuzzer driver generation prompt."
    )
    parser.add_argument("--signature", required=True, help="Target function signature.")
    parser.add_argument("--header", required=True, help="Header path to include.")
    parser.add_argument(
        "--description",
        required=True,
        help="Short description of the target function behavior.",
    )
    args = parser.parse_args()

    prompt = render_prompt(args.signature, args.header, args.description)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(prompt, encoding="utf-8")
    print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
