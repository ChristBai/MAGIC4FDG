#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS_DIR="${ROOT_DIR}/generated/runs"

if [[ -d "${RUNS_DIR}" ]]; then
  rm -rf "${RUNS_DIR}"
fi

mkdir -p "${RUNS_DIR}"
echo "cleaned ${RUNS_DIR}"
