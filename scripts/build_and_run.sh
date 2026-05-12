#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CC="${CC:-clang}"
CXX="${CXX:-clang++}"
FUZZ_USE_CMP="${FUZZ_USE_CMP:-0}"
TARGET_CONFIG="${TARGET_CONFIG:-targets/cjson_parse.json}"

RUNS_DIR="${ROOT_DIR}/generated/runs"
TARGET_CONFIG_PATH="${ROOT_DIR}/${TARGET_CONFIG}"

if [[ ! -f "${TARGET_CONFIG_PATH}" ]]; then
  echo "error: target config not found: ${TARGET_CONFIG}" >&2
  exit 1
fi

eval "$(python3 "${ROOT_DIR}/scripts/parse_target_config.py" "${TARGET_CONFIG_PATH}")"

SEED_CORPUS="${ROOT_DIR}/${SEED_CORPUS}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_REL="generated/runs/${TARGET_NAME}-${timestamp}"
RUN_DIR="${ROOT_DIR}/${RUN_REL}"
while [[ -e "${RUN_DIR}" ]]; do
  sleep 1
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  RUN_REL="generated/runs/${TARGET_NAME}-${timestamp}"
  RUN_DIR="${ROOT_DIR}/${RUN_REL}"
done

RUN_CORPUS="${RUN_DIR}/corpus"
ARTIFACT_DIR="${RUN_DIR}/artifacts"
RUN_CORPUS_REL="${RUN_REL}/corpus"
ARTIFACT_DIR_REL="${RUN_REL}/artifacts"
FUZZ_TARGET_REL="${RUN_REL}/fuzz_driver"
LOG_FILE="${RUN_DIR}/fuzz.log"

hash_command() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256
  else
    cksum
  fi
}

seed_file_list_hash() {
  (
    cd "${SEED_CORPUS}"
    find . -type f -print | LC_ALL=C sort
  ) | hash_command | awk '{print $1}'
}

if ! command -v "${CXX}" >/dev/null 2>&1; then
  echo "error: clang++ not found. Set CXX=/path/to/clang++ or install clang." >&2
  exit 1
fi

if ! command -v "${CC}" >/dev/null 2>&1; then
  echo "error: clang not found. Set CC=/path/to/clang or install clang." >&2
  exit 1
fi

if [[ "${FUZZ_USE_CMP}" != "0" && "${FUZZ_USE_CMP}" != "1" ]]; then
  echo "error: FUZZ_USE_CMP must be 0 or 1." >&2
  exit 1
fi

mkdir -p "${RUN_CORPUS}" "${ARTIFACT_DIR}"
cp -R "${SEED_CORPUS}/." "${RUN_CORPUS}/"

exec > >(tee "${LOG_FILE}") 2>&1

echo "run directory: ${RUN_DIR}"
echo "corpus directory: ${RUN_CORPUS}"
echo "artifact directory: ${ARTIFACT_DIR}"
echo "log file: ${LOG_FILE}"
echo "target config: ${TARGET_CONFIG}"
echo "target name: ${TARGET_NAME}"
echo "FUZZ_USE_CMP=${FUZZ_USE_CMP}"

seed_hash_before="$(seed_file_list_hash)"
echo "seed corpus file-list hash before: ${seed_hash_before}"

"${CXX}" --version
"${CC}" --version
cd "${ROOT_DIR}"

object_files=()
for source_file in "${SOURCE_FILES[@]}"; do
  object_file="${RUN_DIR}/$(basename "${source_file}").o"
  object_files+=("${object_file}")

  case "${source_file}" in
    *.c)
      "${CC}" -g -O1 -fsanitize=address "${INCLUDE_ARGS[@]}" -c "${source_file}" -o "${object_file}"
      ;;
    *)
      "${CXX}" -std=c++17 -g -O1 -fsanitize=address "${INCLUDE_ARGS[@]}" -c "${source_file}" -o "${object_file}"
      ;;
  esac
done

if ! "${CXX}" \
  -std=c++17 \
  -g \
  -O1 \
  -fsanitize=fuzzer,address \
  "${INCLUDE_ARGS[@]}" \
  "${object_files[@]}" \
  generated/fuzz_driver.cpp \
  -o "${FUZZ_TARGET_REL}"; then
  echo "error: failed to compile with the real LibFuzzer runtime." >&2
  echo "hint: run ./scripts/docker_build.sh && ./scripts/docker_run_fuzz.sh for a known-good Linux LibFuzzer environment." >&2
  echo "hint: for a local smoke test only, run ./scripts/build_and_run_fallback.sh." >&2
  exit 1
fi

fuzz_args=(
  "${RUN_CORPUS_REL}"
  "-max_total_time=10"
  "-artifact_prefix=${ARTIFACT_DIR_REL}/"
)

if [[ "${FUZZ_USE_CMP}" == "0" ]]; then
  fuzz_args+=("-use_cmp=0")
fi

set +e
"${FUZZ_TARGET_REL}" "${fuzz_args[@]}"
fuzz_exit_code=$?
set -e

seed_hash_after="$(seed_file_list_hash)"
echo "seed corpus file-list hash after: ${seed_hash_after}"

if [[ "${seed_hash_before}" == "${seed_hash_after}" ]]; then
  echo "seed corpus unchanged: yes"
else
  echo "seed corpus unchanged: no" >&2
fi

echo "fuzz exit code: ${fuzz_exit_code}"
echo "run directory: ${RUN_DIR}"
echo "artifact directory: ${ARTIFACT_DIR}"
echo "log file: ${LOG_FILE}"

if [[ "${seed_hash_before}" != "${seed_hash_after}" ]]; then
  exit 2
fi

exit "${fuzz_exit_code}"
