#!/bin/bash
# MAGIC4FDG v2 multi-agent pipeline: run all target libraries
# Unified params: temperature=0.4, fuzz_seconds=60, model=claude-opus-4-6
set -uo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

FUZZ_SECONDS=60
MAX_ROUNDS=10
TARGET_COVERAGE=100
PER_LIB_TIMEOUT=3600

echo "=== MAGIC4FDG v2 Pipeline: all target libraries ==="
echo "Fuzz duration: ${FUZZ_SECONDS}s | Max rounds: ${MAX_ROUNDS} | Target: ${TARGET_COVERAGE}%"
echo "Per-library timeout: ${PER_LIB_TIMEOUT}s"
echo ""

# Pre-flight: check Docker
if ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker is not available. Please start Docker Desktop first."
  exit 1
fi

# Pre-flight: check image exists
if ! docker images magic4fdg:latest --format '{{.Repository}}' | grep -q magic4fdg; then
  echo "[INFO] Building magic4fdg Docker image..."
  ./scripts/docker_build.sh 2>&1 | tail -3
fi

SUCCEEDED=()
FAILED=()

# Skip libraries that already have results from this run
SKIP_LIBS=("")

for config_file in targets/*.json; do
  lib=$(basename "$config_file" .json)

  # Skip already completed
  skip=false
  for completed in "${SKIP_LIBS[@]}"; do
    if [ "$lib" = "$completed" ]; then
      skip=true
      break
    fi
  done
  if [ "$skip" = true ]; then
    echo "[SKIP] $lib — already completed"
    continue
  fi

  # Check Docker health
  if ! docker info >/dev/null 2>&1; then
    echo "[ERROR] Docker died before $lib. Waiting 30s..."
    sleep 30
    if ! docker info >/dev/null 2>&1; then
      echo "[ABORT] Docker still down."
      break
    fi
  fi

  echo ""
  echo "============================================"
  echo "=== $lib — $(date) ==="
  echo "============================================"

  timeout "$PER_LIB_TIMEOUT" python3 -m src.pipeline.supervisor \
    --target-config "$config_file" \
    --max-rounds "$MAX_ROUNDS" \
    --target-coverage "$TARGET_COVERAGE" \
    --fuzz-seconds "$FUZZ_SECONDS" \
    2>&1

  exit_code=$?
  if [ "$exit_code" -eq 124 ]; then
    echo "[TIMEOUT] $lib — exceeded ${PER_LIB_TIMEOUT}s"
    FAILED+=("$lib(timeout)")
  elif [ "$exit_code" -ne 0 ]; then
    echo "[FAILED] $lib — exit code: $exit_code"
    FAILED+=("$lib(exit=$exit_code)")
  else
    echo "[DONE] $lib — success"
    SUCCEEDED+=("$lib")
  fi

  echo ""
done

echo ""
echo "============================================"
echo "=== Experiment Complete ==="
echo "============================================"
echo "Succeeded (${#SUCCEEDED[@]}): ${SUCCEEDED[*]:-none}"
echo "Failed (${#FAILED[@]}): ${FAILED[*]:-none}"
echo ""

# Coverage summary
echo "=== Coverage Summary ==="
for config_file in targets/*.json; do
  lib=$(basename "$config_file" .json)
  latest_dir="generated/iterations/$lib/latest"
  report_file="$latest_dir/report.json"
  if [ -f "$report_file" ]; then
    python3 -c "
import json
d = json.load(open('$report_file'))
line = d.get('best_coverage', 0)
branch = d.get('best_branch_coverage', d.get('branch_coverage_pct', 0))
tokens = d.get('token_usage', {}).get('total_tokens', 'N/A')
print(f'  $lib: line={line:.1f}% branch={branch:.1f}% tokens={tokens}')
" 2>/dev/null || echo "  $lib: parse error"
  else
    echo "  $lib: NO RESULT"
  fi
done
