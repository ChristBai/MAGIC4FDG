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

import re

from src.config import ROOT

DOCKER_IMAGE = "fuzzforge:latest"


def _docker_run(command: list[str], timeout: int = 120, memory: str = "") -> subprocess.CompletedProcess[str]:
    """Run a command inside the Docker container with workspace mounted."""
    container_name = f"fuzzforge-run-{os.getpid()}"
    mem_limit = memory or os.environ.get("DOCKER_MEMORY", "2g")
    docker_cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--memory", mem_limit,
        "--cpus", os.environ.get("DOCKER_CPUS", "2"),
        "-v", f"{ROOT}:/workspace",
        "-w", "/workspace",
        DOCKER_IMAGE,
    ] + command

    try:
        return subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", container_name],
                        capture_output=True, timeout=10)
        raise


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
    build_command = target_config.get("build_command", "")
    static_libs = target_config.get("static_libs", [])
    link_flags = target_config.get("link_flags", [])

    include_args = [f"-I{d}" for d in include_dirs]
    compile_sources = " ".join(source_files)
    static_libs_str = " ".join(static_libs)
    link_flags_str = " ".join(link_flags)
    driver_rel = f"generated/{driver_filename}"

    build_step = ""
    if build_command:
        build_step = f'bash "{build_command}"'

    compile_script = f"""#!/bin/bash
set -e
CC="${{CC:-clang}}"
CXX="${{CXX:-clang++}}"

# Build library if build_command specified
{build_step}

# Compile source objects
OBJECTS=""
for src in {compile_sources}; do
    [ -z "$src" ] && continue
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
    $OBJECTS {driver_rel} {static_libs_str} {link_flags_str} -o /tmp/fuzz_driver_test 2>&1
echo "COMPILE_SUCCESS"
"""

    script_path = ROOT / "generated" / "compile_test.sh"
    script_path.write_text(compile_script, encoding="utf-8")

    compile_timeout = 1200 if build_command else 60
    try:
        result = _docker_run(["bash", "generated/compile_test.sh"], timeout=compile_timeout)
    except subprocess.TimeoutExpired:
        script_path.unlink(missing_ok=True)
        return False, f"Build timed out after {compile_timeout}s"

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
    build_command = target_config.get("build_command", "")
    static_libs = target_config.get("static_libs", [])
    link_flags = target_config.get("link_flags", [])
    coverage_sources = target_config.get("coverage_sources", [])
    dictionary = target_config.get("dictionary", "")

    include_args = " ".join(f"-I{d}" for d in include_dirs)
    source_list = " ".join(source_files)
    static_libs_str = " ".join(static_libs)
    link_flags_str = " ".join(link_flags)
    driver_rel = f"generated/{driver_filename}"
    report_json = f"generated/coverage_{library_name}.json"

    # For llvm-cov: use coverage_sources if specified, otherwise source_files
    cov_sources = " ".join(coverage_sources) if coverage_sources else source_list

    seed_corpus_setup = ""
    if seed_corpus:
        seed_corpus_setup = f'cp -r "{seed_corpus}"/* /tmp/corpus/ 2>/dev/null || true'

    # Accumulated corpus from prior iterations
    accumulated_corpus_dir = f"generated/accumulated_corpus/{library_name}"
    accumulated_corpus_setup = f'cp -r "{accumulated_corpus_dir}"/* /tmp/corpus/ 2>/dev/null || true'

    build_step = ""
    if build_command:
        build_step = f'bash "{build_command}" > /tmp/build_lib.log 2>&1'

    dict_flag = ""
    if dictionary:
        dict_flag = f"-dict={dictionary}"

    summary_only = "-summary-only" if build_command else ""

    coverage_script = f"""#!/bin/bash
set -e
CC="${{CC:-clang}}"
CXX="${{CXX:-clang++}}"
LLVM_PROFDATA="${{LLVM_PROFDATA:-llvm-profdata}}"
LLVM_COV="${{LLVM_COV:-llvm-cov}}"

COMMON_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"

# Build library if build_command specified
{build_step}

# Compile source objects
OBJECTS=""
for src in {source_list}; do
    [ -z "$src" ] && continue
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
    $OBJECTS {driver_rel} {static_libs_str} {link_flags_str} -fsanitize=fuzzer,address -o /tmp/fuzz_cov 2>&1
echo "BUILD_OK"

# Prepare corpus
mkdir -p /tmp/corpus /tmp/artifacts
{seed_corpus_setup}
{accumulated_corpus_setup}

# Fuzz
export LLVM_PROFILE_FILE=/tmp/fuzzer_%m.profraw
export ASAN_OPTIONS=halt_on_error=0:exitcode=0:detect_leaks=0
/tmp/fuzz_cov /tmp/corpus \\
    -max_total_time={fuzz_seconds} \\
    -artifact_prefix=/tmp/artifacts/ \\
    -fork=1 \\
    -use_cmp=0 {dict_flag} 2>&1 || true

# Save accumulated corpus for future iterations
mkdir -p {accumulated_corpus_dir}
cp /tmp/corpus/* {accumulated_corpus_dir}/ 2>/dev/null || true

# Merge profile (fork mode produces multiple profraw files)
$LLVM_PROFDATA merge -sparse /tmp/fuzzer_*.profraw -o /tmp/fuzzer.profdata

# Export coverage as JSON
$LLVM_COV export /tmp/fuzz_cov \\
    -instr-profile=/tmp/fuzzer.profdata \\
    {summary_only} {cov_sources} > {report_json}

# Export function-level coverage report
$LLVM_COV report /tmp/fuzz_cov \\
    -instr-profile=/tmp/fuzzer.profdata \\
    {cov_sources} > generated/function_report_{library_name}.txt 2>&1 || true

echo "COVERAGE_OK"
"""

    script_path = ROOT / "generated" / "run_coverage.sh"
    script_path.write_text(coverage_script, encoding="utf-8")

    cov_timeout = fuzz_seconds + (1200 if build_command else 600)
    cov_memory = "6g" if build_command else ""
    try:
        result = _docker_run(["bash", "generated/run_coverage.sh"], timeout=cov_timeout, memory=cov_memory)
    except subprocess.TimeoutExpired:
        script_path.unlink(missing_ok=True)
        return {"error": f"Coverage run timed out after {cov_timeout}s", "coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    combined_output = result.stdout + result.stderr
    script_path.unlink(missing_ok=True)

    if "BUILD_OK" not in combined_output:
        return {"error": f"Build failed:\n{combined_output[-2000:]}", "coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    report_path = ROOT / report_json
    if not report_path.exists():
        return {"error": f"Coverage export not produced:\n{combined_output[-1000:]}", "coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    raw_text = report_path.read_text(encoding="utf-8").strip()
    report_path.unlink(missing_ok=True)
    if not raw_text:
        return {"error": "Coverage export produced empty file", "coverage": {"totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}}, "files": []}}

    raw_export = json.loads(raw_text)

    summary = _summarize_llvm_export(raw_export)

    func_report_path = ROOT / f"generated/function_report_{library_name}.txt"
    if func_report_path.exists():
        summary["function_coverage"] = _parse_function_report(
            func_report_path.read_text(encoding="utf-8")
        )
        func_report_path.unlink(missing_ok=True)

    return summary


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


def _parse_function_report(report_text: str) -> list[dict]:
    """Parse llvm-cov report output into per-function coverage list.

    llvm-cov report format (columns vary by version):
    Filename  Function  Regions  Miss  Cover  Lines  Miss  Cover  Branches ...
    """
    functions: list[dict] = []
    for line in report_text.splitlines():
        # Skip headers, separators, totals
        if not line.strip() or line.startswith("-") or line.startswith("Filename"):
            continue
        if line.strip().startswith("TOTAL"):
            continue

        m = re.match(
            r"^(.+?)\s+([\w:~<>,\[\]\s\*&]+?)\s+"
            r"(\d+)\s+(\d+)\s+([\d.]+%)\s+"
            r"(\d+)\s+(\d+)\s+([\d.]+%)",
            line,
        )
        if m:
            functions.append({
                "file": m.group(1).strip(),
                "function": m.group(2).strip(),
                "regions_count": int(m.group(3)),
                "regions_miss": int(m.group(4)),
                "region_cover": m.group(5),
                "lines_count": int(m.group(6)),
                "lines_miss": int(m.group(7)),
                "line_cover": m.group(8),
            })

    return functions
