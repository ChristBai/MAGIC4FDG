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

BROTLI_PREFIX=/opt/bench/brotli
WOFF2_PREFIX=/opt/bench/woff2
BROTLI_SRC=/tmp/brotli-src
WOFF2_SRC=/tmp/woff2-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

# ---- Build brotli first ----
rm -rf "$BROTLI_SRC"
git_clone_retry --depth 1 --branch v1.0.9 https://github.com/google/brotli.git "$BROTLI_SRC"

cd "$BROTLI_SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$BROTLI_PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DBROTLI_DISABLE_TESTS=ON
make -j2
make install

# ---- Build woff2 ----
rm -rf "$WOFF2_SRC"
git_clone_retry --depth 1 --branch v1.0.2 https://github.com/google/woff2.git "$WOFF2_SRC"

cd "$WOFF2_SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$WOFF2_PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DCMAKE_PREFIX_PATH="$BROTLI_PREFIX"
make -j2
make install

# Copy brotli headers into woff2 prefix so the target config can find them
mkdir -p "$WOFF2_PREFIX/include/brotli"
cp "$BROTLI_PREFIX/include/brotli/"*.h "$WOFF2_PREFIX/include/brotli/" 2>/dev/null || true

# Also copy brotli static libs alongside woff2 libs (needed at link time)
# v1.0.9 names them libbrotli*-static.a, rename to libbrotli*.a for consistency
find "$BROTLI_PREFIX" -name "libbrotli*.a" -exec cp {} "$WOFF2_PREFIX/lib/" \; 2>/dev/null || true
cd "$WOFF2_PREFIX/lib"
for f in libbrotli*-static.a; do
  [ -f "$f" ] && cp "$f" "${f/-static/}"
done

# Copy source for coverage mapping
mkdir -p "$WOFF2_PREFIX/src"
cp "$WOFF2_SRC/src/"*.cc "$WOFF2_SRC/src/"*.h "$WOFF2_PREFIX/src/" 2>/dev/null || true

echo "=== woff2 build complete ==="
ls -la "$WOFF2_PREFIX/lib/libwoff2"*.a
