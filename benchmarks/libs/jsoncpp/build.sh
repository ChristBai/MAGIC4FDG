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

PREFIX=/opt/bench/jsoncpp
SRC=/tmp/jsoncpp-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch 1.9.6 https://github.com/open-source-parsers/jsoncpp.git "$SRC"

cd "$SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DJSONCPP_WITH_TESTS=OFF \
  -DJSONCPP_WITH_POST_BUILD_UNITTEST=OFF \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp "$SRC/src/lib_json/"*.cpp "$PREFIX/src/"
cp "$SRC/include/json/"*.h "$PREFIX/src/" 2>/dev/null || true

echo "=== jsoncpp build complete ==="
ls -la "$PREFIX/lib/libjsoncpp.a" 2>/dev/null || ls -la "$PREFIX/lib64/libjsoncpp.a" 2>/dev/null || ls "$PREFIX/lib/"
