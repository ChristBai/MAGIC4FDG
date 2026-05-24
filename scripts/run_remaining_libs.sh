#!/bin/bash
# 串行跑剩余库（libpcap 已在跑，跳过）
# 配置：max-rounds 10, target-coverage 100, fuzz-seconds 60

set -e
cd "$(dirname "$0")/.."

LIBS=(cjson harfbuzz jsoncpp libjpeg_turbo libpng libxml2 mbedtls openssl re2 woff2 zlib)
MAX_ROUNDS=10
TARGET_COV=100
FUZZ_SEC=60

# 等待 libpcap 进程结束
echo "[$(date '+%H:%M:%S')] Waiting for libpcap pipeline to finish..."
while pgrep -f "supervisor.*libpcap" > /dev/null 2>&1; do
    sleep 30
done
echo "[$(date '+%H:%M:%S')] libpcap done."

for lib in "${LIBS[@]}"; do
    TARGET="targets/${lib}.json"
    if [ ! -f "$TARGET" ]; then
        # 尝试带连字符的名称
        TARGET="targets/$(echo $lib | tr '_' '-').json"
    fi
    if [ ! -f "$TARGET" ]; then
        echo "[$(date '+%H:%M:%S')] SKIP $lib: target config not found"
        continue
    fi

    echo ""
    echo "=============================================="
    echo "[$(date '+%H:%M:%S')] START: $lib"
    echo "=============================================="

    # 清除旧的 build cache 以触发 seed corpus 收集
    rm -rf "generated/build_cache/$lib"

    PYTHONUNBUFFERED=1 python3 -m src.pipeline.supervisor \
        --target-config "$TARGET" \
        --max-rounds "$MAX_ROUNDS" \
        --target-coverage "$TARGET_COV" \
        --fuzz-seconds "$FUZZ_SEC" \
        2>&1 | tee "generated/run_${lib}.log"

    echo "[$(date '+%H:%M:%S')] DONE: $lib"
done

echo ""
echo "=============================================="
echo "[$(date '+%H:%M:%S')] ALL LIBRARIES COMPLETE"
echo "=============================================="
