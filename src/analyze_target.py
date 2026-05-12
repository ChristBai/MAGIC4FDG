#!/usr/bin/env python3
"""Rule-based target analyzer for simple C-style public API declarations."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


CLEANUP_WORDS = ("delete", "free", "destroy", "close", "release")
INTERESTING_NAME_WORDS = (
    "parse",
    "decode",
    "load",
    "read",
    "import",
    "scan",
    "verify",
    "check",
    "normalize",
    "convert",
    "from",
)
RAW_INPUT_PATTERNS = (
    r"\bconst\s+char\s*\*",
    r"(?<!\w)\bchar\s*\*",
    r"\buint8_t\s*\*",
    r"\bunsigned\s+char\s*\*",
    r"\bvoid\s*\*",
    r"\bconst\s+void\s*\*",
)
PRIMITIVE_LENGTH_PATTERN = re.compile(
    r"\b(size_t|int|unsigned\s+int|long|unsigned\s+long)\b|"
    r"\b(length|len|size|count|capacity|bytes?)\b",
    re.IGNORECASE,
)
OBJECT_POINTER_PATTERN = re.compile(
    r"\b(?:const\s+)?(?:struct\s+)?[A-Za-z_]\w*\s*(?:\*\s*)+(?:const\s+)?[A-Za-z_]\w*",
)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def strip_preprocessor(text: str) -> str:
    lines: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if skipping:
            skipping = stripped.endswith("\\")
            continue
        if stripped.startswith("#"):
            skipping = stripped.endswith("\\")
            continue
        lines.append(line)
    return "\n".join(lines)


def strip_extern_c_wrappers(text: str) -> str:
    text = re.sub(r'extern\s+"C"\s*\{', "", text)
    return text


def unwrap_public_macros(declaration: str) -> str:
    previous = None
    current = declaration
    while previous != current:
        previous = current
        current = re.sub(r"\b[A-Za-z_]\w*_PUBLIC\s*\(([^()]+)\)", r"\1", current)
    return current


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_declarations(text: str) -> list[str]:
    declarations: list[str] = []
    current: list[str] = []
    brace_depth = 0

    for char in text:
        current.append(char)
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == ";" and brace_depth == 0:
            declaration = "".join(current).strip()
            if declaration:
                declarations.append(declaration[:-1].strip())
            current = []

    return declarations


def is_ignored_declaration(declaration: str) -> bool:
    lowered = declaration.lower().strip()
    ignored_prefixes = ("typedef", "struct ", "enum ", "union ", "extern ")
    if lowered.startswith(ignored_prefixes):
        return True
    if lowered.startswith("static inline") or lowered.startswith("inline static"):
        return True
    if "(*" in declaration:
        return True
    if "{" in declaration or "}" in declaration:
        return True
    return False


def parse_function_declaration(declaration: str) -> dict[str, str] | None:
    declaration = normalize_whitespace(unwrap_public_macros(declaration))
    if not declaration or is_ignored_declaration(declaration):
        return None

    match = re.fullmatch(
        r"(?P<return_type>.+?)\s+(?P<function_name>[A-Za-z_]\w*)\s*\((?P<parameters>.*)\)",
        declaration,
    )
    if not match:
        return None

    return_type = normalize_whitespace(match.group("return_type"))
    function_name = match.group("function_name")
    parameters = normalize_whitespace(match.group("parameters"))

    if function_name in {"if", "for", "while", "switch", "return"}:
        return None
    if not return_type or return_type in {"typedef", "struct", "enum", "union"}:
        return None

    signature = f"{return_type} {function_name}({parameters})"
    return {
        "function_name": function_name,
        "return_type": return_type,
        "parameters": parameters,
        "signature": signature,
    }


def has_raw_input(parameters: str) -> bool:
    normalized = normalize_whitespace(parameters)
    return any(re.search(pattern, normalized) for pattern in RAW_INPUT_PATTERNS)


def is_pointer_like(return_type: str) -> bool:
    normalized = normalize_whitespace(return_type)
    return "*" in normalized or normalized.endswith("_ptr")


def is_cleanup_function(function_name: str) -> bool:
    lowered = function_name.lower()
    return any(word in lowered for word in CLEANUP_WORDS)


def has_complex_object_pointer_without_raw(parameters: str) -> bool:
    if has_raw_input(parameters):
        return False
    if parameters in {"", "void"}:
        return False
    return bool(OBJECT_POINTER_PATTERN.search(parameters))


def looks_object_required_without_raw(parameters: str) -> bool:
    if has_raw_input(parameters):
        return False
    if parameters in {"", "void"}:
        return False
    pointer_count = parameters.count("*")
    return pointer_count > 0


def score_candidate(function: dict[str, str]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    name = function["function_name"]
    return_type = function["return_type"]
    parameters = function["parameters"]

    if has_raw_input(parameters):
        score += 40
        reasons.append("+40 raw/string input parameter")

    if PRIMITIVE_LENGTH_PATTERN.search(parameters):
        score += 25
        reasons.append("+25 primitive length/count parameter")

    if any(word in name.lower() for word in INTERESTING_NAME_WORDS):
        score += 20
        reasons.append("+20 parser/decoder-style function name")

    if is_pointer_like(return_type):
        score += 15
        reasons.append("+15 pointer-like return type")

    if has_complex_object_pointer_without_raw(parameters):
        score -= 30
        reasons.append("-30 complex object pointer without raw/string input")

    if looks_object_required_without_raw(parameters):
        score -= 50
        reasons.append("-50 appears to require existing library object without raw input")

    if is_cleanup_function(name):
        reasons.append("cleanup-only function, not selected as primary target")

    return score, reasons


def library_prefix(library_name: str, functions: list[dict[str, str]]) -> str:
    prefix_counts: dict[str, int] = {}
    for function in functions:
        name = function["function_name"]
        match = re.match(r"([A-Za-z]+)_", name)
        if match:
            prefix_counts[match.group(1)] = prefix_counts.get(match.group(1), 0) + 1

    if prefix_counts:
        return max(prefix_counts, key=prefix_counts.get)
    return library_name


def choose_cleanup(
    candidate: dict[str, str],
    cleanup_functions: list[dict[str, str]],
    prefix: str,
) -> str | None:
    if not is_pointer_like(candidate["return_type"]):
        return None

    prefixed = [
        function
        for function in cleanup_functions
        if function["function_name"].lower().startswith(prefix.lower())
    ]
    pool = prefixed or cleanup_functions
    if not pool:
        return None

    return_type_base = re.sub(r"\b(const|struct)\b|\*", " ", candidate["return_type"])
    return_type_base = normalize_whitespace(return_type_base).lower()

    def cleanup_rank(function: dict[str, str]) -> tuple[int, str]:
        name = function["function_name"].lower()
        parameters = function["parameters"].lower()
        rank = 0
        if return_type_base and return_type_base in parameters:
            rank -= 20
        if "delete" in name or "free" in name:
            rank -= 10
        return rank, name

    return sorted(pool, key=cleanup_rank)[0]["function_name"]


def description_for(function: dict[str, str], cleanup_function: str | None) -> str:
    text = f"Call {function['function_name']} with fuzz-generated input according to its signature."
    if cleanup_function:
        text += f" If it returns an allocated object, release it with {cleanup_function}."
    return text


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def analyze_header(
    *,
    library_name: str,
    header: Path,
    sources: list[str],
    include_dirs: list[str],
    seed_corpus: str,
) -> list[dict[str, Any]]:
    text = strip_extern_c_wrappers(strip_preprocessor(strip_comments(header.read_text(encoding="utf-8"))))
    functions: list[dict[str, str]] = []

    for declaration in split_declarations(text):
        parsed = parse_function_declaration(declaration)
        if parsed:
            functions.append(parsed)

    cleanup_functions = [function for function in functions if is_cleanup_function(function["function_name"])]
    prefix = library_prefix(library_name, functions)
    candidates: list[dict[str, Any]] = []

    for function in functions:
        score, reasons = score_candidate(function)
        if is_cleanup_function(function["function_name"]):
            continue

        cleanup_function = choose_cleanup(function, cleanup_functions, prefix)
        target_name = f"{library_name}_{function['function_name']}"
        candidates.append(
            {
                "name": target_name,
                "target_name": target_name,
                "language": "c",
                "function_name": function["function_name"],
                "signature": function["signature"],
                "header": header.name,
                "source_files": sources,
                "include_dirs": include_dirs,
                "seed_corpus": seed_corpus,
                "cleanup_function": cleanup_function,
                "description": description_for(function, cleanup_function),
                "analysis": {
                    "score": score,
                    "reasons": reasons,
                    "return_type": function["return_type"],
                    "parameters": function["parameters"],
                },
            }
        )

    return sorted(candidates, key=lambda item: (-item["analysis"]["score"], item["function_name"]))


def write_outputs(candidates: list[dict[str, Any]], library_name: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        config_path = out_dir / f"{safe_filename(library_name)}_{safe_filename(candidate['function_name'])}.json"
        config_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")

    index_path = out_dir / f"{safe_filename(library_name)}_index.json"
    index_path.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
    return index_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a C/C++ header and emit fuzz target configs.")
    parser.add_argument("--library-name", required=True, help="Short library name, for example cjson.")
    parser.add_argument("--header", required=True, type=Path, help="Header file to analyze.")
    parser.add_argument("--source", action="append", required=True, help="Implementation source file. Repeatable.")
    parser.add_argument("--include-dir", action="append", required=True, help="Include directory. Repeatable.")
    parser.add_argument("--seed-corpus", required=True, help="Seed corpus directory.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for generated target configs.")
    args = parser.parse_args()

    candidates = analyze_header(
        library_name=args.library_name,
        header=args.header,
        sources=args.source,
        include_dirs=args.include_dir,
        seed_corpus=args.seed_corpus,
    )
    index_path = write_outputs(candidates, args.library_name, args.out_dir)

    print(f"wrote {len(candidates)} target configs to {args.out_dir}")
    print(f"index: {index_path}")
    if candidates:
        top = candidates[0]
        print(
            "top candidate: "
            f"{top['function_name']} score={top['analysis']['score']} "
            f"cleanup={top['cleanup_function'] or '(none)'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
