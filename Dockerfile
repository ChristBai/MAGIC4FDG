FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -i 's|http://ports.ubuntu.com/ubuntu-ports|http://mirrors.ustc.edu.cn/ubuntu-ports|g' /etc/apt/sources.list \
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
