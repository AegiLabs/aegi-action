"""Turn an aegi run into GitHub Actions output.

Reads the `<stem>.data.json` an audit produced and emits, in one pass:

  * inline annotations (`::error/::warning/::notice`) anchored to file:line, so
    findings show up on the PR diff without needing any extra token or API call
  * a job summary table written to $GITHUB_STEP_SUMMARY
  * per-severity counts as step outputs on $GITHUB_OUTPUT
  * optionally a markdown body for a PR comment (--comment-file)

Exit code is the gate: 1 if any finding is at or above --fail-on, else 0.

Standalone stdlib — it runs on whatever Python the runner has.
"""
import argparse
import json
import os
import pathlib
import re
import sys

SEVERITIES = ["critical", "high", "medium", "low", "info"]   # worst first
RANK = {s: i for i, s in enumerate(SEVERITIES)}
# GitHub renders three annotation levels; map our five onto them.
LEVEL = {"critical": "error", "high": "error", "medium": "warning",
         "low": "warning", "info": "notice"}
# Markdown only — never printed to stdout, so a cp1252 console can't choke on it.
ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡",
        "low": "🟢", "info": "⚪"}


def esc_data(s):
    """Escape an annotation message (the part after `::`)."""
    return str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def esc_prop(s):
    """Escape an annotation property value (file=, title=, ...)."""
    return esc_data(s).replace(":", "%3A").replace(",", "%2C")


def sev_of(f):
    s = str(f.get("severity", "info")).lower()
    return s if s in RANK else "info"


def locate(finding, root, repo_root=None):
    """Resolve a finding to (repo-relative file or None, line or None).

    Two different roots, and conflating them is a real bug: `asset` paths are
    relative to the audit TARGET (the agent runs with cwd=target), but GitHub
    resolves annotation paths against the REPO ROOT. When the target is a
    subdirectory those differ, and using one for both makes every annotation
    file-less. `root` is the target; `repo_root` is what the result is expressed
    relative to, defaulting to `root` when they are the same.

    Only returns a file that actually exists inside repo_root: GitHub silently
    drops an annotation pointing at a path it cannot find, so we prefer a
    file-less annotation that is at least visible in the log. A model that wrote
    "src/db.js:12" into `asset` instead of using `line` is handled too.
    """
    repo_root = pathlib.Path(repo_root) if repo_root else pathlib.Path(root)
    asset = str(finding.get("asset") or "").strip()
    line = finding.get("line")
    if not asset or "://" in asset:
        return None, None

    m = re.search(r":(\d+)$", asset)          # asset carried the line inline
    if m:
        asset = asset[:m.start()]
        line = line or int(m.group(1))

    p = pathlib.Path(asset)
    p = p if p.is_absolute() else pathlib.Path(root) / p
    try:
        rel = p.resolve().relative_to(repo_root.resolve())
    except (ValueError, OSError):
        return None, None
    if not p.exists() or p.is_dir():
        return None, None

    try:
        line = int(line) if line is not None else None
    except (TypeError, ValueError):
        line = None
    return str(rel).replace("\\", "/"), (line if line and line >= 1 else None)


def annotate(findings, root, out=sys.stdout, repo_root=None):
    """Print one workflow-command annotation per finding, worst severity first."""
    for f in sorted(findings, key=lambda f: RANK[sev_of(f)]):
        sev = sev_of(f)
        file, line = locate(f, root, repo_root)
        props = [f"title={esc_prop('[' + sev.upper() + '] ' + str(f.get('title', 'Finding')))}"]
        if file:
            props.append(f"file={esc_prop(file)}")
            if line:
                props += [f"line={line}", "col=1"]
        body = " ".join(str(f.get(k, "")).strip() for k in ("summary", "remediation")).strip()
        print(f"::{LEVEL[sev]} {','.join(props)}::{esc_data(body or f.get('title', ''))}", file=out)


def counts_of(findings):
    c = {s: 0 for s in SEVERITIES}
    for f in findings:
        c[sev_of(f)] += 1
    return c


def summary_md(rd, findings, root, title, run_url=None, repo_root=None):
    """Job-summary / PR-comment markdown for one audit."""
    c = counts_of(findings)
    total = sum(c.values())
    lines = [f"## {title}", ""]

    if not total:
        lines += ["**No findings.** The audit completed and reported nothing.", ""]
    else:
        lines += ["| " + " | ".join(f"{ICON[s]} {s.title()}" for s in SEVERITIES) + " |",
                  "|" + "---|" * len(SEVERITIES),
                  "| " + " | ".join(str(c[s]) for s in SEVERITIES) + " |", ""]

    intro = rd.get("summary", {}).get("intro", "")
    if isinstance(intro, list):
        intro = " ".join(x for x in intro if isinstance(x, str))
    if intro:
        lines += [str(intro).strip(), ""]

    if total:
        lines += ["| Sev | ID | Finding | Location |", "|---|---|---|---|"]
        for f in sorted(findings, key=lambda f: RANK[sev_of(f)]):
            sev = sev_of(f)
            file, line = locate(f, root, repo_root)
            loc = f"`{file}:{line}`" if file and line else (
                f"`{file}`" if file else f"`{f.get('asset', '-')}`")
            title_cell = str(f.get("title", "Untitled")).replace("|", "\\|")
            lines.append(f"| {ICON[sev]} {sev.title()} | {f.get('id', '')} | {title_cell} | {loc} |")
        lines += ["", "<sub>Full detail, evidence and remediation are in the PDF report "
                  "attached to this run's artifacts.</sub>"]
    if run_url:
        lines += ["", f"<sub>[Audit run]({run_url}) - AegiLabs</sub>"]
    return "\n".join(lines) + "\n"


def emit_outputs(counts, total, worst):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for s in SEVERITIES:
            fh.write(f"{s}={counts[s]}\n")
        fh.write(f"total={total}\nworst={worst}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="aegi findings -> GitHub Actions output")
    ap.add_argument("data", help="path to the audit's .data.json")
    ap.add_argument("--root", default=os.environ.get("GITHUB_WORKSPACE", "."),
                    help="the audited target — what findings' asset paths are relative to")
    ap.add_argument("--repo-root", default=None,
                    help="repo root that annotation paths must be relative to "
                         "(defaults to --root; differs when the target is a subdirectory)")
    ap.add_argument("--fail-on", default="critical", choices=SEVERITIES + ["none"],
                    help="exit 1 if a finding at this severity or worse exists (default critical)")
    ap.add_argument("--title", default="AegiLabs security audit")
    ap.add_argument("--comment-file", help="also write the markdown to this path")
    ap.add_argument("--run-url", help="link back to the workflow run")
    a = ap.parse_args(argv)

    data_path = pathlib.Path(a.data)
    if not data_path.exists():
        # No structured block means the agent produced only a transcript. Say so
        # loudly, but don't fail the build on it — that's a tooling miss, not a
        # security verdict.
        print(f"::warning title={esc_prop(a.title)}::"
              "the audit produced no structured findings (see the transcript artifact)")
        return 0

    rd = json.loads(data_path.read_text(encoding="utf-8"))
    findings = [f for f in rd.get("findings", []) if isinstance(f, dict)]
    root = pathlib.Path(a.root)
    repo_root = pathlib.Path(a.repo_root) if a.repo_root else root

    annotate(findings, root, repo_root=repo_root)

    counts = counts_of(findings)
    total = sum(counts.values())
    worst = next((s for s in SEVERITIES if counts[s]), "none")
    md = summary_md(rd, findings, root, a.title, a.run_url, repo_root=repo_root)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)
    if a.comment_file:
        pathlib.Path(a.comment_file).write_text(md, encoding="utf-8")
    emit_outputs(counts, total, worst)

    print("\naegi: %d finding(s) - %s" % (
        total, ", ".join(f"{counts[s]} {s}" for s in SEVERITIES if counts[s]) or "none"),
        file=sys.stderr)

    if a.fail_on != "none" and worst != "none" and RANK[worst] <= RANK[a.fail_on]:
        print(f"::error::audit gate failed - found {worst} (fail-on: {a.fail_on})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
