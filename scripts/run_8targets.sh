#!/bin/bash
# 串行执行 8 个目标库的 MAGIC4FDG pipeline

TARGETS=(libpcap libpng libxml2 mbedtls openssl re2 woff2 zlib)

for t in "${TARGETS[@]}"; do
  echo "========== Starting: $t =========="
  PYTHONUNBUFFERED=1 python3 -m src.pipeline.supervisor \
    --target-config "targets/${t}.json" \
    --max-rounds 10 \
    --target-coverage 100 \
    --fuzz-seconds 60
  echo "========== Finished: $t =========="
  echo ""
done
