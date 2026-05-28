#!/bin/bash
# Long-duration fuzzing (10 hours) for cjson using best drivers.
# Goal: find new defects (memory bugs, crashes, undefined behavior).
#
# Usage: ./scripts/long_fuzz_cjson.sh
# Requires: Docker running, magic4fdg:latest image built.
#
# Runs in detached mode — use `docker logs -f magic4fdg-long-fuzz-cjson` to monitor.
# Output: generated/long_fuzz_cjson/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DRIVERS_DIR="$PROJECT_DIR/generated/iterations/cjson/latest/best_drivers"
OUTPUT_DIR="$PROJECT_DIR/generated/long_fuzz_cjson"
FUZZ_SECONDS=$((10 * 3600))  # 10 hours

echo "=== MAGIC4FDG Long Fuzz: cjson ==="
echo "Duration: ${FUZZ_SECONDS}s (10 hours)"
echo "Drivers: $(ls "$DRIVERS_DIR"/*.cpp | wc -l | tr -d ' ')"
echo "Output: $OUTPUT_DIR"
echo ""

# Prepare output directories
mkdir -p "$OUTPUT_DIR"/{crashes,corpus,logs}

# Copy drivers to generated/ for Docker access
mkdir -p "$PROJECT_DIR/generated/long_fuzz_drivers"
cp "$DRIVERS_DIR"/*.cpp "$PROJECT_DIR/generated/long_fuzz_drivers/"

# Copy seed corpus
mkdir -p "$PROJECT_DIR/generated/long_fuzz_drivers/seed_corpus"
cp "$PROJECT_DIR/benchmarks/libs/cjson/seed_corpus"/* \
   "$PROJECT_DIR/generated/long_fuzz_drivers/seed_corpus/" 2>/dev/null || true
cp "$PROJECT_DIR/generated/accumulated_corpus/cjson"/* \
   "$PROJECT_DIR/generated/long_fuzz_drivers/seed_corpus/" 2>/dev/null || true

# Generate the in-container script
cat > "$PROJECT_DIR/generated/long_fuzz_script.sh" << 'INNEREOF'
#!/bin/bash
set -e

CXX="${CXX:-clang++}"
FUZZ_SECONDS="${FUZZ_SECONDS:-36000}"

echo "[*] Compiling drivers with ASan + UBSan..."

DRIVERS_DIR="/workspace/generated/long_fuzz_drivers"
INCLUDE="-I/opt/bench/cjson/include/cjson"
LIBS="/opt/bench/cjson/lib/libcjson.a"

mkdir -p /tmp/binaries /tmp/crashes /tmp/logs

# Compile each driver
COMPILED=0
for driver in "$DRIVERS_DIR"/*.cpp; do
    name=$(basename "$driver" .cpp)
    echo "  Compiling: $name"
    if $CXX -std=c++17 -g -O1 \
        -fsanitize=fuzzer,address,undefined \
        -fno-sanitize-recover=undefined \
        $INCLUDE \
        "$driver" $LIBS \
        -o "/tmp/binaries/$name" 2>"/tmp/logs/${name}_compile.log"; then
        COMPILED=$((COMPILED + 1))
    else
        echo "  FAILED: $name (see /tmp/logs/${name}_compile.log)"
        cat "/tmp/logs/${name}_compile.log"
    fi
done

echo "[*] Compiled $COMPILED drivers successfully"

if [ "$COMPILED" -eq 0 ]; then
    echo "ERROR: No drivers compiled"
    exit 1
fi

# Each driver gets its own corpus dir (shared seed, independent exploration)
echo "[*] Preparing per-driver corpus..."
for binary in /tmp/binaries/*; do
    name=$(basename "$binary")
    mkdir -p "/tmp/corpus_$name"
    cp /workspace/generated/long_fuzz_drivers/seed_corpus/* "/tmp/corpus_$name/" 2>/dev/null || true
done

echo "[*] Starting $COMPILED fuzzers for ${FUZZ_SECONDS}s (no fork mode)..."
echo ""

# Run each fuzzer directly (no -fork) with halt_on_error=0 so it continues after crashes.
# Each fuzzer saves crash artifacts to its own dir.
PIDS=""
for binary in /tmp/binaries/*; do
    name=$(basename "$binary")
    mkdir -p "/tmp/crashes/$name"
    echo "  Starting: $name (PID will follow)"

    ASAN_OPTIONS="halt_on_error=0:exitcode=0:detect_leaks=1:print_stacktrace=1:log_path=/tmp/logs/${name}_asan" \
    UBSAN_OPTIONS="halt_on_error=0:print_stacktrace=1" \
    "$binary" "/tmp/corpus_$name" \
        -max_total_time=$FUZZ_SECONDS \
        -artifact_prefix="/tmp/crashes/${name}/" \
        -use_cmp=1 \
        -use_value_profile=1 \
        -dict=/workspace/benchmarks/libs/cjson/json.dict \
        -print_final_stats=1 \
        > "/tmp/logs/${name}_fuzz.log" 2>&1 &
    PID=$!
    PIDS="$PIDS $PID"
    echo "    PID=$PID"
done

echo ""
echo "[*] All fuzzers running. PIDs:$PIDS"
echo "[*] Waiting up to ${FUZZ_SECONDS}s for completion..."
echo ""

# Periodic status (every 30 min)
(
    elapsed=0
    interval=1800
    while [ $elapsed -lt $FUZZ_SECONDS ]; do
        sleep $interval
        elapsed=$((elapsed + interval))
        hours=$((elapsed / 3600))
        mins=$(( (elapsed % 3600) / 60 ))
        echo ""
        echo "=== Status at ${hours}h${mins}m ==="
        CRASHES_SO_FAR=$(find /tmp/crashes -type f \( -name "crash-*" -o -name "leak-*" \) 2>/dev/null | wc -l)
        echo "  Crashes so far: $CRASHES_SO_FAR"
        for binary in /tmp/binaries/*; do
            name=$(basename "$binary")
            corpus_count=$(ls "/tmp/corpus_$name" 2>/dev/null | wc -l)
            echo "  $name: corpus=$corpus_count"
        done
    done
) &
STATUS_PID=$!

# Wait for all fuzzers
for pid in $PIDS; do
    wait $pid 2>/dev/null || true
done

kill $STATUS_PID 2>/dev/null || true

echo ""
echo "[*] Fuzzing complete. Collecting results..."

# Copy results back to workspace
mkdir -p /workspace/generated/long_fuzz_cjson/crashes
mkdir -p /workspace/generated/long_fuzz_cjson/corpus
mkdir -p /workspace/generated/long_fuzz_cjson/logs

cp -r /tmp/crashes/* /workspace/generated/long_fuzz_cjson/crashes/ 2>/dev/null || true
# Merge all per-driver corpus into one
for d in /tmp/corpus_*; do
    cp "$d"/* /workspace/generated/long_fuzz_cjson/corpus/ 2>/dev/null || true
done
cp /tmp/logs/* /workspace/generated/long_fuzz_cjson/logs/ 2>/dev/null || true

# Summary
echo ""
echo "=========================================="
echo "=== FINAL RESULTS ==="
echo "=========================================="
TOTAL_CRASHES=$(find /tmp/crashes -type f -name "crash-*" | wc -l)
TOTAL_LEAKS=$(find /tmp/crashes -type f -name "leak-*" | wc -l)
TOTAL_TIMEOUTS=$(find /tmp/crashes -type f -name "timeout-*" | wc -l)
TOTAL_CORPUS=0
for d in /tmp/corpus_*; do
    count=$(ls "$d" 2>/dev/null | wc -l)
    TOTAL_CORPUS=$((TOTAL_CORPUS + count))
done

echo "  Crashes: $TOTAL_CRASHES"
echo "  Leaks: $TOTAL_LEAKS"
echo "  Timeouts: $TOTAL_TIMEOUTS"
echo "  Total corpus inputs: $TOTAL_CORPUS"
echo ""

if [ "$TOTAL_CRASHES" -gt 0 ] || [ "$TOTAL_LEAKS" -gt 0 ]; then
    echo "  Defect files:"
    find /tmp/crashes -type f \( -name "crash-*" -o -name "leak-*" \) | sort | while read f; do
        driver=$(basename $(dirname "$f"))
        echo "    [$driver] $(basename $f)"
    done
fi

# Print ASan logs if any
ASAN_LOGS=$(find /tmp/logs -name "*_asan*" -size +0c 2>/dev/null)
if [ -n "$ASAN_LOGS" ]; then
    echo ""
    echo "  ASan reports found:"
    for log in $ASAN_LOGS; do
        echo "    --- $(basename $log) ---"
        head -30 "$log"
        echo "    ..."
    done
fi

echo ""
echo "LONG_FUZZ_DONE"
INNEREOF

chmod +x "$PROJECT_DIR/generated/long_fuzz_script.sh"

# Mount build cache as /opt/bench (read-only) so drivers can link against libcjson
BUILD_CACHE="$PROJECT_DIR/generated/build_cache/cjson"
if [ ! -d "$BUILD_CACHE" ]; then
    echo "ERROR: Build cache not found at $BUILD_CACHE"
    echo "Run the pipeline once first to build the library."
    exit 1
fi

echo "Starting Docker container (detached)..."
echo "Container: magic4fdg-long-fuzz-cjson"
echo ""

# Run detached with generous resources: 8GB RAM, 4 CPUs
docker run -d \
    --name magic4fdg-long-fuzz-cjson \
    --memory 8g \
    --cpus 4 \
    -e FUZZ_SECONDS=$FUZZ_SECONDS \
    -v "$PROJECT_DIR/generated:/workspace/generated" \
    -v "$PROJECT_DIR/benchmarks:/workspace/benchmarks:ro" \
    -v "$BUILD_CACHE:/opt/bench:ro" \
    -w /workspace \
    magic4fdg:latest \
    bash generated/long_fuzz_script.sh

echo ""
echo "=== Container started ==="
echo "Monitor:  docker logs -f magic4fdg-long-fuzz-cjson"
echo "Stop:     docker stop magic4fdg-long-fuzz-cjson"
echo "Results:  $OUTPUT_DIR (populated when complete)"
