FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

ARG APT_MIRROR=""

RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list && \
      sed -i "s|http://ports.ubuntu.com/ubuntu-ports|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    clang \
    cmake \
    git \
    lld \
    llvm \
    ninja-build \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY . /workspace

RUN pip install --no-cache-dir -e . 2>/dev/null || true
