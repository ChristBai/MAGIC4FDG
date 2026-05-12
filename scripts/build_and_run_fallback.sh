#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build"
TARGET_CONFIG="${TARGET_CONFIG:-targets/cjson_parse.json}"
TARGET_CONFIG_PATH="${ROOT_DIR}/${TARGET_CONFIG}"
CC="${CC:-clang}"
CXX="${CXX:-clang++}"

if [[ ! -f "${TARGET_CONFIG_PATH}" ]]; then
  echo "error: target config not found: ${TARGET_CONFIG}" >&2
  exit 1
fi

eval "$(python3 "${ROOT_DIR}/scripts/parse_target_config.py" "${TARGET_CONFIG_PATH}")"

TARGET="${BUILD_DIR}/${TARGET_NAME}_fuzzer_fallback"
CORPUS="${ROOT_DIR}/${SEED_CORPUS}"

if ! command -v "${CXX}" >/dev/null 2>&1; then
  echo "error: clang++ not found. Set CXX=/path/to/clang++ or install clang." >&2
  exit 1
fi

if ! command -v "${CC}" >/dev/null 2>&1; then
  echo "error: clang not found. Set CC=/path/to/clang or install clang." >&2
  exit 1
fi

"${CXX}" --version
"${CC}" --version
mkdir -p "${BUILD_DIR}"
cd "${ROOT_DIR}"

source "${ROOT_DIR}/scripts/compile_target.sh"
compile_target_objects "${BUILD_DIR}" -fsanitize=address

"${CXX}" \
  -std=c++17 \
  -g \
  -O1 \
  -fsanitize=fuzzer-no-link,address \
  "${INCLUDE_ARGS[@]}" \
  "${object_files[@]}" \
  "${ROOT_DIR}/generated/fuzz_driver.cpp" \
  "${ROOT_DIR}/scripts/local_fuzzer_main.cpp" \
  -o "${TARGET}"

"${TARGET}" "${CORPUS}" -max_total_time=10
