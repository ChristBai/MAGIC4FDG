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

PREFIX=/opt/bench/libxml2
SRC=/tmp/libxml2-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch v2.12.6 https://github.com/GNOME/libxml2.git "$SRC"

cd "$SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DLIBXML2_WITH_PYTHON=OFF \
  -DLIBXML2_WITH_ICU=OFF \
  -DLIBXML2_WITH_LZMA=OFF \
  -DLIBXML2_WITH_ZLIB=OFF \
  -DLIBXML2_WITH_TESTS=OFF \
  -DLIBXML2_WITH_PROGRAMS=OFF
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp "$SRC/"*.c "$SRC/"*.h "$PREFIX/src/" 2>/dev/null || true
cp "$SRC/include/libxml/"*.h "$PREFIX/src/" 2>/dev/null || true

# Collect seed corpus from library test data
mkdir -p "$PREFIX/seed_corpus"
find "$SRC" -path "*/test/*" -name "*.xml" -size -50k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$SRC" -path "*/result/*" -name "*.xml" -size -50k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$SRC" -name "*.html" -size -50k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$SRC" -name "*.dtd" -size -50k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
echo "[seed] Collected $(ls "$PREFIX/seed_corpus/" 2>/dev/null | wc -l) XML seed files"

echo "=== libxml2 build complete ==="
ls -la "$PREFIX/lib/libxml2.a"
