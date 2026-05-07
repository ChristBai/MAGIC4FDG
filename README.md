# fuzz-driver-gen-mvp

一个最小可行原型，用于学习“基于 LLM 的 C/C++ LibFuzzer fuzz driver 生成”。

当前版本不会调用真实 LLM API，只会把固定 prompt 模板渲染出来，打印到终端，并保存到 `generated/prompt.txt`。`generated/fuzz_driver.cpp` 先放了一个手写、可运行的 LibFuzzer driver。

## 项目结构

```text
examples/tiny_lib/
  tiny.h
  tiny.cpp
  seed_corpus/
prompts/libfuzzer_driver_prompt.txt
src/generate_driver.py
generated/fuzz_driver.cpp
scripts/docker_build.sh
scripts/docker_run_fuzz.sh
scripts/build_and_run.sh
scripts/clean_runs.sh
```

## 生成 prompt

```bash
python3 src/generate_driver.py \
  --signature "int parse_int(const char* s)" \
  --header "tiny.h" \
  --description "Parses a null-terminated string as an integer. Aborts when input equals CRASH."
```

生成结果会保存到：

```text
generated/prompt.txt
```

## 推荐方式：Docker 运行真正 LibFuzzer

推荐使用 Docker，这样不依赖本机 `clang++` 是否自带 LibFuzzer runtime。

```bash
chmod +x scripts/*.sh
./scripts/docker_build.sh
./scripts/docker_run_fuzz.sh
```

Docker 镜像基于 Ubuntu，安装 `clang`、`llvm`、`lld`、`cmake`、`ninja-build`、`build-essential`、`python3`、`python3-pip` 和 `git`。运行时会把当前项目目录挂载到容器的 `/workspace`，然后执行 `scripts/build_and_run.sh`。

## 运行结果目录

`examples/tiny_lib/seed_corpus` 是只读初始语料目录。正式 fuzz 脚本不会把它直接传给 LibFuzzer，因为 LibFuzzer 会把新发现的输入写回第一个 corpus 目录。

每次运行都会创建独立目录：

```text
generated/runs/<timestamp>/
  corpus/
  artifacts/
  fuzz_driver
  fuzz.log
```

脚本会把 `examples/tiny_lib/seed_corpus` 复制到本次 `corpus/`，然后只让 LibFuzzer 写入这个副本。`crash-*`、`timeout-*` 等 artifact 会写入本次 `artifacts/`。完整运行日志会保存到本次 `fuzz.log`。

## 本地编译并 fuzz

需要本机安装支持真正 LibFuzzer runtime 的 `clang++`。

```bash
chmod +x scripts/build_and_run.sh
./scripts/build_and_run.sh
```

脚本会：

1. 编译 `examples/tiny_lib/tiny.cpp` 和 `generated/fuzz_driver.cpp`
2. 使用 `-fsanitize=fuzzer,address`
3. 复制 `examples/tiny_lib/seed_corpus` 到本次 run 的 `corpus/`
4. 使用 `-max_total_time=10 -artifact_prefix=<run>/artifacts/` 运行 10 秒
5. 每次运行前后统计 seed corpus 文件列表 hash，确认初始语料目录没有变化

本地脚本不会自动 fallback。如果编译失败，请优先使用 Docker 方式。

## 稳定性验证

默认 `FUZZ_USE_CMP=0`，脚本会传入 `-use_cmp=0`，用于稳定跑满 10 秒：

```bash
./scripts/build_and_run.sh
```

Docker 推荐方式同样使用这个默认值：

```bash
./scripts/docker_run_fuzz.sh
```

## crash flow 验证

如果要验证 LibFuzzer 发现 `CRASH` 后的 artifact 和日志流转，设置 `FUZZ_USE_CMP=1`。此时脚本不会传 `-use_cmp=0`：

```bash
FUZZ_USE_CMP=1 ./scripts/build_and_run.sh
```

Docker 方式：

```bash
FUZZ_USE_CMP=1 ./scripts/docker_run_fuzz.sh
```

如果发现 crash，脚本会捕获 fuzz 进程退出码，并打印：

```text
fuzz exit code
run directory
artifact directory
log file
```

## 清理运行结果

清理历史 run 目录：

```bash
./scripts/clean_runs.sh
```

这个脚本只清理 `generated/runs/`，不会删除 `generated/fuzz_driver.cpp`。

## fallback 冒烟测试

如果当前机器没有 LibFuzzer runtime，可以运行单独的 fallback 脚本：

```bash
chmod +x scripts/build_and_run_fallback.sh
./scripts/build_and_run_fallback.sh
```

它使用 `scripts/local_fuzzer_main.cpp` 和 `-fsanitize=fuzzer-no-link,address` 编译同一个 `LLVMFuzzerTestOneInput` 入口，只用于验证 driver 可以被调用，不作为正式 fuzzing 实验方式。

`parse_int` 中故意包含崩溃条件：输入等于 `CRASH` 时调用 `abort()`。默认脚本关闭 CMP 辅助以便稳定跑满 10 秒；如果想观察 LibFuzzer 发现这个 crash，请使用 `FUZZ_USE_CMP=1`。
