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

ZLIB_PREFIX=/opt/bench/zlib
PNG_PREFIX=/opt/bench/libpng
ZLIB_SRC=/tmp/zlib-src
PNG_SRC=/tmp/libpng-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

# ---- Build zlib first ----
rm -rf "$ZLIB_SRC"
git_clone_retry --depth 1 --branch v1.3.1 https://github.com/madler/zlib.git "$ZLIB_SRC"

cd "$ZLIB_SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$ZLIB_PREFIX" \
  -DBUILD_SHARED_LIBS=OFF
make -j2
make install

# Copy zlib source for coverage
mkdir -p "$ZLIB_PREFIX/src"
cp "$ZLIB_SRC/"*.c "$ZLIB_SRC/"*.h "$ZLIB_PREFIX/src/" 2>/dev/null || true

# ---- Build libpng ----
rm -rf "$PNG_SRC"
git_clone_retry --depth 1 --branch v1.6.43 https://github.com/glennrp/libpng.git "$PNG_SRC"

cd "$PNG_SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$PNG_PREFIX" \
  -DPNG_SHARED=OFF \
  -DPNG_STATIC=ON \
  -DPNG_TESTS=OFF \
  -DZLIB_LIBRARY="$ZLIB_PREFIX/lib/libz.a" \
  -DZLIB_INCLUDE_DIR="$ZLIB_PREFIX/include"
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$PNG_PREFIX/src"
cp "$PNG_SRC/"*.c "$PNG_SRC/"*.h "$PNG_PREFIX/src/" 2>/dev/null || true

# Collect seed corpus from library test data
mkdir -p "$PNG_PREFIX/seed_corpus"
find "$PNG_SRC" -path "*/contrib/testpngs/*" -name "*.png" -exec cp {} "$PNG_PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$PNG_SRC" -path "*/contrib/pngsuite/*" -name "*.png" -exec cp {} "$PNG_PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$PNG_SRC" -path "*/tests/*" -name "*.png" -exec cp {} "$PNG_PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$PNG_SRC" -name "*.png" -size -100k -exec cp {} "$PNG_PREFIX/seed_corpus/" \; 2>/dev/null || true
echo "[seed] Collected $(ls "$PNG_PREFIX/seed_corpus/" 2>/dev/null | wc -l) PNG seed files"

echo "=== libpng build complete ==="
ls -la "$PNG_PREFIX/lib/libpng"*.a "$ZLIB_PREFIX/lib/libz.a"
