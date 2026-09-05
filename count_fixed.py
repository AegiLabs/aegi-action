#!/usr/bin/env python3
"""Print how many findings `aegi fix` reported fixing, from its .json summary.

A file, not a `python -c` one-liner in action.yml: the quoting there nests a
double-quoted string inside a double-quoted command substitution inside a YAML
block scalar, which is three chances to be wrong and no chance to be tested.

Any malformed or missing input counts as zero. This number is a step output for
a human to read, never a gate — failing the job over it would throw away a fix
PR that was already opened.
"""
import json
import sys


def main() -> int:
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            print(len(json.load(fh).get("fixed") or []))
    except Exception:
        print(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
