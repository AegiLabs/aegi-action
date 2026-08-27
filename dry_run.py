"""Fabricate an audit result so the action can be exercised without spending quota.

The agent call is the only part of a run that costs money. Everything after it —
annotations, the job summary, the PR comment, the artifact, the fail-on gate — is
just code reading `<stem>.data.json`. This writes a believable one, so the whole
pipeline can be tested for free (and kept honest in CI on every push).

Findings are anchored to files that really exist in the target, picked at runtime,
so `file:line` annotations resolve in whatever repo this runs against — the part
most likely to break silently.

    python dry_run.py --target . --out-dir /tmp/aegi-report

Everything it writes is stamped DRY RUN. It never calls a model or a scanner.
"""
import argparse
import json
import pathlib
import random
import sys

# Extensions worth pretending to have audited, and directories never worth walking.
CODE = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb", ".php", ".java",
        ".cs", ".rs", ".sh", ".sql", ".yml", ".yaml", ".json", ".toml"}
SKIP = {".git", "node_modules", "dist", "build", "vendor", "__pycache__",
        ".venv", "venv", ".mypy_cache", ".pytest_cache"}

# One template per severity, so a dry run exercises every annotation level
# (error / warning / notice) and every fail-on threshold.
TEMPLATES = [
    ("critical", "Hardcoded admin credential",
     "A privileged credential is committed in source and readable by anyone with repo access.",
     "Rotate the credential, move it to a secret store, and purge it from git history."),
    ("high", "Query built by string concatenation",
     "User-controlled input reaches a query without parameterisation.",
     "Use parameterised queries or the ORM's binding API."),
    ("medium", "Missing authorization check on a write path",
     "A mutating handler does not verify the caller owns the record.",
     "Assert ownership server-side before the write."),
    ("low", "Verbose error responses",
     "Stack traces are returned to the client on failure.",
     "Log server-side; return an opaque error to the caller."),
    ("info", "Dependency drift",
     "Several dependencies are behind their latest patch release.",
     "Schedule a routine dependency bump."),
]


def pick_files(target, want):
    """Real files from the target, deepest-looking first so we cite source, not config."""
    found = []
    for p in sorted(target.rglob("*")):
        if any(part in SKIP for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in CODE:
            found.append(p)
    # Prefer files with some substance and a src-ish path over a top-level config.
    found.sort(key=lambda p: (0 if "src" in p.parts else 1, -min(p.stat().st_size, 8192)))
    return found[:want]


def line_in(path, rng):
    """A line number that actually exists in the file."""
    try:
        n = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
    except OSError:
        return 1
    return rng.randint(1, max(1, n))


def build(target, seed=7):
    """Assemble a report-data.json-shaped dict anchored to real files."""
    rng = random.Random(seed)          # deterministic: same repo -> same dry run
    files = pick_files(target, len(TEMPLATES))
    findings = []
    for i, (sev, title, summary, remediation) in enumerate(TEMPLATES, 1):
        f = {
            "id": f"F-{i:02d}",
            "title": f"[DRY RUN] {title}",
            "severity": sev,
            "summary": summary,
            "root_cause": "Fabricated by dry_run.py — no code was actually analysed.",
            "impact": "None. This finding is synthetic.",
            "remediation": remediation,
            "tags": ["dry-run"],
        }
        if i <= len(files):
            rel = files[i - 1].relative_to(target)
            f["asset"] = str(rel).replace("\\", "/")
            f["line"] = line_in(files[i - 1], rng)
        else:
            # No suitable file: exercise the file-less annotation path too.
            f["asset"] = "https://example.invalid/dry-run"
        findings.append(f)

    counts = {s: 0 for s, *_ in TEMPLATES}
    for f in findings:
        counts[f["severity"]] += 1

    return {
        "engagement": {"title": "Security assessment (DRY RUN)", "lang": "en",
                       "period": "dry-run", "scope": "Synthetic result — no audit was performed."},
        "summary": {"intro": "**This is a dry run.** No model was called and no code was "
                             "analysed. These findings are fabricated to exercise the "
                             "reporting pipeline end to end at zero cost.",
                    "counts": counts},
        "findings": findings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="fabricate an aegi result (no model calls)")
    ap.add_argument("--target", default=".", help="repo the fake findings point into")
    ap.add_argument("--out-dir", required=True, help="where to write the report files")
    ap.add_argument("--stem", default="aegi-report", help="filename stem, as aegi would use")
    a = ap.parse_args(argv)

    target = pathlib.Path(a.target).resolve()
    if not target.is_dir():
        sys.exit(f"--target must be a directory (got {target})")
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rd = build(target)
    (out / f"{a.stem}.data.json").write_text(
        json.dumps(rd, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{a.stem}.transcript.md").write_text(
        "# DRY RUN\n\nNo agent was invoked. `dry_run.py` fabricated "
        f"{len(rd['findings'])} synthetic findings against real files in "
        f"`{target.name}` so the reporting pipeline could be tested without "
        "spending token quota.\n", encoding="utf-8")
    # Stand in for the PDF so the artifact-upload step has something to collect.
    (out / f"{a.stem}.html").write_text(
        "<!doctype html><meta charset=utf-8><title>aegi dry run</title>"
        "<h1>Dry run</h1><p>No audit was performed. See the JSON for the "
        "synthetic findings.</p>", encoding="utf-8")

    print(f"dry run: wrote {len(rd['findings'])} synthetic findings to {out}")
    for f in rd["findings"]:
        print(f"  {f['severity']:8} {f['asset']}"
              + (f":{f['line']}" if f.get("line") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
