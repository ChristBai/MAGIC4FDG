#!/bin/bash
# 12 库串行端到端测试
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"
cd "$DIR"

TARGETS=(
  cjson harfbuzz jsoncpp libjpeg_turbo libpcap libpng
  libxml2 mbedtls openssl re2 woff2 zlib
)

echo "=== MAGIC4FDG 12-library benchmark ==="
echo "Rounds: 5, Fuzz: 60s, Started: $(date)"
echo ""

for lib in "${TARGETS[@]}"; do
  echo ">>> [$lib] Starting at $(date '+%H:%M:%S')"
  PYTHONUNBUFFERED=1 "$PYTHON" -m src.pipeline.supervisor \
    --target-config "targets/${lib}.json" \
    --max-rounds 5 \
    --target-coverage 100 \
    --fuzz-seconds 60 \
    > "generated/run_${lib}.log" 2>&1

  # 提取结果摘要
  coverage=$(grep "Best coverage:" "generated/run_${lib}.log" | awk '{print $3}')
  if [ -z "$coverage" ]; then
    coverage=$(tail -20 "generated/run_${lib}.log" | grep -o "[0-9]*\.[0-9]*%" | tail -1)
  fi
  echo "<<< [$lib] Done at $(date '+%H:%M:%S') — coverage: ${coverage:-FAILED}"
  echo ""
done

echo "=== All 12 libraries complete at $(date) ==="
