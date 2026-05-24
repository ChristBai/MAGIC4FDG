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

PREFIX=/opt/bench/zlib
SRC=/tmp/zlib-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch v1.3.1 https://github.com/madler/zlib.git "$SRC"

cd "$SRC"
./configure --prefix="$PREFIX" --static
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp "$SRC/"*.c "$SRC/"*.h "$PREFIX/src/"

# Collect seed corpus from library test data
mkdir -p "$PREFIX/seed_corpus"
find "$SRC" -name "*.gz" -size -100k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$SRC" -path "*/test/*" -type f -size -100k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
printf '\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00' > "$PREFIX/seed_corpus/minimal.gz"
echo "[seed] Collected $(ls "$PREFIX/seed_corpus/" 2>/dev/null | wc -l) zlib seed files"

echo "=== zlib build complete ==="
ls -la "$PREFIX/lib/libz.a"
