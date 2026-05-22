#!/bin/bash
set -e

git_clone_retry() {
  git config --global http.postBuffer 524288000
  git config --global http.lowSpeedLimit 1000
  git config --global http.lowSpeedTime 30
  for i in 1 2 3; do
    git clone "$@" && return 0
    echo "[RETRY] git clone attempt $i failed, retrying in 3s..."
    sleep 3
  done
  echo "[ERROR] git clone failed after 3 attempts"
  exit 1
}

PREFIX=/opt/bench/openssl
SRC=/tmp/openssl-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch openssl-3.2.1 https://github.com/openssl/openssl.git "$SRC"

cd "$SRC"

# OpenSSL uses its own Configure script, not cmake
# Pass compiler and flags via environment; use enable-asan is unreliable,
# so we inject flags directly.
./Configure \
  --prefix="$PREFIX" \
  no-shared \
  no-tests \
  no-ui-console \
  CC="clang" \
  CXX="clang++" \
  CFLAGS="$CFLAGS" \
  CXXFLAGS="$CXXFLAGS" \
  LDFLAGS="-fsanitize=address"

make -j2
make install_sw  # install_sw skips man pages

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp "$SRC/crypto/"*.c "$PREFIX/src/" 2>/dev/null || true
cp "$SRC/ssl/"*.c "$PREFIX/src/" 2>/dev/null || true

echo "=== openssl build complete ==="
ls -la "$PREFIX/lib64/libcrypto.a" "$PREFIX/lib64/libssl.a" 2>/dev/null || \
ls -la "$PREFIX/lib/libcrypto.a" "$PREFIX/lib/libssl.a" 2>/dev/null

# On some platforms OpenSSL installs to lib/ instead of lib64/
# Create lib64 symlink if needed
if [ ! -d "$PREFIX/lib64" ] && [ -d "$PREFIX/lib" ]; then
  ln -sf "$PREFIX/lib" "$PREFIX/lib64"
fi
