#!/bin/bash
set -e

git_clone_retry() {
  git config --global http.postBuffer 524288000
  git config --global http.lowSpeedLimit 1000
  git config --global http.lowSpeedTime 30
  git config --global http.sslVerify false
  for i in 1 2 3; do
    git clone "$@" && return 0
    echo "[RETRY] git clone attempt $i failed, retrying in 3s..."
    sleep 3
  done
  echo "[ERROR] git clone failed after 3 attempts"
  exit 1
}

PREFIX=/opt/bench/harfbuzz
SRC=/tmp/harfbuzz-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch 8.3.0 https://github.com/harfbuzz/harfbuzz.git "$SRC"

cd "$SRC"
meson setup build \
  --prefix="$PREFIX" \
  --default-library=static \
  -Dicu=disabled \
  -Dfreetype=disabled \
  -Dcairo=disabled \
  -Dglib=disabled \
  -Dgobject=disabled \
  -Dtests=disabled \
  -Ddocs=disabled \
  -Dbenchmark=disabled

ninja -j2 -C build
ninja -C build install

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp -r "$SRC/src/"*.cc "$SRC/src/"*.hh "$SRC/src/"*.h "$PREFIX/src/" 2>/dev/null || true

# Ensure lib is at a fixed path regardless of architecture
find "$PREFIX/lib" -name "libharfbuzz.a" -exec cp {} "$PREFIX/lib/libharfbuzz.a" \; 2>/dev/null || true

echo "=== harfbuzz build complete ==="
ls -la "$PREFIX/lib/libharfbuzz.a"
