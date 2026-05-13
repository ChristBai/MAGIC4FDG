"""Docker execution wrapper for compilation, fuzzing, and coverage collection."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from target_config import ROOT

DOCKER_IMAGE = "fuzz-driver-gen-mvp:latest"


def _docker_run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a command inside the Docker container with workspace mounted."""
    docker_cmd = [
        "docker", "run", "--rm",
        "--memory", os.environ.get("DOCKER_MEMORY", "4g"),
        "--cpus", os.environ.get("DOCKER_CPUS", "2"),
        "-v", f"{ROOT}:/workspace",
        "-w", "/workspace",
        DOCKER_IMAGE,
    ] + command

    return subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def compile_driver(
    driver_source: str,
    target_config: dict,
    driver_filename: str = "fuzz_driver_test.cpp",
) -> tuple[bool, str]:
    """Compile a fuzz driver inside Docker.

    Returns (success, error_output).
    """
    driver_path = ROOT / "generated" / driver_filename
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(driver_source, encoding="utf-8")

    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])

    include_args = [f"-I{d}" for d in include_dirs]
    compile_sources = " ".join(source_files)
    driver_rel = f"generated/{driver_filename}"

    compile_script = f"""#!/bin/bash
set -e
CC="${{CC:-clang}}"
CXX="${{CXX:-clang++}}"

# Compile source objects
OBJECTS=""
for src in {compile_sources}; do
    obj="/tmp/$(basename $src).o"
    if [[ "$src" == *.c ]]; then
        $CC -g -O1 -fsanitize=address {' '.join(include_args)} -c "$src" -o "$obj" 2>&1
    else
        $CXX -std=c++17 -g -O1 -fsanitize=address {' '.join(include_args)} -c "$src" -o "$obj" 2>&1
    fi
    OBJECTS="$OBJECTS $obj"
done

# Link with fuzzer
$CXX -std=c++17 -g -O1 -fsanitize=fuzzer,address {' '.join(include_args)} \\
    $OBJECTS {driver_rel} -o /tmp/fuzz_driver_test 2>&1
echo "COMPILE_SUCCESS"
"""

    script_path = ROOT / "generated" / "compile_test.sh"
    script_path.write_text(compile_script, encoding="utf-8")

    result = _docker_run(["bash", "generated/compile_test.sh"], timeout=60)

    combined_output = result.stdout + result.stderr

    script_path.unlink(missing_ok=True)

    if result.returncode == 0 and "COMPILE_SUCCESS" in combined_output:
        return True, ""

    error_lines = [
        line for line in combined_output.splitlines()
        if "error:" in line.lower() or "undefined" in line.lower() or "fatal" in line.lower()
    ]
    error_output = "\n".join(error_lines[:30]) if error_lines else combined_output[-2000:]
    return False, error_output


def run_fuzz_with_coverage(
    driver_source: str,
    target_config: dict,
    fuzz_seconds: int = 15,
    driver_filename: str = "fuzz_driver_cov.cpp",
) -> dict:
    """Compile with coverage instrumentation, fuzz, and collect coverage.

    Returns the coverage report dict or an error dict.
    """
    driver_path = ROOT / "generated" / driver_filename
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(driver_source, encoding="utf-8")

    target_config_path = _find_target_config_path(target_config)

    result = _docker_run(
        [
            "python3", "src/generate_coverage_report.py",
            "--target-config", target_config_path,
            "--driver", f"generated/{driver_filename}",
            "--fuzz-seconds", str(fuzz_seconds),
            "--use-cmp", "0",
        ],
        timeout=fuzz_seconds + 120,
    )

    if result.returncode != 0:
        return {"error": result.stdout + result.stderr, "coverage_pct": 0.0}

    coverage_dirs = sorted(
        (ROOT / "generated" / "coverage").glob(f"{target_config['target_name']}-*"),
        key=lambda p: p.name,
    )
    if not coverage_dirs:
        return {"error": "No coverage output found", "coverage_pct": 0.0}

    latest = coverage_dirs[-1]
    report_path = latest / "coverage_report.json"
    if not report_path.exists():
        return {"error": "coverage_report.json not found", "coverage_pct": 0.0}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    return report


def _find_target_config_path(target_config: dict) -> str:
    """Find the target config file path from the config dict."""
    target_name = target_config.get("target_name", "")
    candidates = [
        f"targets/{target_name}.json",
        f"targets/{target_name}_parse.json",
    ]
    for path in (ROOT / "targets").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("target_name") == target_name:
                return str(path.relative_to(ROOT))
        except (json.JSONDecodeError, KeyError):
            continue

    for c in candidates:
        if (ROOT / c).exists():
            return c

    return f"targets/{target_name}.json"
