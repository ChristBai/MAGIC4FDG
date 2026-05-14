"""Docker execution wrapper for compilation, fuzzing, and coverage collection.

All compilation and fuzzing runs inside a Docker container (fuzzforge:latest)
with the project root mounted at /workspace. This isolates the host from
ASan-instrumented binaries and provides a consistent clang/llvm toolchain.

Key design decisions:
- Fork mode (-fork=1): Each fuzz input runs in a subprocess, so ASan crashes
  don't kill the main fuzzer process and coverage data is always collected.
- ASAN_OPTIONS=halt_on_error=0: Allows fuzzing to continue past crashes.
- Coverage uses -fprofile-instr-generate + -fcoverage-mapping for llvm-cov.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from src.config import ROOT

DOCKER_IMAGE = "fuzzforge:latest"


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

    Generates a self-contained shell script that runs inside Docker:
    compile → fuzz → llvm-profdata merge → llvm-cov export.
    Returns the parsed coverage JSON or an error dict.
    """
    driver_path = ROOT / "generated" / driver_filename
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(driver_source, encoding="utf-8")

    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])
    library_name = target_config.get("library_name", "target")
    seed_corpus = target_config.get("seed_corpus", "")

    include_args = " ".join(f"-I{d}" for d in include_dirs)
    source_list = " ".join(source_files)
    driver_rel = f"generated/{driver_filename}"
    report_json = f"generated/coverage_{library_name}.json"

    seed_corpus_setup = ""
    if seed_corpus:
        seed_corpus_setup = f'cp -r "{seed_corpus}"/* /tmp/corpus/ 2>/dev/null || true'

    coverage_script = f"""#!/bin/bash
set -e
CC="${{CC:-clang}}"
CXX="${{CXX:-clang++}}"
LLVM_PROFDATA="${{LLVM_PROFDATA:-llvm-profdata}}"
LLVM_COV="${{LLVM_COV:-llvm-cov}}"

COMMON_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"

# Compile source objects
OBJECTS=""
for src in {source_list}; do
    obj="/tmp/$(basename $src).o"
    if [[ "$src" == *.c ]]; then
        $CC $COMMON_FLAGS {include_args} -c "$src" -o "$obj" 2>&1
    else
        $CXX -std=c++17 $COMMON_FLAGS {include_args} -c "$src" -o "$obj" 2>&1
    fi
    OBJECTS="$OBJECTS $obj"
done

# Link with fuzzer
$CXX -std=c++17 $COMMON_FLAGS {include_args} \\
    $OBJECTS {driver_rel} -fsanitize=fuzzer,address -o /tmp/fuzz_cov 2>&1
echo "BUILD_OK"

# Prepare corpus
mkdir -p /tmp/corpus /tmp/artifacts
{seed_corpus_setup}

# Fuzz
export LLVM_PROFILE_FILE=/tmp/fuzzer_%m.profraw
export ASAN_OPTIONS=halt_on_error=0:exitcode=0:detect_leaks=0
/tmp/fuzz_cov /tmp/corpus \\
    -max_total_time={fuzz_seconds} \\
    -artifact_prefix=/tmp/artifacts/ \\
    -fork=1 \\
    -use_cmp=0 2>&1 || true

# Merge profile (fork mode produces multiple profraw files)
$LLVM_PROFDATA merge -sparse /tmp/fuzzer_*.profraw -o /tmp/fuzzer.profdata

# Export coverage as JSON
$LLVM_COV export /tmp/fuzz_cov \\
    -instr-profile=/tmp/fuzzer.profdata \\
    {source_list} > {report_json}

echo "COVERAGE_OK"
"""

    script_path = ROOT / "generated" / "run_coverage.sh"
    script_path.write_text(coverage_script, encoding="utf-8")

    result = _docker_run(["bash", "generated/run_coverage.sh"], timeout=fuzz_seconds + 180)

    combined_output = result.stdout + result.stderr
    script_path.unlink(missing_ok=True)

    if "BUILD_OK" not in combined_output:
        return {"error": f"Build failed:\n{combined_output[-2000:]}", "coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    report_path = ROOT / report_json
    if not report_path.exists():
        return {"error": f"Coverage export not produced:\n{combined_output[-1000:]}", "coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    raw_export = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink(missing_ok=True)

    return _summarize_llvm_export(raw_export)


def _summarize_llvm_export(export_data: dict) -> dict:
    """Convert raw llvm-cov export JSON into the format expected by coverage agent."""
    data_items = export_data.get("data") or []
    if not data_items:
        return {"coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    first = data_items[0]
    files = []
    for file_item in first.get("files", []):
        summary = file_item.get("summary", {})
        segments = []
        for seg in file_item.get("segments", []):
            if len(seg) >= 5:
                segments.append(seg)
        files.append({
            "filename": file_item.get("filename", ""),
            "summary": summary,
            "segments": segments,
        })

    return {
        "coverage": {
            "totals": first.get("totals", {}),
            "files": sorted(files, key=lambda f: f["filename"]),
        }
    }
