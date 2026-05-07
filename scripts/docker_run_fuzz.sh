#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker run --rm \
  -v "${ROOT_DIR}:/workspace" \
  -w /workspace \
  fuzz-driver-gen-mvp:latest \
  bash scripts/build_and_run.sh
