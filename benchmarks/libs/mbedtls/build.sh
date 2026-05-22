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

PREFIX=/opt/bench/mbedtls
SRC=/tmp/mbedtls-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch v3.5.2 https://github.com/Mbed-TLS/mbedtls.git "$SRC"

cd "$SRC"
# mbedtls bundles its own submodules for framework and TF-PSA-Crypto
# For v3.5.x these are not yet split out, so no submodule init needed.

mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DUSE_SHARED_MBEDTLS_LIBRARY=OFF \
  -DUSE_STATIC_MBEDTLS_LIBRARY=ON \
  -DENABLE_TESTING=OFF \
  -DENABLE_PROGRAMS=OFF
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp "$SRC/library/"*.c "$PREFIX/src/" 2>/dev/null || true
cp -r "$SRC/include/mbedtls/"*.h "$PREFIX/src/" 2>/dev/null || true

echo "=== mbedtls build complete ==="
ls -la "$PREFIX/lib/libmbedcrypto.a" "$PREFIX/lib/libmbedx509.a" "$PREFIX/lib/libmbedtls.a"
