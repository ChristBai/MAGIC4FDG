#!/usr/bin/env bash
# Shared compilation logic for fuzz targets.
# Source this file and call compile_target_objects and link_fuzz_target.
#
# Required variables before sourcing:
#   ROOT_DIR, TARGET_CONFIG_PATH (or already eval'd parse_target_config.py output)
#   CC, CXX

compile_target_objects() {
  local out_dir="$1"
  shift
  local -a include_args=("${INCLUDE_ARGS[@]}")
  local -a extra_flags=("$@")

  object_files=()
  for source_file in "${SOURCE_FILES[@]}"; do
    local base
    base="$(echo "${source_file}" | tr '/' '_')"
    local object_file="${out_dir}/${base}.o"
    object_files+=("${object_file}")

    case "${source_file}" in
      *.c)
        "${CC}" -g -O1 "${extra_flags[@]}" "${include_args[@]}" -c "${source_file}" -o "${object_file}"
        ;;
      *)
        "${CXX}" -std=c++17 -g -O1 "${extra_flags[@]}" "${include_args[@]}" -c "${source_file}" -o "${object_file}"
        ;;
    esac
  done
}
