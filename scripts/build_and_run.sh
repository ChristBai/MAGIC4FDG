#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CXX="${CXX:-clang++}"

if ! command -v "${CXX}" >/dev/null 2>&1; then
  echo "error: clang++ not found. Set CXX=/path/to/clang++ or install clang." >&2
  exit 1
fi

"${CXX}" --version
cd "${ROOT_DIR}"

if ! "${CXX}" \
  -std=c++17 \
  -g \
  -O1 \
  -fsanitize=fuzzer,address \
  -Iexamples/tiny_lib \
  examples/tiny_lib/tiny.cpp \
  generated/fuzz_driver.cpp \
  -o generated/fuzz_driver; then
  echo "error: failed to compile with the real LibFuzzer runtime." >&2
  echo "hint: run ./scripts/docker_build.sh && ./scripts/docker_run_fuzz.sh for a known-good Linux LibFuzzer environment." >&2
  echo "hint: for a local smoke test only, run ./scripts/build_and_run_fallback.sh." >&2
  exit 1
fi

generated/fuzz_driver examples/tiny_lib/seed_corpus -max_total_time=10 -use_cmp=0
