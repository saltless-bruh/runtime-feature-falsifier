#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS="$ROOT/tests"

printf '[1/6] Validating v2.9.5 distribution, CLI package, and cross-harness skill...\n'
python3 "$TESTS/tools/validate_bundle.py"

printf '[2/6] Syntax-checking runtime, CLI, tests, Claude, Google, and OpenCode companions without generating bytecode...\n'
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
files = [root / 'installer.py', root / 'setup.py']
files += sorted((root / 'src/runtime_feature_falsifier_cli').rglob('*.py'))
files += sorted((root / 'runtime-feature-falsifier').rglob('*.py'))
files += sorted((root / 'tests').rglob('*.py'))
files += sorted((root / 'claude-code').rglob('*.py'))
files += sorted((root / 'google-antigravity').rglob('*.py'))
files += sorted((root / 'opencode').rglob('*.py'))
for path in dict.fromkeys(files):
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
print(f'PASS: syntax-checked {len(list(dict.fromkeys(files)))} Python files')
PY

printf '[3/6] Running anti-pattern teaching/regression corpus...\n'
(
  cd "$TESTS/teaching-corpus"
  PYTHONDONTWRITEBYTECODE=1 python3 run_all_examples.py
)

printf '[4/6] Verifying source-mode Codex/Claude/Antigravity/Gemini/OpenCode install and upgrade behavior...\n'
"$TESTS/install_layout_test.sh"

printf '[5/6] Verifying prebuilt uv-tool CLI package...\n'
"$TESTS/cli_package_test.sh"

printf '[6/6] Verifying no-argument interactive installer wizard...\n'
"$TESTS/wizard_test.sh"

printf 'Self-test PASS.\n'
