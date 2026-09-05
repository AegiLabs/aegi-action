# aegi-action — the audit as a CI gate

Runs the AegiLabs audit agent over a repository inside **the customer's own
runner** — the checkout, the scanners and the agent all execute there, and we
host no compute for it. Same trust boundary as the desktop `aegi` CLI: the repo
is never cloned or uploaded to us, but the parts of it the agent reads are sent
as model context through the metering proxy to Anthropic. See "What leaves your
machine" in `product/aegi/README.md` — it applies identically here, with the
runner in place of a laptop.

Findings come back three ways: inline annotations on the pull request diff, a
job summary table, and the branded PDF report as a run artifact. With
`fix: true` they come back a fourth way — as a pull request that fixes them.

```yaml
# .github/workflows/security.yml
name: security
on: [pull_request]

jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write        # only needed for comment-pr
    steps:
      - uses: actions/checkout@v7
      - uses: AegiLabs/aegi-action@v1
        with:
          key: ${{ secrets.AEGI_KEY }}
          fail-on: high
          comment-pr: true
```

More variants in [`examples/`](examples/).

## Inputs

| Input | Default | What it does |
|---|---|---|
| `key` | — | The Aegi key. Put it in **Settings → Secrets → Actions** as `AEGI_KEY`; never inline it. Required unless `dry-run`. |
| `dry-run` | `false` | Fabricate findings instead of calling the agent. Costs nothing, exercises the whole reporting path. See below. |
| `target` | `.` | Path in the repo to audit, or a URL for a live assessment (a URL also needs `confirm-authorized: true`). |
| `lang` | `en` | Report language — `en` or `sv`. |
| `max-turns` | `30` | Agent turn cap. Higher digs deeper, costs more quota. Raise for large repos. |
| `fail-on` | `critical` | Fail the job at this severity or worse: `critical`/`high`/`medium`/`low`/`info`/`none`. |
| `scanners` | `gitleaks,osv-scanner,semgrep` | Scanners installed and run for grounding. `""` for none (findings become LLM-only). `trufflehog` also available. |
| `pdf` | `true` | Install typst for the branded PDF; otherwise an HTML report. |
| `upload-artifact` | `true` | Upload report + findings JSON + transcript. |
| `artifact-name` | `aegi-report` | Artifact name (change it if a matrix runs several audits). |
| `comment-pr` | `false` | Post one rolling summary comment on the PR. Needs `pull-requests: write`. |
| `eu` | `false` | **EU data residency.** Pins the model to an EU-hosted provider, proves the proxy answers from the EU before sending code, and de-fangs the scanners. See [EU data residency](#eu-data-residency) — the runner itself is the catch. |
| `fix` | `false` | After reporting, run a **second** agent pass that fixes the findings and open a PR with the patch. Spends a second run of quota. See [Fixing what it finds](#fixing-what-it-finds). |
| `fix-severity` | `high` | Only fix this severity or worse. |
| `fix-branch` | run-derived | Branch to push fixes to. Default `aegi/fix-<run_id>` — never collides, never reuses a branch someone has since edited. |
| `fix-base` | audited branch | Branch the fix PR targets. Default is the branch that was audited, so on a `pull_request` run the fixes stack onto the PR under review. |
| `fix-draft` | `true` | Open the fix PR as a draft. |
| `fix-max-turns` | `max-turns` | Turn cap for the fix pass alone. |
| `confirm-authorized` | `false` | Confirms authorization to actively test a URL target. Repo audits never need it. |
| `aegi-version` | latest | Pin the `aegilabs` npm release, e.g. `0.2.0`. Pin it for reproducible gates. |
| `node-version` | `24` | Node used to run the audit (needs 20+). |
| `python-version` | `3.11` | Python for this action's own helper scripts. The audit no longer needs it. |
| `proxy` | AegiLabs | Metering proxy override. Internal/staging only. |

## Outputs

`critical`, `high`, `medium`, `low`, `info`, `total`, `worst` (highest severity
present, or `none`), `report-dir`, plus `fix-pr` (the fix pull request's URL, or
empty) and `fixed` (how many findings it changed code for). Useful when you'd rather gate yourself
than use `fail-on`:

```yaml
      - uses: AegiLabs/aegi-action@v1
        id: audit
        with:
          key: ${{ secrets.AEGI_KEY }}
          fail-on: none
      - if: ${{ fromJSON(steps.audit.outputs.critical) > 0 }}
        run: echo "block the release"
```

## Testing it for free

The agent call is the only part of a run that costs money. `dry-run: true` skips
it — and skips installing the agent, scanners and typst with it — then fabricates
a findings file so everything downstream runs for real:

```yaml
      - uses: AegiLabs/aegi-action@v1
        with:
          dry-run: true          # no key needed, no quota spent
          fail-on: none
```

The synthetic findings are anchored to files that actually exist in the target,
picked at runtime, with line numbers inside each file's real length — so the
`file:line` annotations resolve exactly as they would on a real run. There is one
finding per severity, so all three annotation levels and every `fail-on`
threshold get exercised. Everything it writes is stamped `DRY RUN`, and it is
deterministic for a given repo.

Use it to verify a new workflow before spending anything. Our own CI runs the
real composite action this way on every push (`dry-run` job in
`.github/workflows/aegi-action.yml`), which is what keeps the wiring — step
order, `if:` guards, outputs, artifact, gate — under continuous test without a
token bill.

## Fixing what it finds

`fix: true` adds a second agent pass after the report. It reads the findings the
audit just produced, edits the checkout to fix them, commits to a new branch and
opens a pull request whose body says what changed and what it refused to touch.

```yaml
jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      contents: write           # push the fix branch
      pull-requests: write      # open the PR (and comment)
    steps:
      - uses: actions/checkout@v7
      - uses: AegiLabs/aegi-action@v1
        with:
          key: ${{ secrets.AEGI_KEY }}
          fail-on: high
          fix: true
          fix-severity: high    # don't open a PR over four `info` findings
```

Things worth knowing before you turn it on:

- **It is a second billed run.** Roughly doubles what an audited PR costs, which
  is why `fix-severity` defaults to `high` rather than `info`.
- **The audit agent still never writes.** The auditor is read-only by
  construction and stays that way; the fixer is a separate run that starts from
  findings the audit already committed to, so nothing that edited your code also
  decided what was wrong with it.
- **No agent ever runs a git command.** `aegi fix` only edits files. Branching,
  committing and pushing are plain shell in this action, and only paths inside
  `target` are staged.
- **Draft by default.** These are model-written patches. The PR body says so, in
  the body, every time — including that a leaked credential it removed from the
  source still has to be rotated.
- **The fix PR runs even when the gate failed.** That is the point: a failing
  `fail-on` is exactly when you want the patch. The job still fails.
- **You must let Actions open pull requests.** Enable **Settings → Actions →
  General → Workflow permissions → "Allow GitHub Actions to create and approve
  pull requests"**. It is off by default, and without it `gh pr create` fails
  with a bare `GitHub Actions is not permitted to create or approve pull
  requests`. The fixes are still pushed to the branch when this happens, so
  nothing is lost — the action says so and names the branch.
- **`GITHUB_TOKEN` pushes do not trigger workflows.** GitHub suppresses that on
  purpose, so your own CI won't run on the fix PR unless you push it with a PAT
  or a GitHub App token — pass one via `actions/checkout`'s `token:` input.
- **Skipped in `dry-run`,** which calls no model and so has nothing to fix.
- **Nothing to fix, no PR.** If the pass changes no file, the step says so and
  `fix-pr` comes back empty.

## EU data residency

`eu: true` passes `--eu` to both the audit and, if enabled, the fix pass. That
pins the model to an EU-hosted provider, verifies the proxy is answering from
inside the EU *before* any code is sent, and switches the scanners to flags that
keep code, secrets and your dependency graph off US services. It fails the run
rather than quietly downgrading.

```yaml
      - uses: AegiLabs/aegi-action@v1
        with:
          key: ${{ secrets.AEGI_KEY }}
          eu: true
```

**Read this before you rely on it.** `--eu` guarantees where the *model traffic*
goes. It cannot move the machine holding your checkout:

- **A GitHub-hosted runner is largely US compute.** Your source is cloned onto US
  infrastructure by the `checkout` step, before `aegi` runs at all. For an
  end-to-end EU claim you need a **self-hosted runner in the EU**. The action
  emits a warning when it sees `eu: true` on a GitHub-hosted runner, so this
  can't be true silently.
- **semgrep grounding is withheld** unless you supply a local ruleset —
  `--config auto` fetches rules from semgrep.dev and uploads scan metadata. Set
  `AEGI_SEMGREP_CONFIG` on the step to a ruleset path to keep it; otherwise
  semgrep is skipped, and the action warns rather than letting a missing scanner
  read as "clean".
- **The claim is "EU", never "Sweden".** Model traffic is processed in Germany.

## How it behaves

- **The report is written to `$RUNNER_TEMP`,** never the workspace — the audit
  is read-only and won't leave the repo dirty for later steps.
- **The agent's exit code is not the gate.** A truncated run (turn cap) or a
  partial failure can still have produced real findings, so the gate is
  `annotate.py` reading what was actually written. A run that produced no
  structured findings warns rather than fails — that's a tooling miss, not a
  security verdict.
- **One install.** The audit is `npm install -g aegilabs`; Python is used only
  for this action's own annotation and dry-run helpers.
- **Missing tools never fail the job.** Scanners and typst install best-effort
  and warn on failure; `aegi` degrades to LLM-only findings and an HTML report.
  Non-Linux runners skip the tool install entirely and still audit.
- **Annotations need a real path.** GitHub silently drops an annotation whose
  `file=` it can't resolve, so a finding whose `asset` doesn't exist in the
  workspace (or is a URL) is emitted without a file and shows up in the log and
  summary instead. This is why the findings schema carries `line`.
- **Quota:** every run debits your token quota, like any other audit. On a busy
  repo, prefer `pull_request` over `push`, and consider `paths:` filters.

> **Installs the `aegilabs` npm package** (the command it provides is `aegi`).
> The agent runtime ships inside it, so the action installs one thing and needs
> no separate agent CLI.

## Distribution

This repository is the action's only home — there is no copy inside the product
repo to keep in sync. An action in a private repo can only be used by repos in
the same organisation, so the public home is what makes the action reachable at
all; our own dogfood workflow consumes `@v1` from here like any customer would.

Nothing here is secret: the tradecraft prompt stays server-side in the proxy, and
the audit itself installs from npm (`npm install -g aegilabs`).

Releasing: land on `main`, then move the major tag.

```bash
git tag -f v1 && git push -f origin v1
```

## Tests

```bash
python tests/test_annotate.py
python tests/test_count_fixed.py
```

Covers the dry-run fixture feeding the real pipeline, annotation escaping,
`file:line` resolution (including paths that don't exist and traversal
attempts), the summary table, step outputs, and the `fail-on` gate. CI runs it on
every push, alongside a full dry-run of the composite action itself.

A real audit — live agent, real quota — is exercised by the **manual** `aegi
action e2e` workflow in the private product repo, which has the intentionally
vulnerable demo target and the `AEGI_KEY` secret, and which reaches this action
through `@v1`.

## License

[PolyForm Internal Use 1.0.0](LICENSE.md). Run it in your own CI, commercially,
and modify it for your own use; you may not redistribute it or offer it to third
parties as a service.
