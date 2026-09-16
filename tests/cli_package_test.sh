#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
WHEEL="$ROOT/dist/runtime_feature_falsifier_cli-${VERSION}-py3-none-any.whl"
[[ -f "$WHEEL" ]]
command -v uv >/dev/null 2>&1 || { echo 'SKIP: uv not available for CLI package test'; exit 0; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export UV_TOOL_DIR="$TMP/tools"
export UV_TOOL_BIN_DIR="$TMP/bin"
PROJECT="$TMP/project"
mkdir -p "$PROJECT"

uv tool install "$WHEEL" --force >/dev/null
RFF="$TMP/bin/rff"
[[ -x "$RFF" ]]
[[ "$($RFF --version)" == "rff $VERSION" ]]
$RFF self check | grep -q '"ok": true'
$RFF integration list --project "$PROJECT" | grep -q 'Codex CLI'
$RFF integration list --project "$PROJECT" | grep -q 'Google Antigravity 2.0'
$RFF integration list --project "$PROJECT" | grep -q 'Google Antigravity CLI'
$RFF integration list --project "$PROJECT" | grep -q 'OpenCode'
$RFF init "$PROJECT" --integration codex --non-interactive --yes >/dev/null
[[ -f "$PROJECT/.agents/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$PROJECT/.agents/skills/runtime-feature-falsifier/references/PERSISTENT-INVESTIGATION.md" ]]
[[ "$(cat "$PROJECT/.agents/skills/runtime-feature-falsifier/VERSION")" == "$VERSION" ]]
python3 "$PROJECT/.agents/skills/runtime-feature-falsifier/scripts/auditctl.py" --help | grep -q 'hypothesis-open'
python3 "$PROJECT/.agents/skills/runtime-feature-falsifier/scripts/auditctl.py" --help | grep -q 'investigation-status'

# Upgrade/re-init path must replace stale installed skill content.
touch "$PROJECT/.agents/skills/runtime-feature-falsifier/STALE-FILE"
$RFF init "$PROJECT" --integration codex --non-interactive --yes --force >/dev/null
[[ ! -e "$PROJECT/.agents/skills/runtime-feature-falsifier/STALE-FILE" ]]

# Antigravity 2.0 and CLI are separate selectors backed by the shared Google workspace payload.
$RFF init "$PROJECT" --integration antigravity2 --non-interactive --yes --force >/dev/null
[[ -f "$PROJECT/.agents/agents/runtime-feature-auditor/agent.md" ]]
$RFF init "$PROJECT" --integration antigravity-cli --non-interactive --yes --force >/dev/null
[[ -f "$PROJECT/.agents/skills/runtime-feature-falsifier/references/ANTIGRAVITY-CLI.md" ]]
# Legacy combined alias remains valid.
$RFF init "$PROJECT" --integration antigravity --non-interactive --yes --force >/dev/null
$RFF init "$PROJECT" --integration gemini --non-interactive --yes --force >/dev/null
[[ -f "$PROJECT/.agents/skills/runtime-feature-falsifier/references/GOOGLE-ANTIGRAVITY.md" ]]
$RFF init "$PROJECT" --integration opencode --non-interactive --yes --force >/dev/null
[[ -f "$PROJECT/.opencode/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$PROJECT/.opencode/skills/runtime-feature-falsifier/references/OPENCODE.md" ]]
[[ -f "$PROJECT/.opencode/agents/runtime-feature-auditor.md" ]]

echo 'PASS: prebuilt wheel installs persistent rff CLI; init/update works for Codex, Antigravity 2.0, Antigravity CLI, Gemini, and OpenCode'
