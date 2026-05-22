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

ABSEIL_PREFIX=/opt/bench/abseil
RE2_PREFIX=/opt/bench/re2
ABSEIL_SRC=/tmp/abseil-src
RE2_SRC=/tmp/re2-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

# ---- Build abseil-cpp first ----
rm -rf "$ABSEIL_SRC"
git_clone_retry --depth 1 --branch 20240116.2 https://github.com/abseil/abseil-cpp.git "$ABSEIL_SRC"

cd "$ABSEIL_SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$ABSEIL_PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DBUILD_TESTING=OFF \
  -DABSL_BUILD_TESTING=OFF \
  -DABSL_PROPAGATE_CXX_STD=ON \
  -DCMAKE_CXX_STANDARD=17 \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
make -j2
make install

# ---- Build re2 ----
rm -rf "$RE2_SRC"
git_clone_retry --depth 1 --branch 2024-04-01 https://github.com/google/re2.git "$RE2_SRC"

cd "$RE2_SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$RE2_PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DRE2_BUILD_TESTING=OFF \
  -DCMAKE_CXX_STANDARD=17 \
  -DCMAKE_PREFIX_PATH="$ABSEIL_PREFIX" \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$RE2_PREFIX/src"
cp "$RE2_SRC/re2/"*.cc "$RE2_SRC/re2/"*.h "$RE2_PREFIX/src/" 2>/dev/null || true

# Merge all abseil static libs into one for simpler linking
mkdir -p /tmp/absl_objs
cd /tmp/absl_objs
for a in "$ABSEIL_PREFIX"/lib/libabsl_*.a; do
  ar x "$a"
done
ar rcs "$RE2_PREFIX/lib/libabsl_all.a" /tmp/absl_objs/*.o
rm -rf /tmp/absl_objs

echo "=== re2 build complete ==="
ls -la "$RE2_PREFIX/lib/libre2.a" "$RE2_PREFIX/lib/libabsl_all.a"
