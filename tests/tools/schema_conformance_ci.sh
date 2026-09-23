#!/usr/bin/env bash
# Development/CI-only JSON Schema conformance lane.
#
# Runtime and self-test intentionally do NOT depend on jsonschema or Bowtie.
# This lane adds a second implementation oracle and, when Bowtie is installed,
# runs the official Draft 7 suite against a supported fastjsonschema harness if
# one is published, plus python-jsonschema as an ecosystem baseline.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tests/tools/schema_oracle_ci.py"

if ! command -v bowtie >/dev/null 2>&1; then
  printf '%s\n' 'SKIP: Bowtie is not installed; reference-validator differential checks passed.'
  printf '%s\n' 'CI may install Bowtie and rerun this script to exercise the official Draft 7 suite.'
  exit 0
fi

printf '%s\n' 'Running official JSON Schema Draft 7 conformance through Bowtie...'

# Prefer a published fastjsonschema Bowtie harness when the ecosystem provides
# one. Do not hard-code an implementation ID which may change over time.
FAST_IMPL="$(bowtie filter-implementations --supports-dialect draft7 2>/dev/null | grep -i 'fastjsonschema' | head -n 1 || true)"
if [[ -n "$FAST_IMPL" ]]; then
  printf 'Bowtie implementation: %s\n' "$FAST_IMPL"
  bowtie suite -i "$FAST_IMPL" draft7 | bowtie summary --show failures
else
  printf '%s\n' 'INFO: Bowtie currently exposes no fastjsonschema harness; skipping direct official-suite execution for the vendored engine.'
fi

# Keep an independent ecosystem baseline in the same CI lane. The focused
# schema_oracle_ci.py test above is what directly compares this reference engine
# with the exact vendored fastjsonschema code used by RFF.
if bowtie filter-implementations --supports-dialect draft7 2>/dev/null | grep -qx 'python-jsonschema'; then
  bowtie suite -i python-jsonschema draft7 | bowtie summary --show failures
else
  printf '%s\n' 'INFO: Bowtie python-jsonschema harness unavailable; focused differential oracle already ran.'
fi
