#!/usr/bin/env python3
"""Build, run, and report LLVM coverage for a generated fuzz driver."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from target_config import ROOT, load_target_config, resolve_project_path

DEFAULT_DRIVER = ROOT / "generated" / "fuzz_driver.cpp"
DEFAULT_OUT_DIR = ROOT / "generated" / "coverage"


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required tool not found on PATH: {name}")
    return path


def run_command(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(command) + "\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"\n[exit code: {completed.returncode}]\n")

    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")
    return completed


def metric_percent(metric: dict[str, Any]) -> float:
    percent = metric.get("percent")
    if isinstance(percent, (int, float)):
        return float(percent)

    count = metric.get("count", 0)
    covered = metric.get("covered", 0)
    if not count:
        return 100.0
    return 100.0 * float(covered) / float(count)


def format_metric(metric: dict[str, Any]) -> str:
    count = int(metric.get("count", 0))
    covered = int(metric.get("covered", 0))
    return f"{covered}/{count} ({metric_percent(metric):.2f}%)"


def count_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def parse_fuzzer_log(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "executions": None,
        "final_corpus_files": None,
        "final_corpus_bytes": None,
        "recommended_dictionary": [],
    }

    done_matches = re.findall(r"Done\s+(\d+)\s+runs\s+in\s+\d+\s+second", text)
    if done_matches:
        result["executions"] = int(done_matches[-1])

    corpus_matches = re.findall(r"corp:\s*(\d+)/(\d+)b", text)
    if corpus_matches:
        corpus_files, corpus_bytes = corpus_matches[-1]
        result["final_corpus_files"] = int(corpus_files)
        result["final_corpus_bytes"] = int(corpus_bytes)

    dictionary_match = re.search(
        r"###### Recommended dictionary\. ######\n(?P<body>.*?)###### End of recommended dictionary\. ######",
        text,
        flags=re.DOTALL,
    )
    if dictionary_match:
        entries = []
        for line in dictionary_match.group("body").splitlines():
            stripped = line.strip()
            if not stripped or not stripped.startswith('"'):
                continue
            entries.append(stripped.split(" # ", 1)[0])
        result["recommended_dictionary"] = entries

    return result


def summarize_export(export_data: dict[str, Any]) -> dict[str, Any]:
    data_items = export_data.get("data") or []
    if not data_items:
        return {"totals": {}, "files": []}

    first = data_items[0]
    files = []
    for file_item in first.get("files", []):
        summary = file_item.get("summary", {})
        files.append(
            {
                "filename": file_item.get("filename", ""),
                "summary": summary,
            }
        )

    return {
        "totals": first.get("totals", {}),
        "files": sorted(files, key=lambda item: item["filename"]),
    }


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    coverage = report.get("coverage", {})
    totals = coverage.get("totals", {})
    lines = [
        f"# Coverage report: {report['target']['target_name']}",
        "",
        "## Summary",
        "",
        f"- Target config: `{report['target_config']}`",
        f"- Function: `{report['target']['function_name']}`",
        f"- Driver: `{report['driver']}`",
        f"- Fuzz seconds: `{report['fuzz_seconds']}`",
        f"- Dictionary: `{report['dictionary'] or '(none)'}`",
        f"- Build status: `{report['status']['build']}`",
        f"- Fuzz exit code: `{report['status'].get('fuzz_exit_code', 'not-run')}`",
        f"- Coverage status: `{report['status']['coverage']}`",
        f"- Run directory: `{report['paths']['run_dir']}`",
        f"- Log file: `{report['paths']['log_file']}`",
        "",
        "## Coverage Totals",
        "",
        "| Metric | Covered |",
        "| --- | ---: |",
    ]

    for key in ("lines", "functions", "regions", "branches"):
        metric = totals.get(key)
        if isinstance(metric, dict):
            lines.append(f"| {key} | {format_metric(metric)} |")

    fuzzing = report.get("fuzzing", {})
    lines.extend(
        [
            "",
            "## Fuzzing",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| executions | {fuzzing.get('executions') or 'unknown'} |",
            f"| final corpus files | {fuzzing.get('final_corpus_files') or 0} |",
            f"| final corpus bytes | {fuzzing.get('final_corpus_bytes') or 0} |",
            f"| artifact files | {fuzzing.get('artifact_files') or 0} |",
        ]
    )
    recommended_dictionary = fuzzing.get("recommended_dictionary") or []
    if recommended_dictionary:
        lines.extend(["", "Recommended dictionary entries:"])
        lines.extend(f"- `{entry}`" for entry in recommended_dictionary)

    files = coverage.get("files", [])
    if files:
        lines.extend(
            [
                "",
                "## Files",
                "",
                "| File | Lines | Functions | Regions | Branches |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for file_item in files:
            summary = file_item.get("summary", {})
            row = [file_item.get("filename", "")]
            for key in ("lines", "functions", "regions", "branches"):
                metric = summary.get(key)
                row.append(format_metric(metric) if isinstance(metric, dict) else "n/a")
            lines.append(f"| `{row[0]}` | {row[1]} | {row[2]} | {row[3]} | {row[4]} |")

    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in report["errors"])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    target_config_path = resolve_project_path(args.target_config)
    driver_path = resolve_project_path(args.driver)
    out_dir = resolve_project_path(args.out_dir)
    dictionary_path = resolve_project_path(args.dict) if args.dict else None
    target = load_target_config(target_config_path)

    if not driver_path.is_file():
        raise RuntimeError(f"driver file not found: {driver_path}")
    if dictionary_path is not None and not dictionary_path.is_file():
        raise RuntimeError(f"dictionary file not found: {dictionary_path}")

    cc = os.environ.get("CC", "clang")
    cxx = os.environ.get("CXX", "clang++")
    llvm_profdata = os.environ.get("LLVM_PROFDATA", "llvm-profdata")
    llvm_cov = os.environ.get("LLVM_COV", "llvm-cov")

    require_tool(cc)
    require_tool(cxx)
    require_tool(llvm_profdata)
    require_tool(llvm_cov)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_dir / f"{target['target_name']}-{timestamp}"
    while run_dir.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = out_dir / f"{target['target_name']}-{timestamp}"

    corpus_dir = run_dir / "corpus"
    artifacts_dir = run_dir / "artifacts"
    objects_dir = run_dir / "objects"
    html_dir = run_dir / "html"
    binary_path = run_dir / "fuzz_driver_cov"
    profraw_path = run_dir / "fuzzer.profraw"
    profdata_path = run_dir / "fuzzer.profdata"
    export_path = run_dir / "coverage_export.json"
    json_report_path = run_dir / "coverage_report.json"
    markdown_report_path = run_dir / "coverage_report.md"
    log_path = run_dir / "coverage.log"

    for directory in (corpus_dir, artifacts_dir, objects_dir):
        directory.mkdir(parents=True, exist_ok=True)

    seed_corpus = resolve_project_path(target["seed_corpus"])
    if not seed_corpus.is_dir():
        raise RuntimeError(f"seed corpus directory not found: {seed_corpus}")
    shutil.copytree(seed_corpus, corpus_dir, dirs_exist_ok=True)

    source_files = [resolve_project_path(path) for path in target["source_files"]]
    include_args = [f"-I{resolve_project_path(path)}" for path in target["include_dirs"]]
    common_flags = [
        "-g",
        "-O1",
        "-fprofile-instr-generate",
        "-fcoverage-mapping",
        "-fsanitize=address",
    ]

    report: dict[str, Any] = {
        "target_config": str(target_config_path.relative_to(ROOT) if target_config_path.is_relative_to(ROOT) else target_config_path),
        "driver": str(driver_path.relative_to(ROOT) if driver_path.is_relative_to(ROOT) else driver_path),
        "target": {
            "target_name": target["target_name"],
            "function_name": target["function_name"],
            "signature": target.get("signature"),
        },
        "fuzz_seconds": args.fuzz_seconds,
        "use_cmp": args.use_cmp,
        "dictionary": str(dictionary_path.relative_to(ROOT) if dictionary_path and dictionary_path.is_relative_to(ROOT) else dictionary_path or ""),
        "paths": {
            "run_dir": str(run_dir),
            "log_file": str(log_path),
            "json_report": str(json_report_path),
            "markdown_report": str(markdown_report_path),
            "html_report": str(html_dir),
            "profile_raw": str(profraw_path),
            "profile_data": str(profdata_path),
        },
        "status": {
            "build": "not-run",
            "coverage": "not-run",
        },
        "coverage": {
            "totals": {},
            "files": [],
        },
        "fuzzing": {
            "executions": None,
            "final_corpus_files": count_files(corpus_dir),
            "final_corpus_bytes": None,
            "artifact_files": 0,
            "recommended_dictionary": [],
        },
        "errors": [],
    }

    try:
        object_files: list[Path] = []
        for source_file in source_files:
            object_file = objects_dir / f"{source_file.name}.o"
            object_files.append(object_file)
            compiler = cc if source_file.suffix == ".c" else cxx
            command = [compiler]
            if source_file.suffix != ".c":
                command.append("-std=c++17")
            command.extend(common_flags)
            command.extend(include_args)
            command.extend(["-c", str(source_file), "-o", str(object_file)])
            run_command(command, cwd=ROOT, log_path=log_path)

        link_command = [
            cxx,
            "-std=c++17",
            *common_flags,
            *include_args,
            *(str(path) for path in object_files),
            str(driver_path),
            "-fsanitize=fuzzer,address",
            "-o",
            str(binary_path),
        ]
        run_command(link_command, cwd=ROOT, log_path=log_path)
        report["status"]["build"] = "ok"

        fuzz_args = [
            str(binary_path),
            str(corpus_dir),
            f"-max_total_time={args.fuzz_seconds}",
            f"-artifact_prefix={artifacts_dir}/",
        ]
        if dictionary_path is not None:
            fuzz_args.append(f"-dict={dictionary_path}")
        if args.use_cmp == 0:
            fuzz_args.append("-use_cmp=0")

        env = os.environ.copy()
        env["LLVM_PROFILE_FILE"] = str(profraw_path)
        completed = run_command(fuzz_args, cwd=ROOT, log_path=log_path, env=env, check=False)
        report["status"]["fuzz_exit_code"] = completed.returncode
        report["fuzzing"].update(parse_fuzzer_log(log_path.read_text(encoding="utf-8", errors="replace")))
        report["fuzzing"]["artifact_files"] = count_files(artifacts_dir)
        if report["fuzzing"]["final_corpus_files"] is None:
            report["fuzzing"]["final_corpus_files"] = count_files(corpus_dir)

        if not profraw_path.exists():
            raise RuntimeError(f"profile data was not written: {profraw_path}")

        run_command(
            [
                llvm_profdata,
                "merge",
                "-sparse",
                str(profraw_path),
                "-o",
                str(profdata_path),
            ],
            cwd=ROOT,
            log_path=log_path,
        )

        export_command = [
            llvm_cov,
            "export",
            str(binary_path),
            f"-instr-profile={profdata_path}",
            *[str(path) for path in source_files],
        ]
        with export_path.open("w", encoding="utf-8") as export_file:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n$ " + " ".join(export_command) + f" > {export_path}\n")
                log.flush()
                completed_export = subprocess.run(
                    export_command,
                    cwd=ROOT,
                    text=True,
                    stdout=export_file,
                    stderr=log,
                    check=False,
                )
                log.write(f"\n[exit code: {completed_export.returncode}]\n")
        if completed_export.returncode != 0:
            raise RuntimeError("llvm-cov export failed")

        run_command(
            [
                llvm_cov,
                "show",
                str(binary_path),
                f"-instr-profile={profdata_path}",
                "-format=html",
                f"-output-dir={html_dir}",
                *[str(path) for path in source_files],
            ],
            cwd=ROOT,
            log_path=log_path,
        )

        export_data = json.loads(export_path.read_text(encoding="utf-8"))
        report["coverage"] = summarize_export(export_data)
        report["status"]["coverage"] = "ok"
    except Exception as exc:
        report["errors"].append(str(exc))
        if report["status"]["build"] == "not-run":
            report["status"]["build"] = "failed"
        if report["status"]["coverage"] == "not-run":
            report["status"]["coverage"] = "failed"

    json_report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    write_markdown_report(report, markdown_report_path)
    return report, markdown_report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile a generated fuzz driver with LLVM coverage and write a report."
    )
    parser.add_argument(
        "--target-config",
        default="targets/cjson_parse.json",
        help="Path to target config JSON.",
    )
    parser.add_argument(
        "--driver",
        default=str(DEFAULT_DRIVER),
        help="Generated fuzz driver source file.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Directory where coverage report runs are written.",
    )
    parser.add_argument(
        "--fuzz-seconds",
        type=int,
        default=10,
        help="Short fuzz run duration for coverage collection.",
    )
    parser.add_argument(
        "--use-cmp",
        type=int,
        choices=(0, 1),
        default=0,
        help="Pass -use_cmp=0 when set to 0; leave LibFuzzer default when set to 1.",
    )
    parser.add_argument(
        "--dict",
        default=os.environ.get("FUZZ_DICT", ""),
        help="Optional LibFuzzer dictionary file.",
    )
    args = parser.parse_args()

    try:
        report, markdown_path = build_report(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"coverage report: {markdown_path}")
    print(f"json report: {report['paths']['json_report']}")
    print(f"html report: {report['paths']['html_report']}")

    if report["errors"]:
        print("coverage report completed with errors; see log for details.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
