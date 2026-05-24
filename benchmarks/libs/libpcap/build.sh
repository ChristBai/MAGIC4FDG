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

PREFIX=/opt/bench/libpcap
SRC=/tmp/libpcap-src

export CC=clang
export CXX=clang++
export CFLAGS="-g -O1 -fprofile-instr-generate -fcoverage-mapping -fsanitize=address"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-fsanitize=address"

rm -rf "$SRC"
git_clone_retry --depth 1 --branch libpcap-1.10.4 https://github.com/the-tcpdump-group/libpcap.git "$SRC"

cd "$SRC"
mkdir build && cd build
cmake .. \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBUILD_SHARED_LIBS=OFF \
  -DDISABLE_DBUS=ON \
  -DDISABLE_RDMA=ON \
  -DDISABLE_BLUETOOTH=ON \
  -DDISABLE_USB=ON
make -j2
make install

# Copy source for coverage mapping
mkdir -p "$PREFIX/src"
cp "$SRC/"*.c "$SRC/"*.h "$PREFIX/src/" 2>/dev/null || true

# Collect seed corpus from library test data
mkdir -p "$PREFIX/seed_corpus"
find "$SRC" -path "*/tests/*" -name "*.pcap" -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$SRC" -path "*/testprogs/*" -name "*.pcap" -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
find "$SRC" -name "*.pcap" -size -100k -exec cp {} "$PREFIX/seed_corpus/" \; 2>/dev/null || true
echo "[seed] Collected $(ls "$PREFIX/seed_corpus/" 2>/dev/null | wc -l) pcap seed files"

echo "=== libpcap build complete ==="
ls -la "$PREFIX/lib/libpcap.a"
