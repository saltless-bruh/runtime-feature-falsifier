#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
PACKAGE="runtime-feature-falsifier-cli"
WHEEL="$ROOT/dist/runtime_feature_falsifier_cli-${VERSION}-py3-none-any.whl"

if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'ERR'
ERROR: uv is required for the Runtime Feature Falsifier installer.
Install uv first: https://docs.astral.sh/uv/getting-started/installation/

Fallback without installing the persistent CLI:
  python3 installer.py init
ERR
  exit 2
fi

printf '\nPreparing Runtime Feature Falsifier v%s... ' "$VERSION"
if [[ -f "$WHEEL" ]]; then
  uv -q tool install "$WHEEL" --force
else
  uv -q tool install "$PACKAGE" --force --from "$ROOT"
fi
printf 'done.\n\n'

BIN_DIR="$(uv tool dir --bin)"
RFF="$BIN_DIR/rff"
if [[ ! -x "$RFF" ]]; then
  RFF="$(command -v rff || true)"
fi
if [[ -z "$RFF" || ! -x "$RFF" ]]; then
  echo "ERROR: uv installed the package but the rff executable was not found." >&2
  echo "Try: uv tool update-shell" >&2
  exit 2
fi

# Default: launch the interactive setup wizard. This detects installed agents,
# lets the user choose one or more integrations, previews the update/install
# plan, and performs the installation.
if [[ $# -eq 0 ]]; then
  exec "$RFF" init
fi

# Explicit commands remain available for automation and CI.
case "$1" in
  init|detect|doctor|integration|self|-h|--help|--version)
    exec "$RFF" "$@"
    ;;
  *)
    # Backward compatibility with v2.x top-level flags such as:
    #   ./install.sh --codex --project /path --force
    exec "$RFF" init "$@"
    ;;
esac
