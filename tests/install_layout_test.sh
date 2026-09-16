#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PROJECT="$TMP/project"
GEMINI_PROJECT="$TMP/gemini-project"
OPENCODE_PROJECT="$TMP/opencode-project"
AGY2_PROJECT="$TMP/antigravity2-project"
AGYCLI_PROJECT="$TMP/antigravity-cli-project"
mkdir -p "$PROJECT" "$GEMINI_PROJECT" "$OPENCODE_PROJECT" "$AGY2_PROJECT" "$AGYCLI_PROJECT"

python3 "$ROOT/installer.py" --codex --project "$PROJECT" --no-self-test >/dev/null
SHARED="$PROJECT/.agents/skills/runtime-feature-falsifier"
[[ -f "$SHARED/SKILL.md" ]]
[[ -f "$SHARED/scripts/auditctl.py" ]]
[[ -f "$SHARED/agents/openai.yaml" ]]
[[ -f "$SHARED/references/GOOGLE-ANTIGRAVITY.md" ]]
[[ -f "$SHARED/references/PERSISTENT-INVESTIGATION.md" ]]
[[ -f "$SHARED/assets/hypothesis-event.schema.json" ]]
[[ ! -e "$SHARED/examples" ]]
[[ ! -e "$SHARED/evals" ]]

touch "$SHARED/STALE-FROM-OLD-VERSION"
mkdir -p "$SHARED/examples" "$SHARED/evals"
touch "$SHARED/examples/old-fixture.py" "$SHARED/evals/old-eval.json"
python3 "$ROOT/installer.py" --codex --project "$PROJECT" --force --no-self-test >/dev/null
[[ ! -e "$SHARED/STALE-FROM-OLD-VERSION" ]]
[[ ! -e "$SHARED/examples" ]]
[[ ! -e "$SHARED/evals" ]]

python3 "$ROOT/installer.py" --claude --project "$PROJECT" --no-self-test >/dev/null
[[ -f "$PROJECT/.claude/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$PROJECT/.claude/agents/runtime-feature-auditor.md" ]]
[[ -f "$PROJECT/.claude/runtime-feature-falsifier-hooks/claude_pretool_guard.py" ]]
[[ -f "$PROJECT/.claude/runtime-feature-falsifier-hooks/claude_stop_gate.py" ]]

python3 "$ROOT/installer.py" --antigravity --project "$PROJECT" --force --no-self-test >/dev/null
[[ -f "$PROJECT/.agents/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$PROJECT/.agents/agents/runtime-feature-auditor/agent.md" ]]
[[ ! -e "$PROJECT/.agents/runtime-feature-falsifier-hooks" ]]
[[ ! -e "$PROJECT/.agents/hooks.json" ]]
grep -q 'skills/runtime-feature-falsifier' "$PROJECT/.agents/agents/runtime-feature-auditor/agent.md"
grep -q 'commandExecutionPolicy: sandbox' "$PROJECT/.agents/agents/runtime-feature-auditor/agent.md"

# Antigravity 2.0 and Antigravity CLI are first-class selectors but share one workspace payload.
python3 "$ROOT/installer.py" init "$AGY2_PROJECT" --integration antigravity2 --non-interactive --yes >/dev/null
[[ -f "$AGY2_PROJECT/.agents/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$AGY2_PROJECT/.agents/agents/runtime-feature-auditor/agent.md" ]]
python3 "$ROOT/installer.py" init "$AGYCLI_PROJECT" --integration antigravity-cli --non-interactive --yes >/dev/null
[[ -f "$AGYCLI_PROJECT/.agents/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$AGYCLI_PROJECT/.agents/skills/runtime-feature-falsifier/references/ANTIGRAVITY-CLI.md" ]]
[[ -f "$AGYCLI_PROJECT/.agents/agents/runtime-feature-auditor/agent.md" ]]

mkdir -p "$PROJECT/.agents"
printf '{"existing-user-hook":{"enabled":false}}\n' > "$PROJECT/.agents/hooks.json"
python3 "$ROOT/installer.py" --antigravity --strict-google-hooks --project "$PROJECT" --force --no-self-test >/dev/null
[[ -f "$PROJECT/.agents/runtime-feature-falsifier-hooks/agy_pretool_guard.py" ]]
[[ -f "$PROJECT/.agents/runtime-feature-falsifier-hooks/agy_preinvocation_reminder.py" ]]
[[ -f "$PROJECT/.agents/runtime-feature-falsifier-hooks/agy_stop_gate.py" ]]
python3 - "$PROJECT/.agents/hooks.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding='utf-8'))
required={'existing-user-hook','runtime-feature-falsifier-source-guard','runtime-feature-falsifier-context-reminder','runtime-feature-falsifier-stop-gate'}
assert required <= set(d), required-set(d)
PY

GUARD="$PROJECT/.agents/runtime-feature-falsifier-hooks/agy_pretool_guard.py"
NOACTIVE=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"toolCall\":{\"name\":\"write_to_file\",\"args\":{\"TargetFile\":\"$PROJECT/src.py\"}}}" | python3 "$GUARD")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["decision"]=="allow"' "$NOACTIVE"
mkdir -p "$PROJECT/.runtime-feature-audit"
printf '{}\n' > "$PROJECT/.runtime-feature-audit/.active.json"
DENY=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"toolCall\":{\"name\":\"write_to_file\",\"args\":{\"TargetFile\":\"$PROJECT/src.py\"}}}" | python3 "$GUARD")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["decision"]=="deny"' "$DENY"
ALLOW_AUDIT=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"toolCall\":{\"name\":\"write_to_file\",\"args\":{\"TargetFile\":\"$PROJECT/.runtime-feature-audit/audit-plan.json\"}}}" | python3 "$GUARD")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["decision"]=="allow"' "$ALLOW_AUDIT"
DENY_LOG=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"toolCall\":{\"name\":\"write_to_file\",\"args\":{\"TargetFile\":\"$PROJECT/.runtime-feature-audit/attempts.jsonl\"}}}" | python3 "$GUARD")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["decision"]=="deny"' "$DENY_LOG"
DENY_HYP=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"toolCall\":{\"name\":\"write_to_file\",\"args\":{\"TargetFile\":\"$PROJECT/.runtime-feature-audit/hypothesis-ledger.jsonl\"}}}" | python3 "$GUARD")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["decision"]=="deny"' "$DENY_HYP"
REMINDER="$PROJECT/.agents/runtime-feature-falsifier-hooks/agy_preinvocation_reminder.py"
REM=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"]}" | python3 "$REMINDER")
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("injectSteps") and "audit is ACTIVE" in d["injectSteps"][0]["ephemeralMessage"]' "$REM"

STOP="$PROJECT/.agents/runtime-feature-falsifier-hooks/agy_stop_gate.py"
STOP_OUT=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"terminationReason\":\"model_stop\",\"fullyIdle\":true}" | python3 "$STOP")
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["decision"]=="continue" and "cannot finish" in d["reason"]' "$STOP_OUT"
rm -f "$PROJECT/.runtime-feature-audit/.active.json"
STOP_IDLE=$(printf '%s\n' "{\"workspacePaths\":[\"$PROJECT\"],\"terminationReason\":\"model_stop\",\"fullyIdle\":true}" | python3 "$STOP")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["decision"]=="allow"' "$STOP_IDLE"

python3 "$ROOT/installer.py" --gemini --project "$GEMINI_PROJECT" --no-self-test >/dev/null
[[ -f "$GEMINI_PROJECT/.agents/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ ! -e "$GEMINI_PROJECT/.agents/agents/runtime-feature-auditor" ]]

python3 "$ROOT/installer.py" --opencode --project "$OPENCODE_PROJECT" --no-self-test >/dev/null
[[ -f "$OPENCODE_PROJECT/.opencode/skills/runtime-feature-falsifier/SKILL.md" ]]
[[ -f "$OPENCODE_PROJECT/.opencode/skills/runtime-feature-falsifier/references/OPENCODE.md" ]]
[[ -f "$OPENCODE_PROJECT/.opencode/agents/runtime-feature-auditor.md" ]]
[[ ! -e "$OPENCODE_PROJECT/.agents/skills/runtime-feature-falsifier" ]]
grep -q 'resource: "runtime-feature-falsifier"' "$OPENCODE_PROJECT/.opencode/agents/runtime-feature-auditor.md"
grep -q 'action: edit' "$OPENCODE_PROJECT/.opencode/agents/runtime-feature-auditor.md"
# Force update must replace stale native OpenCode content.
touch "$OPENCODE_PROJECT/.opencode/skills/runtime-feature-falsifier/STALE-OPENCODE"
python3 "$ROOT/installer.py" --opencode --project "$OPENCODE_PROJECT" --force --no-self-test >/dev/null
[[ ! -e "$OPENCODE_PROJECT/.opencode/skills/runtime-feature-falsifier/STALE-OPENCODE" ]]

# Installer detection/status mode is read-only and reports the installed version.
DETECT_OUT=$(python3 "$ROOT/installer.py" --detect --project "$PROJECT")
printf '%s\n' "$DETECT_OUT" | grep -q 'Detected agents'
printf '%s\n' "$DETECT_OUT" | grep -q "$VERSION"
[[ "$(cat "$SHARED/VERSION")" == "$VERSION" ]]

# Executable detection must surface supported clients without installing anything.
FAKEBIN="$TMP/fakebin"
mkdir -p "$FAKEBIN"
printf '#!/bin/sh\nexit 0\n' > "$FAKEBIN/codex"
printf '#!/bin/sh\nexit 0\n' > "$FAKEBIN/agy"
printf '#!/bin/sh\nexit 0\n' > "$FAKEBIN/opencode"
chmod +x "$FAKEBIN/codex" "$FAKEBIN/agy" "$FAKEBIN/opencode"
DETECT_FAKE=$(PATH="$FAKEBIN:$PATH" python3 "$ROOT/installer.py" --detect --project "$GEMINI_PROJECT")
printf '%s\n' "$DETECT_FAKE" | grep -q '\[x\].*Codex CLI'
printf '%s\n' "$DETECT_FAKE" | grep -q '\[x\].*Google Antigravity CLI'
printf '%s\n' "$DETECT_FAKE" | grep -q '\[x\].*OpenCode'

# Best-effort Antigravity 2.0 desktop detection uses standard desktop application markers.
FAKEHOME="$TMP/fakehome"
mkdir -p "$FAKEHOME/.local/share/applications"
touch "$FAKEHOME/.local/share/applications/antigravity.desktop"
DETECT_DESKTOP=$(HOME="$FAKEHOME" python3 "$ROOT/installer.py" --detect --project "$GEMINI_PROJECT")
printf '%s\n' "$DETECT_DESKTOP" | grep -q '\[x\].*Google Antigravity 2.0'

# No integration flag in a non-interactive context must fail rather than silently choosing clients.
mkdir -p "$TMP/no-default-project"
set +e
NONINTERACTIVE_OUT=$(printf '' | python3 "$ROOT/installer.py" --project "$TMP/no-default-project" --no-self-test 2>&1)
NONINTERACTIVE_RC=$?
set -e
[[ "$NONINTERACTIVE_RC" -ne 0 ]]
printf '%s\n' "$NONINTERACTIVE_OUT" | grep -q 'non-interactive init requires --integration'

printf 'PASS: Codex/Claude/Antigravity 2.0/Antigravity CLI/Gemini/OpenCode integration layouts and upgrades work\n'
