#!/bin/bash
# Long-time fuzzing script for FuzzForge best_drivers.
# Standalone — reads pipeline output, does not import pipeline code.
#
# Usage: ./scripts/long_fuzz.sh <target_config> [fuzz_hours] [parallel_jobs]
#   target_config : path to targets/<lib>.json
#   fuzz_hours    : fuzz duration per driver in hours (default: 1)
#   parallel_jobs : number of drivers to fuzz in parallel (default: 4)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKER_IMAGE="${DOCKER_IMAGE:-fuzzforge:latest}"
DOCKER_MEMORY="${DOCKER_MEMORY:-4g}"
DOCKER_CPUS="${DOCKER_CPUS:-2}"

# --- Argument parsing ---
TARGET_CONFIG="${1:?Usage: $0 <target_config> [fuzz_hours] [parallel_jobs]}"
FUZZ_HOURS="${2:-1}"
PARALLEL_JOBS="${3:-4}"

if [ ! -f "$TARGET_CONFIG" ]; then
    echo "ERROR: target config not found: $TARGET_CONFIG"
    exit 1
fi

# --- Read target config via python3 ---
read_json() { python3 -c "import json,sys; d=json.load(open('$TARGET_CONFIG')); print(d.get('$1',''))" ; }
read_json_list() { python3 -c "import json,sys; d=json.load(open('$TARGET_CONFIG')); print(' '.join(d.get('$1',[])))" ; }

LIB_NAME=$(read_json library_name)
HEADER=$(read_json header)
DICTIONARY=$(read_json dictionary)
STATIC_LIBS=$(read_json_list static_libs)
INCLUDE_DIRS=$(read_json_list include_dirs)
LINK_FLAGS=$(read_json_list link_flags)

DRIVERS_DIR="$PROJECT_ROOT/generated/iterations/$LIB_NAME/latest/best_drivers"
CACHE_DIR="$PROJECT_ROOT/generated/build_cache/$LIB_NAME"
CORPUS_DIR="$PROJECT_ROOT/generated/accumulated_corpus/$LIB_NAME"

if [ ! -d "$DRIVERS_DIR" ]; then
    echo "ERROR: best_drivers not found: $DRIVERS_DIR"
    echo "Run the pipeline first to generate drivers."
    exit 1
fi

if [ ! -d "$CACHE_DIR" ] || [ -z "$(ls -A "$CACHE_DIR" 2>/dev/null)" ]; then
    echo "ERROR: build cache not found: $CACHE_DIR"
    echo "Run the pipeline first to build the library."
    exit 1
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUT_DIR="$PROJECT_ROOT/generated/long_fuzz/$LIB_NAME/$TIMESTAMP"
mkdir -p "$OUT_DIR"/{crashes,corpus,logs}

FUZZ_SECONDS=$(python3 -c "import math; print(int($FUZZ_HOURS * 3600))")

echo "============================================================"
echo "Long Fuzz: $LIB_NAME"
echo "  Drivers:  $DRIVERS_DIR"
echo "  Duration: ${FUZZ_HOURS}h per driver ($FUZZ_SECONDS s)"
echo "  Parallel: $PARALLEL_JOBS"
echo "  Output:   $OUT_DIR"
echo "============================================================"

# --- Build include/lib flags ---
INCLUDE_FLAGS=""
for d in $INCLUDE_DIRS; do
    INCLUDE_FLAGS="$INCLUDE_FLAGS -I$d"
done

DICT_FLAG=""
if [ -n "$DICTIONARY" ] && [ -f "$PROJECT_ROOT/$DICTIONARY" ]; then
    DICT_FLAG="-dict=/workspace/$DICTIONARY"
fi

# --- Generate Docker script for one driver ---
generate_fuzz_script() {
    local DRIVER_NAME="$1"
    local DRIVER_REL="$2"
    cat <<SCRIPT
#!/bin/bash
set -e
CXX="\${CXX:-clang++}"

# Discover build-generated include dirs
BUILD_INCLUDES=\$(find /opt/bench -type d -name "include" 2>/dev/null | sed "s/^/-I/" | tr "\\n" " ")

# Compile driver (ASan only, no coverage — faster fuzzing)
\$CXX -std=c++17 -g -O2 -fsanitize=fuzzer,address \\
    $INCLUDE_FLAGS \$BUILD_INCLUDES \\
    "/workspace/$DRIVER_REL" $STATIC_LIBS $LINK_FLAGS \\
    -o /tmp/fuzz_${DRIVER_NAME} 2>&1
echo "COMPILE_OK"

# Prepare corpus
mkdir -p /tmp/corpus_${DRIVER_NAME} /tmp/artifacts_${DRIVER_NAME}
cp /workspace/generated/accumulated_corpus/$LIB_NAME/* /tmp/corpus_${DRIVER_NAME}/ 2>/dev/null || true

# Fuzz (normal mode — crash stops immediately and saves input)
export ASAN_OPTIONS=halt_on_error=1:detect_leaks=1:allocator_may_return_null=1
/tmp/fuzz_${DRIVER_NAME} /tmp/corpus_${DRIVER_NAME} \\
    -max_total_time=$FUZZ_SECONDS \\
    -artifact_prefix=/tmp/artifacts_${DRIVER_NAME}/ \\
    -print_final_stats=1 \\
    $DICT_FLAG 2>&1 || true

# Copy corpus back
mkdir -p /workspace/$( echo "$OUT_DIR" | sed "s|$PROJECT_ROOT/||" )/corpus
cp /tmp/corpus_${DRIVER_NAME}/* /workspace/$( echo "$OUT_DIR" | sed "s|$PROJECT_ROOT/||" )/corpus/ 2>/dev/null || true

# Copy artifacts (crashes)
if ls /tmp/artifacts_${DRIVER_NAME}/crash-* 1>/dev/null 2>&1; then
    mkdir -p /workspace/$( echo "$OUT_DIR" | sed "s|$PROJECT_ROOT/||" )/raw_crashes/${DRIVER_NAME}
    cp /tmp/artifacts_${DRIVER_NAME}/crash-* /workspace/$( echo "$OUT_DIR" | sed "s|$PROJECT_ROOT/||" )/raw_crashes/${DRIVER_NAME}/
fi

echo "FUZZ_DONE"
SCRIPT
}

# --- Run one driver ---
run_one_driver() {
    local DRIVER_PATH="$1"
    local DRIVER_NAME
    DRIVER_NAME=$(basename "$DRIVER_PATH" .cpp)
    local DRIVER_REL
    DRIVER_REL=$(echo "$DRIVER_PATH" | sed "s|$PROJECT_ROOT/||")
    local LOG_FILE="$OUT_DIR/logs/${DRIVER_NAME}.log"

    local SCRIPT_PATH="$OUT_DIR/logs/_run_${DRIVER_NAME}.sh"
    generate_fuzz_script "$DRIVER_NAME" "$DRIVER_REL" > "$SCRIPT_PATH"
    chmod +x "$SCRIPT_PATH"

    local SCRIPT_REL
    SCRIPT_REL=$(echo "$SCRIPT_PATH" | sed "s|$PROJECT_ROOT/||")

    echo "[$(date +%H:%M:%S)] Starting: $DRIVER_NAME"

    docker run --rm \
        --name "longfuzz-${DRIVER_NAME}-$$" \
        --memory "$DOCKER_MEMORY" \
        --cpus "$DOCKER_CPUS" \
        -v "$PROJECT_ROOT:/workspace" \
        -v "$CACHE_DIR:/opt/bench:ro" \
        -w /workspace \
        "$DOCKER_IMAGE" \
        bash "/workspace/$SCRIPT_REL" \
        > "$LOG_FILE" 2>&1 || true

    if grep -q "COMPILE_OK" "$LOG_FILE"; then
        echo "[$(date +%H:%M:%S)] Finished: $DRIVER_NAME ($(grep -c 'FUZZ_DONE' "$LOG_FILE" || echo 0) done)"
    else
        echo "[$(date +%H:%M:%S)] FAILED to compile: $DRIVER_NAME"
    fi
}

export -f run_one_driver generate_fuzz_script
export LIB_NAME INCLUDE_FLAGS STATIC_LIBS LINK_FLAGS DICT_FLAG
export FUZZ_SECONDS OUT_DIR PROJECT_ROOT CACHE_DIR DOCKER_IMAGE DOCKER_MEMORY DOCKER_CPUS

# --- Launch parallel fuzzing ---
DRIVER_LIST=$(find "$DRIVERS_DIR" -name "*.cpp" | sort)
DRIVER_COUNT=$(echo "$DRIVER_LIST" | wc -l | tr -d ' ')
echo ""
echo "Found $DRIVER_COUNT drivers. Launching with $PARALLEL_JOBS parallel jobs..."
echo ""

echo "$DRIVER_LIST" | xargs -P "$PARALLEL_JOBS" -I{} bash -c 'run_one_driver "$@"' _ {}

# --- Crash dedup ---
echo ""
echo "--- Crash Dedup ---"

RAW_CRASHES_DIR="$OUT_DIR/raw_crashes"
CRASH_IDX=0

if [ -d "$RAW_CRASHES_DIR" ]; then
    declare -A SEEN_HASHES
    for DRIVER_DIR in "$RAW_CRASHES_DIR"/*/; do
        [ -d "$DRIVER_DIR" ] || continue
        DRIVER_NAME=$(basename "$DRIVER_DIR")
        DRIVER_SRC="$DRIVERS_DIR/${DRIVER_NAME}.cpp"

        for CRASH_FILE in "$DRIVER_DIR"/crash-*; do
            [ -f "$CRASH_FILE" ] || continue

            # Replay to get stack trace
            TRACE=$(docker run --rm \
                --memory "$DOCKER_MEMORY" \
                -v "$PROJECT_ROOT:/workspace" \
                -v "$CACHE_DIR:/opt/bench:ro" \
                -w /workspace \
                -e "ASAN_OPTIONS=halt_on_error=1:detect_leaks=0" \
                "$DOCKER_IMAGE" \
                bash -c "
                    CXX=clang++
                    BUILD_INCLUDES=\$(find /opt/bench -type d -name 'include' 2>/dev/null | sed 's/^/-I/' | tr '\n' ' ')
                    \$CXX -std=c++17 -g -O2 -fsanitize=fuzzer,address \
                        $INCLUDE_FLAGS \$BUILD_INCLUDES \
                        /workspace/$(echo "$DRIVER_SRC" | sed "s|$PROJECT_ROOT/||") $STATIC_LIBS $LINK_FLAGS \
                        -o /tmp/replay 2>/dev/null && \
                    /tmp/replay /workspace/$(echo "$CRASH_FILE" | sed "s|$PROJECT_ROOT/||") 2>&1 || true
                " 2>&1 || true)

            # Extract top 3 stack frames for dedup
            FRAMES=$(echo "$TRACE" | grep -E '^\s*#[0-2]\s' | head -3)
            HASH=$(echo "$FRAMES" | shasum -a 256 | cut -c1-16)

            if [ -z "${SEEN_HASHES[$HASH]:-}" ]; then
                SEEN_HASHES[$HASH]=1
                CRASH_IDX=$((CRASH_IDX + 1))
                CRASH_DIR="$OUT_DIR/crashes/crash_$(printf '%03d' $CRASH_IDX)"
                mkdir -p "$CRASH_DIR"

                cp "$CRASH_FILE" "$CRASH_DIR/input.bin"
                echo "$TRACE" > "$CRASH_DIR/stacktrace.txt"
                [ -f "$DRIVER_SRC" ] && cp "$DRIVER_SRC" "$CRASH_DIR/driver.cpp"

                # Classify crash type
                CRASH_TYPE="unknown"
                if echo "$TRACE" | grep -q "heap-buffer-overflow"; then CRASH_TYPE="heap-buffer-overflow"
                elif echo "$TRACE" | grep -q "heap-use-after-free"; then CRASH_TYPE="use-after-free"
                elif echo "$TRACE" | grep -q "stack-buffer-overflow"; then CRASH_TYPE="stack-buffer-overflow"
                elif echo "$TRACE" | grep -q "SEGV"; then CRASH_TYPE="segfault"
                elif echo "$TRACE" | grep -q "null"; then CRASH_TYPE="null-deref"
                elif echo "$TRACE" | grep -q "leak"; then CRASH_TYPE="memory-leak"
                fi

                python3 -c "
import json, sys
info = {
    'crash_id': $CRASH_IDX,
    'type': '$CRASH_TYPE',
    'dedup_hash': '$HASH',
    'driver': '$DRIVER_NAME',
    'input_size': $(wc -c < "$CRASH_FILE" | tr -d ' '),
}
json.dump(info, open('$CRASH_DIR/info.json', 'w'), indent=2)
"
                echo "  Unique crash #$CRASH_IDX: $CRASH_TYPE (driver=$DRIVER_NAME, hash=$HASH)"
            fi
        done
    done
fi

# --- Generate report ---
echo ""
echo "--- Generating Report ---"

python3 -c "
import json, os, glob

out_dir = '$OUT_DIR'
crashes_dir = os.path.join(out_dir, 'crashes')
logs_dir = os.path.join(out_dir, 'logs')

# Count unique crashes
crash_dirs = sorted(glob.glob(os.path.join(crashes_dir, 'crash_*')))
crashes = []
for cd in crash_dirs:
    info_path = os.path.join(cd, 'info.json')
    if os.path.exists(info_path):
        crashes.append(json.load(open(info_path)))

# Parse logs for stats
total_execs = 0
driver_stats = []
for log_file in sorted(glob.glob(os.path.join(logs_dir, '*.log'))):
    name = os.path.basename(log_file).replace('.log', '')
    if name.startswith('_run_'):
        continue
    content = open(log_file).read()
    compiled = 'COMPILE_OK' in content
    execs = 0
    for line in content.splitlines():
        if 'stat::number_of_executed_inputs:' in line:
            try:
                execs = int(line.split(':')[-1].strip())
            except:
                pass
    total_execs += execs
    driver_stats.append({'driver': name, 'compiled': compiled, 'executions': execs})

report = {
    'library': '$LIB_NAME',
    'fuzz_hours': $FUZZ_HOURS,
    'parallel_jobs': $PARALLEL_JOBS,
    'total_drivers': len(driver_stats),
    'compiled_drivers': sum(1 for d in driver_stats if d['compiled']),
    'total_executions': total_execs,
    'unique_crashes': len(crashes),
    'crash_types': {},
    'crashes': crashes,
    'driver_stats': driver_stats,
}
for c in crashes:
    t = c.get('type', 'unknown')
    report['crash_types'][t] = report['crash_types'].get(t, 0) + 1

json.dump(report, open(os.path.join(out_dir, 'report.json'), 'w'), indent=2)
print(f'Report saved: {os.path.join(out_dir, \"report.json\")}')
print(f'  Unique crashes: {len(crashes)}')
print(f'  Total executions: {total_execs:,}')
print(f'  Drivers compiled: {sum(1 for d in driver_stats if d[\"compiled\"])}/{len(driver_stats)}')
"

# Cleanup
rm -rf "$OUT_DIR/raw_crashes" "$OUT_DIR/logs/_run_"*.sh

echo ""
echo "============================================================"
echo "Long Fuzz Complete: $LIB_NAME"
echo "  Output: $OUT_DIR"
echo "============================================================"
