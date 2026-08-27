#!/usr/bin/env bash
# Install the audit's optional companions on a GitHub-hosted Linux runner:
# the deterministic scanners (which ground findings in real tool output) and
# typst (which compiles the branded PDF).
#
# Every install is best-effort. aegi degrades gracefully — no scanners means
# LLM-only findings, no typst means an HTML report — so a tool that fails to
# install must never fail the audit. We warn and carry on.
#
# Usage: install_tools.sh "<comma-separated scanners>" "<true|false: typst>"
# Releases are resolved with `gh` (preinstalled on runners), so there are no
# pinned versions or hand-built URLs to rot.
set -uo pipefail

WANT="${1:-}"
WANT_TYPST="${2:-true}"
BIN="${RUNNER_TEMP:-/tmp}/aegi-bin"
mkdir -p "$BIN"
echo "$BIN" >> "${GITHUB_PATH:-/dev/null}"

if [ "${RUNNER_OS:-Linux}" != "Linux" ]; then
  echo "::warning::aegi-action installs scanners and typst on Linux runners only;" \
       "skipping on ${RUNNER_OS}. The audit still runs (LLM-only, HTML report)."
  exit 0
fi

warn() { echo "::warning::aegi-action: $*"; }

# Fetch one asset from a repo's latest release into $BIN, unpacking if needed.
# $1 repo  $2 asset glob  $3 binary name inside the archive
grab() {
  local repo="$1" pattern="$2" name="$3" tmp
  tmp="$(mktemp -d)"
  if ! gh release download --repo "$repo" --pattern "$pattern" --dir "$tmp" --clobber 2>&1; then
    warn "could not download $name from $repo ($pattern) - skipping"
    rm -rf "$tmp"; return 1
  fi
  local file
  file="$(find "$tmp" -maxdepth 1 -type f | head -n1)"
  case "$file" in
    *.tar.gz|*.tgz) tar -xzf "$file" -C "$tmp" ;;
    *.tar.xz)       tar -xJf "$file" -C "$tmp" ;;
    *.zip)          unzip -qo "$file" -d "$tmp" ;;
    *)              chmod +x "$file"; mv "$file" "$BIN/$name"; rm -rf "$tmp"; return 0 ;;
  esac
  local found
  found="$(find "$tmp" -type f -name "$name" | head -n1)"
  if [ -z "$found" ]; then
    warn "$name not found inside the $repo archive - skipping"
    rm -rf "$tmp"; return 1
  fi
  chmod +x "$found"; mv "$found" "$BIN/$name"; rm -rf "$tmp"
}

has() { case ",$WANT," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

has gitleaks    && grab gitleaks/gitleaks          'gitleaks_*_linux_x64.tar.gz'   gitleaks
has osv-scanner && grab google/osv-scanner         'osv-scanner_linux_amd64'       osv-scanner
has trufflehog  && grab trufflesecurity/trufflehog 'trufflehog_*_linux_amd64.tar.gz' trufflehog

# semgrep ships on PyPI — simpler and more reliable than a release binary.
if has semgrep; then
  python -m pip install --quiet --disable-pip-version-check semgrep \
    || warn "semgrep install failed - skipping"
fi

if [ "$WANT_TYPST" = "true" ]; then
  grab typst/typst 'typst-x86_64-unknown-linux-musl.tar.xz' typst \
    || warn "typst unavailable - the report will fall back to HTML"
fi

echo "aegi-action: tools in $BIN:"
ls -1 "$BIN" 2>/dev/null | sed 's/^/  /' || echo "  (none)"
exit 0
