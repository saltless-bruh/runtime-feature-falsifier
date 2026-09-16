#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
command -v uv >/dev/null 2>&1 || { echo 'SKIP: uv not available for interactive wizard test'; exit 0; }
command -v script >/dev/null 2>&1 || { echo 'SKIP: script(1) not available for interactive wizard test'; exit 0; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PROJECT="$TMP/project"
mkdir -p "$PROJECT" "$TMP/bin"
export UV_TOOL_DIR="$TMP/uv-tools"
export UV_TOOL_BIN_DIR="$TMP/bin"

# Running from the extracted release directory intentionally has no project
# default. Choose the temporary project, select Codex manually, accept plan.
printf '%s\n2\n\n' "$PROJECT" | script -qec "$ROOT/install.sh" /dev/null > "$TMP/out.txt" 2>&1

grep -q "Runtime Feature Falsifier v$VERSION" "$TMP/out.txt"
grep -q 'Target project' "$TMP/out.txt"
grep -q 'Detected agents' "$TMP/out.txt"
grep -q 'Install for' "$TMP/out.txt"
grep -q 'All detected' "$TMP/out.txt"
grep -q 'Codex CLI' "$TMP/out.txt"
grep -q 'Installation complete' "$TMP/out.txt"
[[ -f "$PROJECT/.agents/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ "$(cat "$PROJECT/.agents/skills/runtime-feature-falsifier/VERSION")" == "$VERSION" ]]

echo 'PASS: ./install.sh launches interactive project/client detection wizard and installs selected integration'
