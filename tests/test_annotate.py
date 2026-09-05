"""python tests/test_annotate.py — the data.json -> GitHub Actions bridge.

Covers what breaks silently in CI: annotation escaping, file:line resolution
(including a path that doesn't exist, which GitHub would drop), the summary
table, step outputs, and the fail-on gate.
"""
import io
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import annotate as A

RD = {
    "engagement": {"title": "Security assessment", "lang": "en"},
    "summary": {"intro": "One critical injection, one hardcoded secret.",
                "counts": {"critical": 1, "high": 0, "medium": 1, "low": 0, "info": 0}},
    "findings": [
        {"id": "F-01", "title": "Hardcoded service key", "severity": "MEDIUM",
         "asset": "src/config.js", "line": 3, "summary": "Admin key in source.",
         "remediation": "Move it to an env var."},
        {"id": "F-02", "title": "SQL injection, 100% exploitable", "severity": "critical",
         "asset": "src/db.js", "line": 12, "summary": "Unparameterised query.",
         "remediation": "Parameterise it."},
        {"id": "F-03", "title": "Missing security headers", "severity": "low",
         "asset": "https://example.test/", "summary": "No CSP."},
        {"id": "F-04", "title": "Stale dep", "severity": "info",
         "asset": "does/not/exist.js", "line": 9, "summary": "Old library."},
    ],
}


def _repo():
    """A workspace where two of the four finding paths actually exist."""
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    (root / "src" / "config.js").write_text("a\nb\nconst KEY='x'\n", encoding="utf-8")
    (root / "src" / "db.js").write_text("\n" * 20, encoding="utf-8")
    return root


def test_locate():
    root = _repo()
    assert A.locate(RD["findings"][0], root) == ("src/config.js", 3)
    assert A.locate(RD["findings"][2], root) == (None, None)     # a URL is not a file
    assert A.locate(RD["findings"][3], root) == (None, None)     # path doesn't exist
    # line carried inline in `asset` (a model ignoring the schema) is recovered
    assert A.locate({"asset": "src/db.js:7"}, root) == ("src/db.js", 7)
    # a bad line degrades to a file-only annotation rather than breaking
    assert A.locate({"asset": "src/db.js", "line": "nope"}, root) == ("src/db.js", None)
    # nothing escapes the workspace
    assert A.locate({"asset": "../../etc/passwd"}, root) == (None, None)


def test_annotations():
    root = _repo()
    buf = io.StringIO()
    A.annotate(RD["findings"], root, out=buf)
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 4
    # worst first, and severity maps onto GitHub's three levels
    assert lines[0].startswith("::error ") and "src/db.js" in lines[0] and "line=12" in lines[0]
    assert lines[1].startswith("::warning ") and "file=src/config.js" in lines[1]
    assert lines[2].startswith("::warning ")            # low -> warning, no file=
    assert "file=" not in lines[2]
    assert lines[3].startswith("::notice ")             # info -> notice
    # the comma in the title would truncate the property list if unescaped
    assert "100%25 exploitable" in lines[0] and "%2C" in lines[0]


def test_summary_and_counts():
    root = _repo()
    md = A.summary_md(RD, RD["findings"], root, "Audit", run_url="http://run/1")
    assert "## Audit" in md
    assert "| 1 | 0 | 1 | 1 | 1 |" in md                # critical high medium low info
    assert "One critical injection" in md
    assert "`src/db.js:12`" in md and "`https://example.test/`" in md
    assert "http://run/1" in md
    assert A.counts_of(RD["findings"]) == {"critical": 1, "high": 0, "medium": 1,
                                           "low": 1, "info": 1}
    empty = A.summary_md({"summary": {}}, [], root, "Audit")
    assert "No findings" in empty


def _run(fail_on, findings, tmp):
    """Invoke main() with the GitHub env vars pointed at temp files."""
    data = tmp / "r.data.json"
    data.write_text(json.dumps({**RD, "findings": findings}), encoding="utf-8")
    out, summary = tmp / "out.txt", tmp / "sum.md"
    os.environ["GITHUB_OUTPUT"], os.environ["GITHUB_STEP_SUMMARY"] = str(out), str(summary)
    try:
        code = A.main([str(data), "--root", str(tmp), "--fail-on", fail_on])
    finally:
        del os.environ["GITHUB_OUTPUT"], os.environ["GITHUB_STEP_SUMMARY"]
    kv = dict(l.split("=", 1) for l in out.read_text().strip().splitlines() if "=" in l)
    return code, kv, summary.read_text(encoding="utf-8")


def test_gate():
    tmp = pathlib.Path(tempfile.mkdtemp())
    code, kv, summary = _run("critical", RD["findings"], tmp)
    assert code == 1, "a critical finding must fail a fail-on=critical gate"
    assert kv["critical"] == "1" and kv["total"] == "4" and kv["worst"] == "critical"
    assert "## AegiLabs security audit" in summary

    lows = [f for f in RD["findings"] if A.sev_of(f) in ("low", "info")]
    assert _run("critical", lows, tmp)[0] == 0, "low findings must not trip a critical gate"
    assert _run("low", lows, tmp)[0] == 1, "fail-on=low must catch a low finding"
    assert _run("none", RD["findings"], tmp)[0] == 0, "fail-on=none never fails"
    assert _run("critical", [], tmp)[0] == 0


def test_missing_data_is_not_a_failure():
    tmp = pathlib.Path(tempfile.mkdtemp())
    assert A.main([str(tmp / "nope.json"), "--fail-on", "critical"]) == 0


def test_subdir_target_resolves_against_the_repo_root():
    """Regression: `asset` paths are relative to the audit target, but GitHub
    resolves annotations against the repo root. Conflating them made every
    annotation file-less whenever `target` was a subdirectory — caught on our
    own first dogfood PR, where findings landed on the workflow file instead."""
    repo = pathlib.Path(tempfile.mkdtemp())
    target = repo / "product" / "examples" / "vuln-demo"
    (target / "src").mkdir(parents=True)
    (target / "src" / "db.js").write_text(chr(10) * 10, encoding="utf-8")
    f = {"asset": "src/db.js", "line": 6, "severity": "critical", "title": "x"}

    # the bug: one root for both jobs finds nothing
    assert A.locate(f, repo) == (None, None)
    # the fix: resolve against the target, express relative to the repo
    assert A.locate(f, target, repo) == ("product/examples/vuln-demo/src/db.js", 6)

    buf = io.StringIO()
    A.annotate([f], target, out=buf, repo_root=repo)
    line = buf.getvalue().strip()
    assert "file=product/examples/vuln-demo/src/db.js" in line and "line=6" in line
    md = A.summary_md({"summary": {}}, [f], target, "t", repo_root=repo)
    assert "`product/examples/vuln-demo/src/db.js:6`" in md
    # traversal is still bounded by the repo root, not the target
    assert A.locate({"asset": "../../../../../etc/passwd"}, target, repo) == (None, None)


def test_dry_run_fabricates_a_real_but_harmless_patch():
    """`--fix` in a dry run must produce an actual diff, and never corrupt a file.

    The point of it is that the branch/commit/push/PR steps then run for real, so
    "there is a diff" is the whole contract. JSON is the trap: it has no comment
    syntax, and the fabricated findings routinely anchor to package.json.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import dry_run

    src = pathlib.Path(tempfile.mkdtemp()) / "repo"
    (src / "src").mkdir(parents=True)
    (src / "src" / "app.js").write_text("const a = 1;\n", encoding="utf-8")
    (src / "package.json").write_text('{"name": "x"}\n', encoding="utf-8")
    before = (src / "package.json").read_text(encoding="utf-8")

    rd = dry_run.build(src)
    fixed, asset = dry_run.fabricate_fix(src, rd)

    assert fixed is not None and asset, "a dry-run fix must produce a change"
    assert not asset.endswith(".json"), "JSON has no comment syntax; must be skipped"
    assert (src / "package.json").read_text(encoding="utf-8") == before

    touched = (src / asset).read_text(encoding="utf-8")
    assert dry_run.FIX_MARKER in touched
    assert touched.endswith("\n")
    # Exactly one line added — a dry run must not grow the diff over time.
    assert touched.count(dry_run.FIX_MARKER) == 1

    body = dry_run.fix_summary_md(fixed, asset)
    assert "DRY RUN" in body and "Close this pull request" in body
    assert asset in body


def test_dry_run_feeds_the_pipeline():
    """The fabricated result must survive the real annotate/gate path — this is
    what makes the free CI self-test meaningful."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import dry_run

    root = _repo()
    rd = dry_run.build(root)
    findings = rd["findings"]
    assert len(findings) == len(dry_run.TEMPLATES)
    # every severity present, so all three annotation levels get exercised
    assert {A.sev_of(f) for f in findings} == set(A.SEVERITIES)

    anchored = [f for f in findings if A.locate(f, root)[0]]
    assert anchored, "dry run must anchor at least one finding to a real file"
    for f in anchored:
        file, line = A.locate(f, root)
        n = len((root / file).read_text(encoding="utf-8").splitlines())
        assert line is not None and 1 <= line <= max(1, n), f"{file}:{line} is out of range"

    buf = io.StringIO()
    A.annotate(findings, root, out=buf)
    assert len(buf.getvalue().strip().splitlines()) == len(findings)
    assert "DRY RUN" in A.summary_md(rd, findings, root, "Audit")

    # deterministic: same repo, same result, so CI diffs stay quiet
    assert dry_run.build(root) == rd


if __name__ == "__main__":
    test_locate()
    test_annotations()
    test_summary_and_counts()
    test_gate()
    test_missing_data_is_not_a_failure()
    test_subdir_target_resolves_against_the_repo_root()
    test_dry_run_feeds_the_pipeline()
    test_dry_run_fabricates_a_real_but_harmless_patch()
    print("ok - action bridge (locate, annotate, summary, gate) passes")
