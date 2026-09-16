#!/usr/bin/env python3
"""Validate the v2.7.0 distribution and the cross-harness installable Agent Skill."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

DIST_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = DIST_ROOT / "runtime-feature-falsifier"


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    skill = SKILL_ROOT / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        errors.append("SKILL.md lacks YAML frontmatter")
        front = ""
    else:
        front = m.group(1)

    name = re.search(r"^name:\s*(.+)$", front, re.M)
    desc = re.search(r"^description:\s*(.+)$", front, re.M)
    if not name or name.group(1).strip() != SKILL_ROOT.name:
        errors.append("frontmatter name must match skill directory")
    if not desc:
        errors.append("frontmatter description missing")
    else:
        d = desc.group(1).strip()
        if len(d) > 1024:
            errors.append(f"description exceeds 1024 chars: {len(d)}")
        if not d.startswith("Use this skill when"):
            errors.append("description should begin with trigger phrasing 'Use this skill when'")

    lines = len(text.splitlines())
    if lines >= 500:
        errors.append(f"SKILL.md should stay under 500 lines; found {lines}")
    if (SKILL_ROOT / "README.md").exists():
        errors.append("installable skill must not contain a root README.md")
    if "be-there-to-look-pretty (BTTLP)" not in text:
        errors.append("SKILL.md must expand BTTLP at first-use/discovery text")
    if "Normative authority" not in text:
        errors.append("SKILL.md must declare itself normative for duplicated audit semantics")
    if "py -3 scripts/auditctl.py" not in text:
        errors.append("SKILL.md must document Windows py -3 fallback")

    # All local links from SKILL.md must resolve inside the installable skill.
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        if "://" in target or target.startswith("#"):
            continue
        rel_target = target.split("#", 1)[0]
        if rel_target and not (SKILL_ROOT / rel_target).exists():
            errors.append(f"SKILL.md has broken local resource link: {target}")

    runtime_required = [
        "scripts/auditctl.py",
        "assets/audit-plan.schema.json",
        "assets/attempt-event.schema.json",
        "assets/attempt-batch.schema.json",
        "assets/feature-inventory.schema.json",
        "assets/hypothesis-event.schema.json",
        "references/FALSIFICATION-METHOD.md",
        "references/EVIDENCE-AND-VERDICTS.md",
        "references/ATTEMPT-LOGGING.md",
        "references/INPUT-MATRICES.md",
        "references/SYSTEM-AUDIT.md",
        "references/GOOGLE-ANTIGRAVITY.md",
        "references/ANTIGRAVITY-CLI.md",
        "references/PERSISTENT-INVESTIGATION.md",
        "references/OPENCODE.md",
        "agents/openai.yaml",
        "vendor/fastjsonschema/__init__.py",
        "licenses/fastjsonschema-LICENSE",
    ]
    for rel in runtime_required:
        if not (SKILL_ROOT / rel).exists():
            errors.append(f"missing installable runtime resource: {rel}")
    for ref in (SKILL_ROOT / "references").glob("*.md"):
        if "Normative authority" not in ref.read_text(encoding="utf-8")[:800]:
            errors.append(f"reference lacks SKILL.md normative-authority banner: {ref.name}")

    # Development/evaluation material belongs in the distribution, not the installed skill.
    forbidden_runtime = [
        "examples",
        "evals",
        "scripts/generate_upload_corpus.py",
        "scripts/validate_bundle.py",
        "scripts/claude_pretool_guard.py",
        "scripts/claude_stop_gate.py",
        "references/ANTI-PATTERN-FIXTURES.md",
    ]
    for rel in forbidden_runtime:
        if (SKILL_ROOT / rel).exists():
            errors.append(f"development/client-specific file leaked into installable skill: {rel}")

    dist_required = [
        "VERSION",
        "install.sh",
        "installer.py",
        "self-test.sh",
        "pyproject.toml",
        "setup.py",
        "src/runtime_feature_falsifier_cli/__init__.py",
        "src/runtime_feature_falsifier_cli/__main__.py",
        "src/runtime_feature_falsifier_cli/cli.py",
        "src/runtime_feature_falsifier_cli/payload/VERSION",
        "src/runtime_feature_falsifier_cli/payload/runtime-feature-falsifier/SKILL.md",
        "tests/cli_package_test.sh",
        "tests/wizard_test.sh",
        "tests/evals/evals.json",
        "tests/evals/trigger_queries.json",
        "tests/tools/generate_upload_corpus.py",
        "tests/teaching-corpus/run_all_examples.py",
        "claude-code/.claude/agents/runtime-feature-auditor.md",
        "claude-code/.claude/runtime-feature-falsifier-hooks/claude_pretool_guard.py",
        "claude-code/.claude/runtime-feature-falsifier-hooks/claude_stop_gate.py",
        "google-antigravity/.agents/agents/runtime-feature-auditor/agent.md",
        "google-antigravity/.agents/runtime-feature-falsifier-hooks/agy_pretool_guard.py",
        "google-antigravity/.agents/runtime-feature-falsifier-hooks/agy_preinvocation_reminder.py",
        "google-antigravity/.agents/runtime-feature-falsifier-hooks/agy_stop_gate.py",
        "google-antigravity/install_hooks.py",
        "opencode/.opencode/agents/runtime-feature-auditor.md",
    ]
    for rel in dist_required:
        if not (DIST_ROOT / rel).exists():
            errors.append(f"missing distribution resource: {rel}")

    version = (DIST_ROOT / "VERSION").read_text(encoding="utf-8").strip() if (DIST_ROOT / "VERSION").exists() else ""
    if version != "2.7.0":
        errors.append(f"VERSION must be 2.7.0; found {version!r}")
    skill_version_path = SKILL_ROOT / "VERSION"
    if not skill_version_path.exists():
        errors.append("installable skill missing VERSION marker used for upgrade detection")
    elif skill_version_path.read_text(encoding="utf-8").strip() != version:
        errors.append("installable skill VERSION does not match distribution VERSION")

    for rel in [
        "assets/audit-plan.schema.json",
        "assets/attempt-event.schema.json",
        "assets/attempt-batch.schema.json",
        "assets/feature-inventory.schema.json",
        "assets/hypothesis-event.schema.json",
    ]:
        try:
            schema_doc = json.loads((SKILL_ROOT / rel).read_text(encoding="utf-8"))
            if schema_doc.get("$schema") != "http://json-schema.org/draft-07/schema#":
                errors.append(f"{rel} must declare JSON Schema Draft 7 for vendored fastjsonschema")
        except Exception as exc:
            errors.append(f"invalid JSON {rel}: {exc}")


    # CLI package metadata and embedded payload must match the release source.
    pyproject = (DIST_ROOT / "pyproject.toml").read_text(encoding="utf-8") if (DIST_ROOT / "pyproject.toml").exists() else ""
    if 'name = "runtime-feature-falsifier-cli"' not in pyproject:
        errors.append("pyproject.toml missing runtime-feature-falsifier-cli project name")
    if f'version = "{version}"' not in pyproject:
        errors.append("pyproject.toml version does not match VERSION")
    if 'rff = "runtime_feature_falsifier_cli:main"' not in pyproject:
        errors.append("pyproject.toml missing rff console-script entry point")

    payload_root = DIST_ROOT / "src/runtime_feature_falsifier_cli/payload"
    payload_version = payload_root / "VERSION"
    if not payload_version.exists() or payload_version.read_text(encoding="utf-8").strip() != version:
        errors.append("CLI embedded payload VERSION does not match distribution VERSION")

    def tree_files(root: Path) -> dict[str, bytes]:
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    mirror_pairs = [
        (DIST_ROOT / "runtime-feature-falsifier", payload_root / "runtime-feature-falsifier", "portable skill"),
        (DIST_ROOT / "claude-code", payload_root / "claude-code", "Claude companion"),
        (DIST_ROOT / "google-antigravity", payload_root / "google-antigravity", "Google companion"),
        (DIST_ROOT / "opencode", payload_root / "opencode", "OpenCode companion"),
    ]
    for source_root, mirror_root, label in mirror_pairs:
        if source_root.exists() and mirror_root.exists() and tree_files(source_root) != tree_files(mirror_root):
            errors.append(f"CLI embedded {label} payload differs from distribution source")

    wheel = DIST_ROOT / "dist" / f"runtime_feature_falsifier_cli-{version}-py3-none-any.whl"
    if not wheel.exists():
        errors.append(f"prebuilt offline CLI wheel missing: {wheel.name}")
    else:
        try:
            with zipfile.ZipFile(wheel) as zf:
                leaked = [n for n in zf.namelist() if n.endswith(".pyc") or "/__pycache__/" in n]
                if leaked:
                    errors.append("prebuilt CLI wheel contains Python bytecode/cache artifacts: " + ", ".join(leaked[:10]))
        except Exception as exc:
            errors.append(f"could not inspect prebuilt CLI wheel: {exc}")

    for rel in ["tests/evals/evals.json", "tests/evals/trigger_queries.json"]:
        try:
            json.loads((DIST_ROOT / rel).read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"invalid distribution JSON {rel}: {exc}")

    openai_path = SKILL_ROOT / "agents/openai.yaml"
    openai = openai_path.read_text(encoding="utf-8") if openai_path.exists() else ""
    sm = re.search(r"short_description:\s*[\"']?([^\"'\n]+)", openai)
    if not sm:
        errors.append("agents/openai.yaml missing short_description")
    else:
        n = len(sm.group(1).strip())
        if not 25 <= n <= 64:
            errors.append(f"OpenAI short_description should be 25-64 chars; found {n}")
    if "$runtime-feature-falsifier" not in openai:
        errors.append("agents/openai.yaml default_prompt should mention $runtime-feature-falsifier")

    if '--target-root "<project-root>" \\\n  --target-root "<project-root>"' in text:
        errors.append("SKILL.md contains duplicate --target-root in init example")

    # Gemini CLI's current skill creator recommends only name + description frontmatter.
    front_keys = []
    for raw in front.splitlines():
        if raw and not raw.startswith((" ", "\t")) and ":" in raw:
            front_keys.append(raw.split(":", 1)[0].strip())
    if front_keys != ["name", "description"]:
        errors.append(f"portable SKILL.md frontmatter should contain only name + description; found {front_keys}")

    opencode_agent_path = DIST_ROOT / "opencode/.opencode/agents/runtime-feature-auditor.md"
    opencode_agent = opencode_agent_path.read_text(encoding="utf-8") if opencode_agent_path.exists() else ""
    for required in (
        "description: Persistently falsifies claimed runtime features",
        "mode: primary",
        "action: edit",
        "effect: deny",
        'resource: "runtime-feature-falsifier"',
        "action: skill",
    ):
        if required not in opencode_agent:
            errors.append(f"OpenCode custom auditor missing required marker: {required}")
    if ".opencode/skills/runtime-feature-falsifier" not in (SKILL_ROOT / "references/OPENCODE.md").read_text(encoding="utf-8"):
        errors.append("OpenCode reference must document the native project skill path")

    google_agent_path = DIST_ROOT / "google-antigravity/.agents/agents/runtime-feature-auditor/agent.md"
    google_agent = google_agent_path.read_text(encoding="utf-8") if google_agent_path.exists() else ""
    for required in ("name: runtime-feature-auditor", "mainAgent: true", "model: inherit", "skills:", "skills/runtime-feature-falsifier"):
        if required not in google_agent:
            errors.append(f"Antigravity custom auditor missing required marker: {required}")
    for forbidden in ("  - write_to_file", "  - replace_file_content", "  - multi_replace_file_content"):
        if forbidden in google_agent:
            errors.append(f"Antigravity dedicated auditor must not directly expose mutating file tool: {forbidden.strip()}")

    google_ref = (SKILL_ROOT / "references/GOOGLE-ANTIGRAVITY.md").read_text(encoding="utf-8")
    agy_ref = (SKILL_ROOT / "references/ANTIGRAVITY-CLI.md").read_text(encoding="utf-8")
    if ".agents/skills/runtime-feature-falsifier" not in google_ref or ".agents/agents/runtime-feature-auditor" not in google_ref:
        errors.append("Antigravity 2.0 reference must document native workspace skill and custom-agent paths")
    for marker in ("agy", "/skills", "/agents", "~/.gemini/antigravity-cli/skills/"):
        if marker not in agy_ref:
            errors.append(f"Antigravity CLI reference missing required marker: {marker}")
    cli_source = (DIST_ROOT / "src/runtime_feature_falsifier_cli/cli.py").read_text(encoding="utf-8")
    for marker in ('"antigravity2"', '"antigravity-cli"', 'GOOGLE_CLIENTS', '--antigravity-cli', '--antigravity2'):
        if marker not in cli_source:
            errors.append(f"rff CLI missing split Antigravity integration marker: {marker}")

    help_env = dict(__import__("os").environ)
    help_env["PYTHONDONTWRITEBYTECODE"] = "1"
    help_proc = subprocess.run(
        [sys.executable, str(SKILL_ROOT / "scripts" / "auditctl.py"), "--help"],
        capture_output=True,
        text=True,
        env=help_env,
    )
    if help_proc.returncode != 0 or "attempt-start" not in help_proc.stdout or "attempt-batch" not in help_proc.stdout or "hypothesis-open" not in help_proc.stdout or "investigation-status" not in help_proc.stdout or "gate" not in help_proc.stdout:
        errors.append("auditctl --help failed or is incomplete")
    ctl_text = (SKILL_ROOT / "scripts/auditctl.py").read_text(encoding="utf-8")
    for marker in (
        'load_schema("audit-plan.schema.json")',
        'load_schema("attempt-event.schema.json")',
        'load_schema("feature-inventory.schema.json")',
        'load_schema("hypothesis-event.schema.json")',
        'load_schema("attempt-batch.schema.json")',
        'msvcrt',
        'chain_heads',
        'startup health has not completed SURVIVED',
        'import fastjsonschema',
        'return _read_events_unlocked(log_path)',
        'with exclusive_lock(_jsonl_lock_path(log_path))',
    ):
        if marker not in ctl_text:
            errors.append(f"auditctl missing v2.7.0 hardening marker: {marker}")

    if 'def _json_type_ok(' in ctl_text:
        errors.append("auditctl still contains the retired hand-written JSON Schema mini-validator")

    leaked_bytecode = [str(x.relative_to(DIST_ROOT)) for x in DIST_ROOT.rglob("*.pyc")]
    leaked_caches = [str(x.relative_to(DIST_ROOT)) for x in DIST_ROOT.rglob("__pycache__") if x.is_dir()]
    if leaked_bytecode or leaked_caches:
        errors.append("release tree contains Python bytecode/cache artifacts: " + ", ".join((leaked_bytecode + leaked_caches)[:10]))

    result = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "skill_lines": lines,
        "version": version,
        "installable_python_files": len(list(SKILL_ROOT.rglob("*.py"))),
        "distribution_test_python_files": len(list((DIST_ROOT / "tests").rglob("*.py"))),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
