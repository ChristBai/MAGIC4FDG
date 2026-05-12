#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker run --rm \
  --memory="${DOCKER_MEMORY:-4g}" \
  --cpus="${DOCKER_CPUS:-2}" \
  -e "FUZZ_USE_CMP=${FUZZ_USE_CMP:-0}" \
  -e "TARGET_CONFIG=${TARGET_CONFIG:-targets/cjson_parse.json}" \
  -v "${ROOT_DIR}:/workspace" \
  -w /workspace \
  fuzz-driver-gen-mvp:latest \
  bash scripts/build_and_run.sh
