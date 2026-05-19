#!/bin/bash
# Run naive baseline on all 12 target libraries sequentially
set -uo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

FUZZ_SECONDS=60
TEMPERATURE=0.4

# No skip — rerun all 12 libraries with unified temperature
COMPLETED_LIBS=("")

echo "=== Naive Baseline: all 12 libraries ==="
echo "Fuzz duration: ${FUZZ_SECONDS}s per library"
echo ""

# Pre-flight: check Docker
if ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker is not available. Please start Docker Desktop first."
  exit 1
fi

for config_file in targets/*.json; do
  lib=$(basename "$config_file" .json)

  # Skip already completed
  skip=false
  for completed in "${COMPLETED_LIBS[@]}"; do
    if [ "$lib" = "$completed" ]; then
      skip=true
      break
    fi
  done

  if [ "$skip" = true ]; then
    echo "[SKIP] $lib — already completed"
    continue
  fi

  # Check Docker is still alive before each library
  if ! docker info >/dev/null 2>&1; then
    echo "[ERROR] Docker died before $lib. Waiting 30s for recovery..."
    sleep 30
    if ! docker info >/dev/null 2>&1; then
      echo "[ABORT] Docker still down. Stopping."
      break
    fi
  fi

  echo ""
  echo "============================================"
  echo "=== $lib — $(date) ==="
  echo "============================================"

  timeout 2400 python3 -m src.baselines.naive \
    --target-config "$config_file" \
    --fuzz-seconds "$FUZZ_SECONDS" \
    --temperature "$TEMPERATURE" \
    2>&1

  exit_code=$?
  if [ "$exit_code" -eq 124 ]; then
    echo "[TIMEOUT] $lib — exceeded 2400s"
  elif [ "$exit_code" -ne 0 ]; then
    echo "[FAILED] $lib — exit code: $exit_code"
  else
    echo "[DONE] $lib — success"
  fi

  echo ""
done

echo "=== All baselines complete ==="
echo ""

# Summary
echo "=== Results Summary ==="
for config_file in targets/*.json; do
  lib=$(basename "$config_file" .json)
  result_file="generated/baseline/$lib/result.json"
  if [ -f "$result_file" ]; then
    line_pct=$(python3 -c "import json; d=json.load(open('$result_file')); print(f'{d.get(\"coverage_pct\", 0):.1f}%')" 2>/dev/null || echo "N/A")
    branch_pct=$(python3 -c "import json; d=json.load(open('$result_file')); print(f'{d.get(\"branch_coverage_pct\", 0):.1f}%')" 2>/dev/null || echo "N/A")
    echo "  $lib: lines=$line_pct branches=$branch_pct"
  else
    echo "  $lib: NO RESULT"
  fi
done
