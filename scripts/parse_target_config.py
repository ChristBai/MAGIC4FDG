#!/usr/bin/env python3
"""Emit shell variable assignments from a target config JSON file.

Usage from bash:
  eval "$(python3 scripts/parse_target_config.py "$TARGET_CONFIG_PATH")"

Outputs:
  TARGET_NAME='...'
  SEED_CORPUS='...'
  SOURCE_FILES=('...' '...')
  INCLUDE_ARGS=('-I...' '-I...')
"""

import json
import shlex
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_target_config.py <config.json>", file=sys.stderr)
        return 1

    target = json.load(open(sys.argv[1], encoding="utf-8"))
    quote = shlex.quote

    print(f"TARGET_NAME={quote(target['target_name'])}")
    print(f"SEED_CORPUS={quote(target['seed_corpus'])}")
    print("SOURCE_FILES=(" + " ".join(quote(path) for path in target["source_files"]) + ")")
    print("INCLUDE_ARGS=(" + " ".join(quote("-I" + path) for path in target["include_dirs"]) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
