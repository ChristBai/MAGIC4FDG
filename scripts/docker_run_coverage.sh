#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker run --rm \
  --memory="${DOCKER_MEMORY:-4g}" \
  --cpus="${DOCKER_CPUS:-2}" \
  -e "FUZZ_USE_CMP=${FUZZ_USE_CMP:-0}" \
  -e "TARGET_CONFIG=${TARGET_CONFIG:-targets/cjson_parse.json}" \
  -e "FUZZ_SECONDS=${FUZZ_SECONDS:-10}" \
  -e "FUZZ_DICT=${FUZZ_DICT:-}" \
  -v "${ROOT_DIR}:/workspace" \
  -w /workspace \
  magic4fdg:latest \
  python3 src/generate_coverage_report.py \
    --target-config "${TARGET_CONFIG:-targets/cjson_parse.json}" \
    --fuzz-seconds "${FUZZ_SECONDS:-10}" \
    --use-cmp "${FUZZ_USE_CMP:-0}" \
    --dict "${FUZZ_DICT:-}"
