FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

ARG APT_MIRROR=""

RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list && \
      sed -i "s|http://ports.ubuntu.com/ubuntu-ports|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get -o Acquire::Retries=5 update \
    && for i in 1 2 3; do \
         apt-get install -y --no-install-recommends --fix-missing \
           build-essential clang cmake git lld llvm ninja-build \
           python3 python3-pip pkg-config autoconf automake libtool \
           meson wget ca-certificates flex bison yasm nasm zlib1g-dev \
         && break || { echo "apt-get attempt $i failed, retrying..."; sleep 5; apt-get update; }; \
       done \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY . /workspace

ARG PIP_INDEX_URL=""
RUN if [ -n "${PIP_INDEX_URL}" ]; then \
      pip config set global.index-url "${PIP_INDEX_URL}"; \
    fi \
    && pip install --no-cache-dir -e . 2>/dev/null || true
