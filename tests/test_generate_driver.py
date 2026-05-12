#!/usr/bin/env python3
"""Unit tests for generate_driver.py pure functions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from generate_driver import extract_response_text, strip_code_fences


class TestStripCodeFences(unittest.TestCase):
    def test_strips_cpp_fence(self) -> None:
        text = "```cpp\nint main() {}\n```"
        self.assertEqual(strip_code_fences(text), "int main() {}\n")

    def test_strips_c_fence(self) -> None:
        text = "```c\nvoid foo();\n```"
        self.assertEqual(strip_code_fences(text), "void foo();\n")

    def test_strips_bare_fence(self) -> None:
        text = "```\ncode here\n```"
        self.assertEqual(strip_code_fences(text), "code here\n")

    def test_no_fence_passes_through(self) -> None:
        text = "int main() {}"
        self.assertEqual(strip_code_fences(text), "int main() {}\n")

    def test_strips_surrounding_whitespace(self) -> None:
        text = "  \n```cpp\n  code  \n```\n  "
        self.assertEqual(strip_code_fences(text), "code\n")


class TestExtractResponseText(unittest.TestCase):
    def test_extracts_output_text_field(self) -> None:
        response = {"output_text": "hello world"}
        self.assertEqual(extract_response_text(response), "hello world")

    def test_extracts_from_nested_output(self) -> None:
        response = {
            "output_text": "",
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "part1"},
                        {"type": "output_text", "text": "part2"},
                    ]
                }
            ],
        }
        self.assertEqual(extract_response_text(response), "part1\npart2")

    def test_raises_on_empty_response(self) -> None:
        with self.assertRaises(RuntimeError):
            extract_response_text({"output": []})


if __name__ == "__main__":
    unittest.main()
