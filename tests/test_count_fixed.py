"""python tests/test_count_fixed.py — the `fixed` step output.

The script runs inside a `set -euo pipefail` step, so anything that raises would
kill the PR step *after* the pull request had already been opened — losing the
link to it. Every bad input must therefore print 0 and exit 0.
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "count_fixed.py")


def run(*args):
    p = subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True
    )
    return p.returncode, p.stdout.strip()


def write(text):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_counts_the_fixed_array():
    path = write(json.dumps({"fixed": [{"id": "F-01"}, {"id": "F-02"}],
                             "skipped": [{"id": "F-03"}]}))
    assert run(path) == (0, "2")


def test_every_bad_input_is_zero_not_a_crash():
    # An agent that emitted no JSON block at all, a truncated write, a null
    # field, a file that isn't there, and no argument whatsoever.
    assert run(write("{}")) == (0, "0")
    assert run(write('{"fixed": null}')) == (0, "0")
    assert run(write("{not json")) == (0, "0")
    assert run(write("[]")) == (0, "0")
    assert run(os.path.join(tempfile.gettempdir(), "no-such-aegi-fix.json")) == (0, "0")
    assert run() == (0, "0")


if __name__ == "__main__":
    test_counts_the_fixed_array()
    test_every_bad_input_is_zero_not_a_crash()
    print("ok - count_fixed never fails the step that already opened the PR")
