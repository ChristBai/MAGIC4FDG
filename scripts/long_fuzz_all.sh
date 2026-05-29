#!/bin/bash
# Long-duration fuzzing (10 hours) for multiple libraries using best drivers.
# Launches one Docker container per library, all running in parallel.
#
# Usage: ./scripts/long_fuzz_all.sh [cjson libpng mbedtls re2]
# Default: all four libraries
#
# Monitor:  docker logs -f magic4fdg-long-fuzz-<lib>
# Stop all: docker stop $(docker ps -q --filter "name=magic4fdg-long-fuzz")
# Results:  generated/long_fuzz_<lib>/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FUZZ_SECONDS=$((10 * 3600))

if [ $# -gt 0 ]; then
    LIBS="$@"
else
    LIBS="cjson libpng mbedtls re2"
fi

get_config() {
    local lib=$1
    case "$lib" in
        cjson)
            INCLUDES="-I/opt/bench/cjson/include/cjson"
            STATIC_LIBS="/opt/bench/cjson/lib/libcjson.a"
            LINK_FLAGS=""
            DICT="benchmarks/libs/cjson/json.dict"
            ;;
        libpng)
            INCLUDES="-I/opt/bench/libpng/include/libpng16 -I/opt/bench/zlib/include"
            STATIC_LIBS="/opt/bench/libpng/lib/libpng16.a /opt/bench/zlib/lib/libz.a"
            LINK_FLAGS="-lm"
            DICT="benchmarks/libs/libpng/png.dict"
            ;;
        mbedtls)
            INCLUDES="-I/opt/bench/mbedtls/include"
            STATIC_LIBS="/opt/bench/mbedtls/lib/libmbedtls.a /opt/bench/mbedtls/lib/libmbedx509.a /opt/bench/mbedtls/lib/libmbedcrypto.a"
            LINK_FLAGS=""
            DICT="benchmarks/libs/mbedtls/mbedtls.dict"
            ;;
        re2)
            INCLUDES="-I/opt/bench/re2/include -I/opt/bench/abseil/include"
            STATIC_LIBS="/opt/bench/re2/lib/libre2.a /opt/bench/re2/lib/libabsl_all.a"
            LINK_FLAGS="-lpthread -lstdc++"
            DICT="benchmarks/libs/re2/re2.dict"
            ;;
    esac
}

echo "=== MAGIC4FDG Long Fuzz (10 hours) ==="
echo "Libraries: $LIBS"
echo "Duration: ${FUZZ_SECONDS}s per library"
echo ""

for lib in $LIBS; do
    get_config "$lib"

    CONTAINER_NAME="magic4fdg-long-fuzz-${lib}"
    DRIVERS_DIR="$PROJECT_DIR/generated/iterations/$lib/latest/best_drivers"
    OUTPUT_DIR="$PROJECT_DIR/generated/long_fuzz_$lib"
    BUILD_CACHE="$PROJECT_DIR/generated/build_cache/$lib"

    if [ ! -d "$DRIVERS_DIR" ]; then
        echo "SKIP $lib: no best_drivers at $DRIVERS_DIR"
        continue
    fi
    if [ ! -d "$BUILD_CACHE" ]; then
        echo "SKIP $lib: no build_cache at $BUILD_CACHE"
        continue
    fi

    DRIVER_COUNT=$(ls "$DRIVERS_DIR"/*.cpp 2>/dev/null | wc -l | tr -d ' ')
    echo "--- $lib: $DRIVER_COUNT drivers ---"

    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

    mkdir -p "$OUTPUT_DIR"/{crashes,corpus,logs}

    STAGING="$PROJECT_DIR/generated/long_fuzz_staging_$lib"
    rm -rf "$STAGING"
    mkdir -p "$STAGING/drivers" "$STAGING/seed_corpus"
    cp "$DRIVERS_DIR"/*.cpp "$STAGING/drivers/"
    cp "$PROJECT_DIR/benchmarks/libs/$lib/seed_corpus"/* "$STAGING/seed_corpus/" 2>/dev/null || true
    cp "$PROJECT_DIR/generated/accumulated_corpus/$lib"/* "$STAGING/seed_corpus/" 2>/dev/null || true

    # Generate in-container script
    cat > "$STAGING/run.sh" << INNEREOF
#!/bin/bash
set -e

CXX=clang++
FUZZ_SECONDS=${FUZZ_SECONDS}
INCLUDES="${INCLUDES}"
LIBS="${STATIC_LIBS}"
LINK_FLAGS="${LINK_FLAGS}"
DICT_FLAG=""
if [ -f "/workspace/${DICT}" ]; then
    DICT_FLAG="-dict=/workspace/${DICT}"
fi

echo "[*] Long fuzz: $lib (\${FUZZ_SECONDS}s)"
echo "[*] Compiling drivers..."

mkdir -p /tmp/binaries /tmp/crashes /tmp/logs

COMPILED=0
for driver in /workspace/generated/long_fuzz_staging_$lib/drivers/*.cpp; do
    name=\$(basename "\$driver" .cpp)
    if \$CXX -std=c++17 -g -O1 \\
        -fsanitize=fuzzer,address,undefined \\
        -fno-sanitize-recover=undefined \\
        \$INCLUDES \\
        "\$driver" \$LIBS \$LINK_FLAGS \\
        -o "/tmp/binaries/\$name" 2>"/tmp/logs/\${name}_compile.log"; then
        COMPILED=\$((COMPILED + 1))
        echo "  OK: \$name"
    else
        echo "  FAIL: \$name"
    fi
done

echo "[*] Compiled \$COMPILED drivers"

if [ "\$COMPILED" -eq 0 ]; then
    echo "ERROR: No drivers compiled"
    exit 1
fi

for binary in /tmp/binaries/*; do
    name=\$(basename "\$binary")
    mkdir -p "/tmp/corpus_\$name"
    cp /workspace/generated/long_fuzz_staging_$lib/seed_corpus/* "/tmp/corpus_\$name/" 2>/dev/null || true
done

echo "[*] Starting \$COMPILED fuzzers for \${FUZZ_SECONDS}s..."

PIDS=""
for binary in /tmp/binaries/*; do
    name=\$(basename "\$binary")
    mkdir -p "/tmp/crashes/\$name"

    ASAN_OPTIONS="halt_on_error=0:exitcode=0:detect_leaks=1:print_stacktrace=1:log_path=/tmp/logs/\${name}_asan" \\
    UBSAN_OPTIONS="halt_on_error=0:print_stacktrace=1" \\
    "\$binary" "/tmp/corpus_\$name" \\
        -max_total_time=\$FUZZ_SECONDS \\
        -artifact_prefix="/tmp/crashes/\${name}/" \\
        -use_cmp=1 \\
        -use_value_profile=1 \\
        \$DICT_FLAG \\
        -print_final_stats=1 \\
        > "/tmp/logs/\${name}_fuzz.log" 2>&1 &
    PID=\$!
    PIDS="\$PIDS \$PID"
    echo "  Started: \$name (PID=\$PID)"
done

echo "[*] All fuzzers running."

# Status every 30 min
(
    elapsed=0
    interval=1800
    while [ \$elapsed -lt \$FUZZ_SECONDS ]; do
        sleep \$interval
        elapsed=\$((elapsed + interval))
        hours=\$((elapsed / 3600))
        mins=\$(( (elapsed % 3600) / 60 ))
        echo ""
        echo "=== Status [\${hours}h\${mins}m] ==="
        CRASHES_SO_FAR=\$(find /tmp/crashes -type f \( -name "crash-*" -o -name "leak-*" \) 2>/dev/null | wc -l)
        echo "  Crashes: \$CRASHES_SO_FAR"
        for binary in /tmp/binaries/*; do
            name=\$(basename "\$binary")
            corpus_count=\$(ls "/tmp/corpus_\$name" 2>/dev/null | wc -l)
            echo "  \$name: corpus=\$corpus_count"
        done
    done
) &
STATUS_PID=\$!

for pid in \$PIDS; do
    wait \$pid 2>/dev/null || true
done
kill \$STATUS_PID 2>/dev/null || true

echo ""
echo "[*] Fuzzing complete. Collecting results..."

OUTDIR="/workspace/generated/long_fuzz_$lib"
rm -rf "\$OUTDIR/crashes" "\$OUTDIR/logs"
mkdir -p "\$OUTDIR/crashes" "\$OUTDIR/corpus" "\$OUTDIR/logs"
cp -r /tmp/crashes/* "\$OUTDIR/crashes/" 2>/dev/null || true
for d in /tmp/corpus_*; do
    cp "\$d"/* "\$OUTDIR/corpus/" 2>/dev/null || true
done
cp /tmp/logs/* "\$OUTDIR/logs/" 2>/dev/null || true

echo ""
echo "=========================================="
echo "=== FINAL RESULTS: $lib ==="
echo "=========================================="
TOTAL_CRASHES=\$(find /tmp/crashes -type f -name "crash-*" | wc -l)
TOTAL_LEAKS=\$(find /tmp/crashes -type f -name "leak-*" | wc -l)
TOTAL_CORPUS=0
for d in /tmp/corpus_*; do
    count=\$(ls "\$d" 2>/dev/null | wc -l)
    TOTAL_CORPUS=\$((TOTAL_CORPUS + count))
done

echo "  Crashes: \$TOTAL_CRASHES"
echo "  Leaks: \$TOTAL_LEAKS"
echo "  Total corpus: \$TOTAL_CORPUS"

if [ "\$TOTAL_CRASHES" -gt 0 ] || [ "\$TOTAL_LEAKS" -gt 0 ]; then
    echo ""
    echo "  Defect files:"
    find /tmp/crashes -type f \( -name "crash-*" -o -name "leak-*" \) | sort | while read f; do
        driver=\$(basename \$(dirname "\$f"))
        echo "    [\$driver] \$(basename \$f)"
    done
fi

echo ""
echo "LONG_FUZZ_DONE"
INNEREOF
    chmod +x "$STAGING/run.sh"

    docker run -d \
        --name "$CONTAINER_NAME" \
        --memory 8g \
        --cpus 4 \
        -v "$PROJECT_DIR/generated:/workspace/generated" \
        -v "$PROJECT_DIR/benchmarks:/workspace/benchmarks:ro" \
        -v "$BUILD_CACHE:/opt/bench:ro" \
        -w /workspace \
        magic4fdg:latest \
        bash "/workspace/generated/long_fuzz_staging_$lib/run.sh"

    echo "  Container: $CONTAINER_NAME"
    echo "  Monitor: docker logs -f $CONTAINER_NAME"
    echo ""
done

echo "=== All containers launched ==="
echo "Stop all: docker stop \$(docker ps -q --filter 'name=magic4fdg-long-fuzz')"
echo "Results:  generated/long_fuzz_<lib>/"
