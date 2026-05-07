#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build"
TARGET="${BUILD_DIR}/tiny_fuzzer_fallback"
CORPUS="${ROOT_DIR}/examples/tiny_lib/seed_corpus"
CXX="${CXX:-clang++}"

if ! command -v "${CXX}" >/dev/null 2>&1; then
  echo "error: clang++ not found. Set CXX=/path/to/clang++ or install clang." >&2
  exit 1
fi

"${CXX}" --version
mkdir -p "${BUILD_DIR}"

"${CXX}" \
  -std=c++17 \
  -g \
  -O1 \
  -fsanitize=fuzzer-no-link,address \
  -I"${ROOT_DIR}/examples/tiny_lib" \
  "${ROOT_DIR}/examples/tiny_lib/tiny.cpp" \
  "${ROOT_DIR}/generated/fuzz_driver.cpp" \
  "${ROOT_DIR}/scripts/local_fuzzer_main.cpp" \
  -o "${TARGET}"

"${TARGET}" "${CORPUS}" -max_total_time=10
