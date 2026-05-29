"""Docker 执行封装：Knowledge 提取、编译、fuzzing 和覆盖率收集。

所有操作在 Docker 容器（magic4fdg:latest）中运行，项目根目录挂载到 /workspace。

架构：
- Knowledge 阶段：build + clang AST 分析，产物缓存到 generated/build_cache/<lib>/
- Compile/Fuzz 阶段：挂载 build_cache 为 /opt/bench/（只读），跳过重复 build
- Coverage：fork 模式 fuzz + corpus replay 收集覆盖率
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from src.config import ROOT

DOCKER_IMAGE = "magic4fdg:latest"


def _clear_xattr(path: Path) -> None:
    """清除 macOS extended attributes，防止 Docker VirtioFS 读取失败。"""
    try:
        subprocess.run(["xattr", "-c", str(path)], capture_output=True, timeout=5)
    except Exception:
        pass


def _docker_run(
    command: list[str],
    timeout: int = 120,
    memory: str = "",
    run_id: str = "",
    extra_volumes: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """在 Docker 容器中执行命令，挂载最小必要目录。

    只挂载 Docker 实际需要的子目录，避免整个项目目录被 Docker 文件共享锁定
    （macOS VirtioFS/gRPC FUSE 死锁问题）。
    """
    uid = run_id or f"{os.getpid()}-{threading.get_ident()}"
    container_name = f"magic4fdg-{uid}"[:63]
    mem_limit = memory or os.environ.get("DOCKER_MEMORY", "2g")
    generated_dir = ROOT / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    docker_cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--memory", mem_limit,
        "--cpus", os.environ.get("DOCKER_CPUS", "2"),
        "-v", f"{generated_dir}:/workspace/generated",
        "-v", f"{ROOT / 'benchmarks'}:/workspace/benchmarks:ro",
        "-v", f"{ROOT / 'targets'}:/workspace/targets:ro",
        "-w", "/workspace",
    ]
    proxy = os.environ.get("DOCKER_PROXY", "http://host.docker.internal:7897")
    if proxy:
        docker_cmd += [
            "-e", f"http_proxy={proxy}",
            "-e", f"https_proxy={proxy}",
        ]
    for vol in (extra_volumes or []):
        docker_cmd += ["-v", vol]
    docker_cmd.append(DOCKER_IMAGE)
    docker_cmd += command

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            timeout=timeout,
        )
        # Decode with errors='replace' to handle binary data in fuzzer output
        result.stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
        result.stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        return result
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", container_name],
                       capture_output=True, timeout=10)
        raise


# =============================================================================
# Knowledge 阶段：build + clang AST 提取
# =============================================================================

def build_and_extract(target_config: dict) -> dict:
    """Docker 内执行 build + clang AST dump + 宏提取。

    产物缓存到 generated/build_cache/<library_name>/，供后续容器复用。
    如果缓存已存在，跳过 build 直接挂载缓存执行 clang 分析。
    返回 {"ast_header": str, "macros": str, "source_asts": [str]}。
    """
    library_name = target_config["library_name"]
    build_command = target_config.get("build_command", "")
    header = target_config.get("header", "")
    include_dirs = target_config.get("include_dirs", [])
    source_files = target_config.get("source_files", [])
    lang_flag = "-xc++" if target_config.get("language", "C") == "C++" else "-xc"

    cache_dir = f"generated/build_cache/{library_name}"
    include_flags = " ".join(f"-I{d}" for d in include_dirs)

    extra_volumes, cache_hit = _get_build_cache_volumes(target_config)
    effective_build_command = "" if cache_hit else build_command

    script = _build_knowledge_script(
        effective_build_command, header, source_files,
        include_flags, lang_flag, cache_dir,
    )

    script_path = ROOT / "generated" / f"knowledge_{library_name}.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    _clear_xattr(script_path)

    timeout = 120 if cache_hit else (600 if build_command else 120)
    try:
        result = _docker_run(
            ["bash", f"generated/knowledge_{library_name}.sh"],
            timeout=timeout, memory="4g",
            run_id=f"knowledge-{library_name}",
            extra_volumes=extra_volumes,
        )
    except subprocess.TimeoutExpired:
        script_path.unlink(missing_ok=True)
        return {"ast_header": "", "macros": "", "source_asts": [], "error": "timeout"}

    script_path.unlink(missing_ok=True)
    combined = result.stdout + result.stderr
    return _parse_knowledge_output(combined)


def _build_knowledge_script(
    build_command: str,
    header: str,
    source_files: list[str],
    include_flags: str,
    lang_flag: str,
    cache_dir: str,
) -> str:
    """生成 Knowledge 阶段的 Docker 内执行脚本。"""
    build_step = ""
    if build_command:
        build_step = f'bash "{build_command}"'

    cache_step = ""
    if build_command:
        cache_step = f"""
mkdir -p /workspace/{cache_dir}
cp -r /opt/bench/* /workspace/{cache_dir}/ 2>/dev/null || true
"""

    source_ast_steps = ""
    if source_files:
        for i, src in enumerate(source_files[:5]):
            source_ast_steps += f"""
echo "===AST_SRC_{i}_START==="
clang {lang_flag} {include_flags} -Xclang -ast-dump=json -fsyntax-only "{src}" 2>/dev/null || true
echo "===AST_SRC_{i}_END==="
"""
    elif "/opt/bench" in header:
        ext = "cpp" if "c++" in lang_flag else "c"
        source_ast_steps = f"""
# Auto-discover source files from build output
SRC_FILES=$(find /opt/bench -path "*/src/*.{ext}" -o -path "*/src/*.cc" 2>/dev/null | head -5)
_IDX=0
for _src in $SRC_FILES; do
    echo "===AST_SRC_${{_IDX}}_START==="
    clang {lang_flag} {include_flags} -Xclang -ast-dump=json -fsyntax-only "$_src" 2>/dev/null || true
    echo "===AST_SRC_${{_IDX}}_END==="
    _IDX=$((_IDX + 1))
done
"""

    return f"""#!/bin/bash
set -e

# Phase 0: Build library
{build_step}

# Cache build artifacts for later compile/fuzz containers
{cache_step}

# Phase 1: AST dump (header)
echo "===AST_HEADER_START==="
clang {lang_flag} {include_flags} -fparse-all-comments -Xclang -ast-dump=json -fsyntax-only "{header}" 2>/dev/null || true
echo "===AST_HEADER_END==="

# Phase 2: Preprocessor macros
echo "===MACROS_START==="
clang {lang_flag} {include_flags} -dM -E "{header}" 2>/dev/null || true
echo "===MACROS_END==="

# Phase 3: Source AST (call graph)
{source_ast_steps}

echo "===DONE==="
"""


def _parse_knowledge_output(output: str) -> dict:
    """按分隔符解析 Knowledge 脚本的 stdout。"""
    result = {"ast_header": "", "macros": "", "source_asts": []}

    ast_match = _extract_section(output, "AST_HEADER")
    if ast_match:
        result["ast_header"] = ast_match

    macros_match = _extract_section(output, "MACROS")
    if macros_match:
        result["macros"] = macros_match

    i = 0
    while True:
        section = _extract_section(output, f"AST_SRC_{i}")
        if section is None:
            break
        result["source_asts"].append(section)
        i += 1

    return result


def _extract_section(output: str, name: str) -> str | None:
    """从输出中提取 ===NAME_START=== 和 ===NAME_END=== 之间的内容。"""
    start_marker = f"==={name}_START==="
    end_marker = f"==={name}_END==="
    start_idx = output.find(start_marker)
    if start_idx < 0:
        return None
    start_idx += len(start_marker) + 1  # skip newline
    end_idx = output.find(end_marker, start_idx)
    if end_idx < 0:
        return None
    return output[start_idx:end_idx].strip()


# =============================================================================
# Build 缓存辅助
# =============================================================================

def _get_build_cache_volumes(target_config: dict) -> tuple[list[str], bool]:
    """检查 build_cache 是否存在，返回 (extra_volumes, cache_hit)。

    Cache 结构: build_cache/<lib>/<lib>/include/ (mirrors /opt/bench/<lib>/)
    """
    library_name = target_config.get("library_name", "")
    cache_dir = ROOT / "generated" / "build_cache" / library_name
    if not cache_dir.exists():
        return [], False
    # Look for include/ or lib/ at any depth ≤ 2 inside cache_dir
    for sub in cache_dir.rglob("include"):
        if sub.is_dir() and len(sub.relative_to(cache_dir).parts) <= 2:
            return [f"{cache_dir}:/opt/bench:ro"], True
    for sub in cache_dir.rglob("lib"):
        if sub.is_dir() and len(sub.relative_to(cache_dir).parts) <= 2:
            return [f"{cache_dir}:/opt/bench:ro"], True
    return [], False


# =============================================================================
# 编译阶段
# =============================================================================

def compile_driver(
    driver_source: str,
    target_config: dict,
    driver_filename: str = "fuzz_driver_test.cpp",
) -> tuple[bool, str]:
    """在 Docker 中编译 fuzz driver，返回 (成功, 错误输出)。"""
    driver_path = ROOT / "generated" / driver_filename
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(driver_source, encoding="utf-8")
    _clear_xattr(driver_path)

    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])
    build_command = target_config.get("build_command", "")
    static_libs = target_config.get("static_libs", [])
    link_flags = target_config.get("link_flags", [])

    include_args = [f'-I"{d}"' for d in include_dirs]
    compile_sources = " ".join(f'"{s}"' for s in source_files)
    static_libs_str = " ".join(f'"{s}"' for s in static_libs)
    link_flags_str = " ".join(link_flags)
    driver_rel = f"generated/{driver_filename}"

    extra_volumes, cache_hit = _get_build_cache_volumes(target_config)

    build_step = ""
    build_include_discovery = ""
    if cache_hit:
        build_include_discovery = 'BUILD_INCLUDES=$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")'
    elif build_command:
        build_step = f'bash "{build_command}"'
        build_include_discovery = 'BUILD_INCLUDES=$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")'

    compile_script = f"""#!/bin/bash
set -e
CC="${{CC:-clang}}"
CXX="${{CXX:-clang++}}"

# Build library if needed (skipped when cache is mounted)
{build_step}

# Discover build-generated include dirs
{build_include_discovery}

# Compile source objects
SOURCES=({compile_sources})
OBJECTS=""
for src in "${{SOURCES[@]}}"; do
    [ -z "$src" ] && continue
    obj="/tmp/$(basename "$src").o"
    if [[ "$src" == *.c ]]; then
        $CC -g -O1 -fsanitize=address {' '.join(include_args)} $BUILD_INCLUDES -c "$src" -o "$obj" 2>&1
    else
        $CXX -std=c++17 -g -O1 -fsanitize=address {' '.join(include_args)} $BUILD_INCLUDES -c "$src" -o "$obj" 2>&1
    fi
    OBJECTS="$OBJECTS $obj"
done

# Link with fuzzer
$CXX -std=c++17 -g -O1 -fsanitize=fuzzer,address {' '.join(include_args)} $BUILD_INCLUDES \\
    $OBJECTS "{driver_rel}" {static_libs_str} {link_flags_str} -o /tmp/fuzz_driver_test 2>&1
echo "COMPILE_SUCCESS"
"""

    script_path = ROOT / "generated" / f"compile_{driver_filename}.sh"
    script_path.write_text(compile_script, encoding="utf-8")
    _clear_xattr(script_path)

    compile_timeout = 60 if cache_hit else (1200 if build_command else 60)
    try:
        result = _docker_run(
            ["bash", f"generated/compile_{driver_filename}.sh"],
            timeout=compile_timeout,
            run_id=f"compile-{driver_filename}",
            extra_volumes=extra_volumes,
        )
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
    error_output = "\n".join(error_lines) if error_lines else combined_output[-4000:]
    return False, error_output


# =============================================================================
# Fuzz + 覆盖率收集
# =============================================================================

def run_fuzz_with_coverage(
    driver_source: str,
    target_config: dict,
    fuzz_seconds: int = 15,
    driver_filename: str = "fuzz_driver_cov.cpp",
) -> dict:
    """编译（带覆盖率插桩）→ fuzz → replay corpus → 收集覆盖率。"""
    driver_path = ROOT / "generated" / driver_filename
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(driver_source, encoding="utf-8")
    _clear_xattr(driver_path)

    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])
    library_name = target_config.get("library_name", "target")
    seed_corpus = target_config.get("seed_corpus", "")
    build_command = target_config.get("build_command", "")
    static_libs = target_config.get("static_libs", [])
    link_flags = target_config.get("link_flags", [])
    coverage_sources = target_config.get("coverage_sources", [])
    dictionary = target_config.get("dictionary", "")

    include_args = " ".join(f'-I"{d}"' for d in include_dirs)
    source_list = " ".join(f'"{s}"' for s in source_files)
    static_libs_str = " ".join(f'"{s}"' for s in static_libs)
    if len(static_libs) > 1:
        static_libs_whole = f'-Wl,--whole-archive "{static_libs[0]}" -Wl,--no-whole-archive ' + " ".join(f'"{s}"' for s in static_libs[1:])
    else:
        static_libs_whole = '-Wl,--whole-archive ' + static_libs_str + ' -Wl,--no-whole-archive'
    link_flags_str = " ".join(link_flags)
    driver_rel = f"generated/{driver_filename}"
    driver_stem = Path(driver_filename).stem
    report_json = f"generated/coverage_{driver_stem}.json"
    func_report = f"generated/func_report_{driver_stem}.txt"

    cov_sources = " ".join(f'"{s}"' for s in coverage_sources) if coverage_sources else source_list

    seed_corpus_setup = ""
    if seed_corpus:
        seed_corpus_setup = f'cp -r "{seed_corpus}"/* /tmp/corpus/ 2>/dev/null || true'
    # Also copy from build-cache seed corpus (collected from library test data during build)
    build_cache_seed = f"/opt/bench/{library_name}/seed_corpus"
    seed_corpus_setup += f'\ncp -r "{build_cache_seed}"/* /tmp/corpus/ 2>/dev/null || true'

    accumulated_corpus_dir = f"generated/accumulated_corpus/{library_name}"
    accumulated_corpus_setup = f'cp -r "{accumulated_corpus_dir}"/* /tmp/corpus/ 2>/dev/null || true'

    extra_volumes, cache_hit = _get_build_cache_volumes(target_config)

    build_step = ""
    build_include_discovery = ""
    if cache_hit:
        build_include_discovery = 'BUILD_INCLUDES=$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")'
    elif build_command:
        build_step = f'bash "{build_command}" > /tmp/build_lib.log 2>&1'
        build_include_discovery = 'BUILD_INCLUDES=$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")'

    dict_flag = f"-dict={dictionary}" if dictionary else ""

    coverage_script = f"""#!/bin/bash
set -e
CC="${{CC:-clang}}"
CXX="${{CXX:-clang++}}"
LLVM_PROFDATA="${{LLVM_PROFDATA:-llvm-profdata}}"
LLVM_COV="${{LLVM_COV:-llvm-cov}}"

COMMON_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"

# Build library (skipped when cache is mounted)
{build_step}

# Discover build-generated include dirs
{build_include_discovery}

# Compile source objects
SOURCES=({source_list})
OBJECTS=""
for src in "${{SOURCES[@]}}"; do
    [ -z "$src" ] && continue
    obj="/tmp/$(basename "$src").o"
    if [[ "$src" == *.c ]]; then
        $CC $COMMON_FLAGS {include_args} $BUILD_INCLUDES -c "$src" -o "$obj" 2>&1
    else
        $CXX -std=c++17 $COMMON_FLAGS {include_args} $BUILD_INCLUDES -c "$src" -o "$obj" 2>&1
    fi
    OBJECTS="$OBJECTS $obj"
done

# Link with fuzzer (--whole-archive for target lib only, stable coverage denominator)
$CXX -std=c++17 $COMMON_FLAGS {include_args} $BUILD_INCLUDES \\
    $OBJECTS "{driver_rel}" {static_libs_whole} {link_flags_str} -fsanitize=fuzzer,address -o /tmp/fuzz_cov 2>&1
echo "BUILD_OK"

# Prepare corpus
mkdir -p /tmp/corpus /tmp/artifacts
{seed_corpus_setup}
{accumulated_corpus_setup}

# Fuzz with fork mode (crash isolation)
# cd /tmp so fork-mode crash files land in /tmp, not /workspace (project root)
export LLVM_PROFILE_FILE=/tmp/fuzzer_%m.profraw
export ASAN_OPTIONS=halt_on_error=0:exitcode=0:detect_leaks=0
cd /tmp
/tmp/fuzz_cov /tmp/corpus \\
    -max_total_time={fuzz_seconds} \\
    -artifact_prefix=/tmp/artifacts/ \\
    -fork=1 \\
    -use_cmp=1 {dict_flag} 2>&1 || true
cd /workspace

# Save accumulated corpus
mkdir -p {accumulated_corpus_dir}
cp /tmp/corpus/* {accumulated_corpus_dir}/ 2>/dev/null || true

# Replay corpus without fork mode to collect coverage properly.
# Fork workers get SIGKILL'd at timeout, losing profraw data.
export LLVM_PROFILE_FILE=/tmp/replay_%m.profraw
/tmp/fuzz_cov /tmp/corpus -runs=0 2>&1 || true

# Merge profraw (prefer replay which is complete)
$LLVM_PROFDATA merge -sparse /tmp/replay_*.profraw -o /tmp/fuzzer.profdata 2>/dev/null \\
  || $LLVM_PROFDATA merge -sparse /tmp/fuzzer_*.profraw -o /tmp/fuzzer.profdata

# Export coverage as JSON (filtering done in Python by coverage_sources prefix)
$LLVM_COV export /tmp/fuzz_cov \\
    -instr-profile=/tmp/fuzzer.profdata \\
    -skip-expansions -skip-functions > {report_json}

# Export function-level report
$LLVM_COV report /tmp/fuzz_cov \\
    -instr-profile=/tmp/fuzzer.profdata > {func_report} 2>&1 || true

echo "COVERAGE_OK"
"""

    script_path = ROOT / "generated" / f"run_cov_{driver_stem}.sh"
    script_path.write_text(coverage_script, encoding="utf-8")
    _clear_xattr(script_path)

    cov_timeout = fuzz_seconds + (60 if cache_hit else (1200 if build_command else 600))
    cov_memory = "6g" if (build_command and not cache_hit) else ""
    try:
        result = _docker_run(
            ["bash", f"generated/run_cov_{driver_stem}.sh"],
            timeout=cov_timeout, memory=cov_memory,
            run_id=f"cov-{driver_stem}",
            extra_volumes=extra_volumes,
        )
    except subprocess.TimeoutExpired:
        script_path.unlink(missing_ok=True)
        return _empty_coverage("Coverage run timed out")

    combined_output = result.stdout + result.stderr
    script_path.unlink(missing_ok=True)

    if "BUILD_OK" not in combined_output:
        return _empty_coverage(f"Build failed:\n{combined_output[-2000:]}")

    report_path = ROOT / report_json
    if not report_path.exists():
        return _empty_coverage(f"Coverage export not produced:\n{combined_output[-1000:]}")

    raw_text = report_path.read_text(encoding="utf-8").strip()
    report_path.unlink(missing_ok=True)
    if not raw_text:
        return _empty_coverage("Coverage export produced empty file")

    raw_export = json.loads(raw_text)
    summary = _summarize_llvm_export(raw_export, coverage_sources=coverage_sources)

    func_report_path = ROOT / func_report
    if func_report_path.exists():
        summary["function_coverage"] = _parse_function_report(
            func_report_path.read_text(encoding="utf-8")
        )
        func_report_path.unlink(missing_ok=True)

    return summary


def _empty_coverage(error: str) -> dict:
    return {
        "error": error,
        "coverage": {
            "totals": {"lines": {"count": 0, "covered": 0}, "branches": {"count": 0, "covered": 0}},
            "files": [],
        },
    }


# =============================================================================
# 覆盖率解析
# =============================================================================

def _summarize_llvm_export(export_data: dict, coverage_sources: list[str] | None = None) -> dict:
    """将 llvm-cov export JSON 转换为内部格式。

    coverage_sources: 源路径前缀列表，用于过滤只统计目标库的文件。
    """
    data_items = export_data.get("data") or []
    if not data_items:
        return _empty_coverage("No data in llvm-cov export")

    first = data_items[0]
    files = []
    for file_item in first.get("files", []):
        filename = file_item.get("filename", "")
        if coverage_sources:
            if not any(filename.startswith(prefix) for prefix in coverage_sources):
                continue
        summary = file_item.get("summary", {})
        segments = [seg for seg in file_item.get("segments", []) if len(seg) >= 5]
        files.append({
            "filename": filename,
            "summary": summary,
            "segments": segments,
        })

    if coverage_sources and files:
        totals = _recompute_totals(files)
    else:
        totals = first.get("totals", {})

    return {
        "coverage": {
            "totals": totals,
            "files": sorted(files, key=lambda f: f["filename"]),
        }
    }


def _recompute_totals(files: list[dict]) -> dict:
    """从过滤后的文件列表重新计算 totals。"""
    lines_count = lines_covered = 0
    branches_count = branches_covered = 0
    functions_count = functions_covered = 0
    regions_count = regions_covered = 0

    for f in files:
        s = f.get("summary", {})
        lines = s.get("lines", {})
        lines_count += lines.get("count", 0)
        lines_covered += lines.get("covered", 0)
        branches = s.get("branches", {})
        branches_count += branches.get("count", 0)
        branches_covered += branches.get("covered", 0)
        functions = s.get("functions", {})
        functions_count += functions.get("count", 0)
        functions_covered += functions.get("covered", 0)
        regions = s.get("regions", {})
        regions_count += regions.get("count", 0)
        regions_covered += regions.get("covered", 0)

    def pct(covered, total):
        return (covered / total * 100) if total > 0 else 0.0

    return {
        "lines": {"count": lines_count, "covered": lines_covered, "percent": pct(lines_covered, lines_count)},
        "branches": {"count": branches_count, "covered": branches_covered, "percent": pct(branches_covered, branches_count)},
        "functions": {"count": functions_count, "covered": functions_covered, "percent": pct(functions_covered, functions_count)},
        "regions": {"count": regions_count, "covered": regions_covered, "percent": pct(regions_covered, regions_count)},
    }


def _parse_function_report(report_text: str) -> list[dict]:
    """解析 llvm-cov report 输出为函数级覆盖率列表。"""
    functions: list[dict] = []
    for line in report_text.splitlines():
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


# =============================================================================
# Union 覆盖率：合并多个 driver 的 profdata 测量真实并集覆盖率
# =============================================================================

def run_union_coverage(
    drivers: dict[str, str],
    target_config: dict,
    fuzz_seconds: int = 0,
) -> dict:
    """测量多个 driver 的合并覆盖率（真实 union）。

    对每个 driver：编译（带 coverage instrumentation）→ replay accumulated corpus → 收集 profraw。
    然后合并所有 profdata，用 llvm-cov export 导出真实 union 覆盖率。

    Args:
        drivers: slot_id → source_code
        target_config: 目标库配置
        fuzz_seconds: 若 >0 则先 fuzz 再 replay；0 表示只 replay 已有 corpus

    Returns:
        {"line_pct", "line_covered", "line_total", "branch_pct", "branch_covered", "branch_total", "error"?}
    """
    if not drivers:
        return {"line_pct": 0.0, "line_covered": 0, "line_total": 0,
                "branch_pct": 0.0, "branch_covered": 0, "branch_total": 0}

    library_name = target_config.get("library_name", "target")
    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])
    static_libs = target_config.get("static_libs", [])
    link_flags = target_config.get("link_flags", [])
    coverage_sources = target_config.get("coverage_sources", [])
    build_command = target_config.get("build_command", "")

    include_args = " ".join(f'-I"{d}"' for d in include_dirs)
    source_list = " ".join(f'"{s}"' for s in source_files)
    static_libs_str = " ".join(f'"{s}"' for s in static_libs)
    if len(static_libs) > 1:
        static_libs_whole = f'-Wl,--whole-archive "{static_libs[0]}" -Wl,--no-whole-archive ' + " ".join(f'"{s}"' for s in static_libs[1:])
    else:
        static_libs_whole = '-Wl,--whole-archive ' + static_libs_str + ' -Wl,--no-whole-archive'
    link_flags_str = " ".join(link_flags)
    cov_sources = " ".join(f'"{s}"' for s in coverage_sources) if coverage_sources else source_list
    accumulated_corpus_dir = f"generated/accumulated_corpus/{library_name}"

    extra_volumes, cache_hit = _get_build_cache_volumes(target_config)

    build_step = ""
    build_include_discovery = ""
    if cache_hit:
        build_include_discovery = 'BUILD_INCLUDES=$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")'
    elif build_command:
        build_step = f'bash "{build_command}" > /tmp/build_lib.log 2>&1'
        build_include_discovery = 'BUILD_INCLUDES=$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")'

    # Write each driver to generated/union_*.cpp
    driver_files: list[str] = []
    for slot_id, source_code in drivers.items():
        fname = f"union_{slot_id}.cpp"
        fpath = ROOT / "generated" / fname
        fpath.write_text(source_code, encoding="utf-8")
        _clear_xattr(fpath)
        driver_files.append(fname)

    # Build compile + replay script for each driver
    compile_blocks = []
    replay_blocks = []
    for i, fname in enumerate(driver_files):
        compile_blocks.append(f"""
# Compile driver {i}: {fname}
$CXX -std=c++17 $COMMON_FLAGS {include_args} $BUILD_INCLUDES \\
    $OBJECTS "generated/{fname}" {static_libs_whole} {link_flags_str} \\
    -fsanitize=fuzzer,address -o /tmp/fuzz_union_{i} 2>&1
if [ $? -ne 0 ]; then echo "COMPILE_FAIL_{i}"; fi
""")
        replay_blocks.append(f"""
# Replay corpus with driver {i}
if [ -f /tmp/fuzz_union_{i} ]; then
    export LLVM_PROFILE_FILE=/tmp/union_{i}_%m.profraw
    /tmp/fuzz_union_{i} /tmp/corpus -runs=0 2>&1 || true
fi
""")

    script = f"""#!/bin/bash
set -e
CXX="${{CXX:-clang++}}"
CC="${{CC:-clang}}"
LLVM_PROFDATA="${{LLVM_PROFDATA:-llvm-profdata}}"
LLVM_COV="${{LLVM_COV:-llvm-cov}}"

COMMON_FLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"

# Build library
{build_step}
{build_include_discovery}

# Compile source objects
SOURCES=({source_list})
OBJECTS=""
for src in "${{SOURCES[@]}}"; do
    [ -z "$src" ] && continue
    obj="/tmp/$(basename "$src").o"
    if [[ "$src" == *.c ]]; then
        $CC $COMMON_FLAGS {include_args} $BUILD_INCLUDES -c "$src" -o "$obj" 2>&1
    else
        $CXX -std=c++17 $COMMON_FLAGS {include_args} $BUILD_INCLUDES -c "$src" -o "$obj" 2>&1
    fi
    OBJECTS="$OBJECTS $obj"
done

# Compile all drivers
{"".join(compile_blocks)}

echo "BUILD_OK"

# Prepare corpus (accumulated from all prior rounds)
mkdir -p /tmp/corpus
cp -r {accumulated_corpus_dir}/* /tmp/corpus/ 2>/dev/null || true

# Replay corpus with each driver
export ASAN_OPTIONS=halt_on_error=0:exitcode=0:detect_leaks=0
{"".join(replay_blocks)}

# Merge all profraw files
$LLVM_PROFDATA merge -sparse /tmp/union_*.profraw -o /tmp/union_merged.profdata 2>/dev/null
if [ $? -ne 0 ]; then
    echo "PROFDATA_MERGE_FAIL"
    exit 1
fi

# Export union coverage (use first successfully compiled binary for coverage mapping)
FIRST_BIN=""
for i in $(seq 0 {len(driver_files) - 1}); do
    if [ -f /tmp/fuzz_union_$i ]; then
        FIRST_BIN="/tmp/fuzz_union_$i"
        break
    fi
done

if [ -z "$FIRST_BIN" ]; then
    echo "NO_BINARY"
    exit 1
fi

$LLVM_COV export $FIRST_BIN \\
    -instr-profile=/tmp/union_merged.profdata \\
    -skip-expansions -skip-functions > generated/union_coverage.json

echo "UNION_OK"
"""

    script_path = ROOT / "generated" / "run_union_coverage.sh"
    script_path.write_text(script, encoding="utf-8")
    _clear_xattr(script_path)

    cov_timeout = 120 if cache_hit else (1200 if build_command else 600)
    try:
        result = _docker_run(
            ["bash", "generated/run_union_coverage.sh"],
            timeout=cov_timeout,
            run_id=f"union-cov-{library_name}",
            extra_volumes=extra_volumes,
        )
    except subprocess.TimeoutExpired:
        script_path.unlink(missing_ok=True)
        return {"line_pct": 0.0, "line_covered": 0, "line_total": 0,
                "branch_pct": 0.0, "branch_covered": 0, "branch_total": 0,
                "error": "Union coverage timed out"}

    combined_output = result.stdout + result.stderr
    script_path.unlink(missing_ok=True)
    for fname in driver_files:
        (ROOT / "generated" / fname).unlink(missing_ok=True)

    if "BUILD_OK" not in combined_output:
        return {"line_pct": 0.0, "line_covered": 0, "line_total": 0,
                "branch_pct": 0.0, "branch_covered": 0, "branch_total": 0,
                "error": f"Union build failed: {combined_output[-500:]}"}

    report_path = ROOT / "generated" / "union_coverage.json"
    if not report_path.exists() or "UNION_OK" not in combined_output:
        return {"line_pct": 0.0, "line_covered": 0, "line_total": 0,
                "branch_pct": 0.0, "branch_covered": 0, "branch_total": 0,
                "error": f"Union coverage export failed: {combined_output[-500:]}"}

    raw_text = report_path.read_text(encoding="utf-8").strip()
    report_path.unlink(missing_ok=True)
    if not raw_text:
        return {"line_pct": 0.0, "line_covered": 0, "line_total": 0,
                "branch_pct": 0.0, "branch_covered": 0, "branch_total": 0,
                "error": "Union coverage export empty"}

    export_data = json.loads(raw_text)
    data_items = export_data.get("data") or []
    if not data_items:
        return {"line_pct": 0.0, "line_covered": 0, "line_total": 0,
                "branch_pct": 0.0, "branch_covered": 0, "branch_total": 0,
                "error": "No data in union export"}

    # Filter files by coverage_sources prefix
    first = data_items[0]
    if coverage_sources:
        matching_files = [
            f for f in first.get("files", [])
            if any(f.get("filename", "").startswith(prefix) for prefix in coverage_sources)
        ]
        if matching_files:
            totals = _recompute_totals([
                {"summary": f.get("summary", {})} for f in matching_files
            ])
        else:
            totals = first.get("totals", {})
    else:
        totals = first.get("totals", {})
    lines = totals.get("lines", {})
    branches = totals.get("branches", {})

    line_count = lines.get("count", 0)
    line_covered = lines.get("covered", 0)
    branch_count = branches.get("count", 0)
    branch_covered = branches.get("covered", 0)

    return {
        "line_pct": (100.0 * line_covered / line_count) if line_count else 0.0,
        "line_covered": line_covered,
        "line_total": line_count,
        "branch_pct": (100.0 * branch_covered / branch_count) if branch_count else 0.0,
        "branch_covered": branch_covered,
        "branch_total": branch_count,
    }
