# fuzz-driver-gen-mvp

一个最小可行原型，用于学习“基于 LLM 的 C/C++ LibFuzzer fuzz driver 生成”。

当前版本可以先把固定 prompt 模板渲染出来，也可以调用真实 OpenAI Responses API 生成 `generated/fuzz_driver.cpp`。仓库里仍保留了一个手写、可运行的 LibFuzzer driver，方便没有 API key 时继续学习构建和运行流程。

本项目只用于本地、防御性的软件测试教学示例：为开源 C/C++ 库 API 生成 LibFuzzer driver，不包含攻击真实系统、漏洞利用、未授权目标、恶意软件、凭据窃取或绕过访问控制的逻辑。

## 项目结构

```text
examples/tiny_lib/
  tiny.h
  tiny.cpp
  seed_corpus/
examples/cjson_lib/
  cJSON.h
  cJSON.c
  json.dict
  seed_corpus/seed.json
targets/
  tiny_parse_int.json
  cjson_parse.json
prompts/libfuzzer_driver_prompt.txt
src/analyze_target.py
src/generate_driver.py
generated/fuzz_driver.cpp
scripts/docker_build.sh
scripts/docker_run_fuzz.sh
scripts/docker_run_coverage.sh
scripts/build_and_run.sh
scripts/clean_runs.sh
```

## Automatic target analysis

You can generate target config JSON files from a C/C++ header without hand-writing each target. The analyzer is intentionally conservative for the MVP: it strips comments/preprocessor lines, parses simple C-style public function declarations, ranks likely fuzz entry points, and writes one config per candidate plus an index sorted by score.

Generate cJSON target configs:

```bash
python3 src/analyze_target.py \
  --library-name cjson \
  --header examples/cjson_lib/cJSON.h \
  --source examples/cjson_lib/cJSON.c \
  --include-dir examples/cjson_lib \
  --seed-corpus examples/cjson_lib/seed_corpus \
  --out-dir targets/generated
```

The generated configs are written to `targets/generated/`, with `targets/generated/cjson_index.json` listing all candidates. To use the top generated target, pick the first entry from the index, then pass that config to the existing pipeline:

```bash
python3 src/generate_driver.py \
  --target-config targets/generated/cjson_cJSON_ParseWithLength.json \
  --mode llm

./scripts/docker_build.sh
TARGET_CONFIG=targets/generated/cjson_cJSON_ParseWithLength.json ./scripts/docker_run_fuzz.sh
```

The manually written `targets/cjson_parse.json` remains as a small baseline example.

## Coverage report

After a driver has been generated and can compile, collect LLVM source-based coverage for a short fuzz run:

```bash
./scripts/docker_build.sh
TARGET_CONFIG=targets/generated/cjson_cJSON_ParseWithLength.json \
FUZZ_SECONDS=10 \
FUZZ_USE_CMP=1 \
FUZZ_DICT=examples/cjson_lib/json.dict \
./scripts/docker_run_coverage.sh
```

The coverage run writes an isolated report directory:

```text
generated/coverage/<target-name>-<timestamp>/
  coverage_report.md
  coverage_report.json
  coverage_export.json
  coverage.log
  html/
  corpus/
  artifacts/
```

Use `coverage_report.md` for a quick human-readable summary, `coverage_report.json` for tool integration, and `html/index.html` for line-by-line inspection. The report records build status, fuzz exit code, total line/function/region/branch coverage, and per-file coverage.

The JSON and Markdown reports also include fuzzing signals that help evaluate target quality:

```text
executions
final corpus files
final corpus bytes
artifact files
recommended dictionary entries
```

Compare multiple coverage runs:

```bash
python3 src/compare_coverage_reports.py generated/coverage/*/coverage_report.json
```

## 生成 prompt

为第一个真实外部 C 库目标 cJSON 生成 prompt：

```bash
python3 src/generate_driver.py \
  --target-config targets/cjson_parse.json \
  --mode prompt
```

生成结果会保存到：

```text
generated/prompt.txt
```

默认是 dry-run，不会调用真实 LLM。

教学用 `tiny_lib` 目标仍然保留：

```bash
python3 src/generate_driver.py \
  --target-config targets/tiny_parse_int.json \
  --mode prompt
```

## 调用真实 LLM 生成 driver

设置 OpenAI API key 后，使用 `--mode llm` 为 cJSON 生成 driver：

```bash
export OPENAI_API_KEY="sk-..."

python3 src/generate_driver.py \
  --target-config targets/cjson_parse.json \
  --mode llm
```

脚本会：

1. 将完整 prompt 写入 `generated/prompt.txt`
2. 调用 OpenAI Responses API
3. 将原始 API 响应写入 `generated/llm_response.json`
4. 将生成的 C++ driver 写入 `generated/fuzz_driver.cpp`

默认模型来自 `OPENAI_MODEL`，如果没有设置则使用 `gpt-5.4`：

```bash
OPENAI_MODEL="gpt-5.4" python3 src/generate_driver.py --target-config targets/cjson_parse.json --mode llm
```

也可以显式指定：

```bash
python3 src/generate_driver.py --target-config targets/cjson_parse.json --mode llm --model gpt-5.4
```

生成后运行 cJSON 的 LibFuzzer validation：

```bash
./scripts/docker_build.sh
TARGET_CONFIG=targets/cjson_parse.json ./scripts/docker_run_fuzz.sh
```

## 推荐方式：Docker 运行真正 LibFuzzer

推荐使用 Docker，这样不依赖本机 `clang++` 是否自带 LibFuzzer runtime。

```bash
chmod +x scripts/*.sh
./scripts/docker_build.sh
TARGET_CONFIG=targets/cjson_parse.json ./scripts/docker_run_fuzz.sh
```

Docker 镜像基于 Ubuntu，安装 `clang`、`llvm`、`lld`、`cmake`、`ninja-build`、`build-essential`、`python3`、`python3-pip` 和 `git`。运行时会把当前项目目录挂载到容器的 `/workspace`，然后执行 `scripts/build_and_run.sh`。

## 运行结果目录

目标配置中的 `seed_corpus` 是只读初始语料目录。正式 fuzz 脚本不会把它直接传给 LibFuzzer，因为 LibFuzzer 会把新发现的输入写回第一个 corpus 目录。

每次运行都会创建独立目录：

```text
generated/runs/<timestamp>/
  corpus/
  artifacts/
  fuzz_driver
  fuzz.log
```

脚本会把目标配置中的 seed corpus 复制到本次 `corpus/`，然后只让 LibFuzzer 写入这个副本。`crash-*`、`timeout-*` 等 artifact 会写入本次 `artifacts/`。完整运行日志会保存到本次 `fuzz.log`。

## 本地编译并 fuzz

需要本机安装支持真正 LibFuzzer runtime 的 `clang++`。

```bash
chmod +x scripts/build_and_run.sh
./scripts/build_and_run.sh
```

脚本会：

1. 编译目标配置中的 source files 和 `generated/fuzz_driver.cpp`
2. 使用 `-fsanitize=fuzzer,address`
3. 复制目标配置中的 seed corpus 到本次 run 的 `corpus/`
4. 使用 `-max_total_time=10 -artifact_prefix=<run>/artifacts/` 运行 10 秒
5. 每次运行前后统计 seed corpus 文件列表 hash，确认初始语料目录没有变化

本地脚本不会自动 fallback。如果编译失败，请优先使用 Docker 方式。

要本地验证 cJSON，请先生成 cJSON driver，然后运行：

```bash
TARGET_CONFIG=targets/cjson_parse.json ./scripts/build_and_run.sh
```

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
