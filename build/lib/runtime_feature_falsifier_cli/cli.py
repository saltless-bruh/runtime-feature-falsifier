#!/usr/bin/env python3
"""Runtime Feature Falsifier project bootstrap CLI.

The CLI is intentionally dependency-free at runtime. It installs the portable
Agent Skill plus client-specific companion files into an existing project.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import secrets
import tomllib
from uuid import UUID
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PACKAGE_ROOT = Path(__file__).resolve().parent


def _payload_root() -> Path:
    """Locate bundled payload in an installed wheel or source checkout."""
    installed = PACKAGE_ROOT / "payload"
    if (installed / "runtime-feature-falsifier" / "SKILL.md").is_file():
        return installed
    # Source checkout: <root>/src/runtime_feature_falsifier_cli/cli.py
    source = PACKAGE_ROOT.parents[1]
    if (source / "runtime-feature-falsifier" / "SKILL.md").is_file():
        return source
    raise RuntimeError("Runtime Feature Falsifier payload could not be located")


PAYLOAD = _payload_root()
VERSION_FILE = PAYLOAD / "VERSION"
VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()
SOURCE_SKILL = PAYLOAD / "runtime-feature-falsifier"
SOURCE_CLAUDE_AGENT = PAYLOAD / "claude-code/.claude/agents/runtime-feature-auditor.md"
SOURCE_CLAUDE_HOOKS = PAYLOAD / "claude-code/.claude/runtime-feature-falsifier-hooks"
SOURCE_GOOGLE_AGENT = PAYLOAD / "google-antigravity/.agents/agents/runtime-feature-auditor"
SOURCE_OPENCODE_AGENT = PAYLOAD / "opencode/.opencode/agents/runtime-feature-auditor.md"
SOURCE_GOOGLE_HOOKS = PAYLOAD / "google-antigravity/.agents/runtime-feature-falsifier-hooks"
GOOGLE_HOOK_INSTALLER = PAYLOAD / "google-antigravity/install_hooks.py"


@dataclass(frozen=True)
class Client:
    key: str
    label: str
    command: str | None
    project_markers: tuple[str, ...]
    destination: str


CLIENTS = (
    Client("codex", "Codex CLI", "codex", (".codex",), ".agents/skills/runtime-feature-falsifier/"),
    Client(
        "antigravity2",
        "Google Antigravity 2.0",
        None,
        (),
        ".agents/skills/ + dedicated auditor",
    ),
    Client(
        "antigravity-cli",
        "Google Antigravity CLI",
        "agy",
        (),
        ".agents/skills/ + dedicated auditor",
    ),
    Client("gemini", "Gemini CLI", "gemini", (".gemini",), ".agents/skills/runtime-feature-falsifier/"),
    Client(
        "opencode",
        "OpenCode",
        "opencode",
        (".opencode", "opencode.json", "opencode.jsonc"),
        ".opencode/skills/ + dedicated auditor",
    ),
    Client(
        "claude",
        "Claude Code",
        "claude",
        (".claude",),
        ".claude/skills/ + dedicated auditor/hooks",
    ),
)
GOOGLE_CLIENTS = {"antigravity2", "antigravity-cli"}

CLIENT_BY_KEY = {c.key: c for c in CLIENTS}


class UI:
    def __init__(self, interactive: bool) -> None:
        self.interactive = interactive
        self.color = interactive and os.environ.get("NO_COLOR") is None

    def style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def title(self, text: str) -> None:
        print(self.style(text, "1;36"))

    def ok(self, text: str) -> str:
        return self.style(text, "32")

    def warn(self, text: str) -> str:
        return self.style(text, "33")

    def dim(self, text: str) -> str:
        return self.style(text, "2")

    def bold(self, text: str) -> str:
        return self.style(text, "1")


def _add_project_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", nargs="?", type=Path, help="Existing target project directory")
    p.add_argument("--project", type=Path, help="Legacy alias for target project directory")
    p.add_argument("--here", action="store_true", help="Initialize the current directory")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rff",
        description="Runtime Feature Falsifier CLI — install/update the audit skill for coding-agent clients.",
    )
    p.add_argument("--version", action="version", version=f"rff {VERSION}")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Install or update integrations in an existing project")
    _add_project_args(init)
    init.add_argument(
        "--integration",
        "-i",
        action="append",
        choices=[c.key for c in CLIENTS] + ["antigravity", "all", "detected"],
        help="Integration to install; repeat for multiple clients",
    )
    # Backward-compatible shortcuts used by v2.x install.sh.
    init.add_argument("--codex", action="store_true", help=argparse.SUPPRESS)
    # Legacy combined shortcut: install support shared by both Antigravity 2.0 and Antigravity CLI.
    init.add_argument("--antigravity", "--google", dest="antigravity_all", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--antigravity2", "--antigravity-2", dest="antigravity2", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--antigravity-cli", "--agy", dest="antigravity_cli", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--gemini", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--opencode", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--claude", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--both", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--force", action="store_true", help="Replace/update existing managed destinations")
    init.add_argument("--no-self-test", action="store_true", help=argparse.SUPPRESS)
    init.add_argument("--non-interactive", action="store_true", help="Never prompt; requires explicit integration")
    init.add_argument("--yes", "-y", action="store_true", help="Accept the displayed install/update plan")
    init.add_argument(
        "--strict-google-hooks",
        action="store_true",
        help="Install optional workspace-wide Antigravity enforcement hooks",
    )

    detect = sub.add_parser("detect", help="Read-only client and installation-state detection")
    _add_project_args(detect)

    doctor = sub.add_parser("doctor", help="Validate bundled payload and optionally a project installation")
    _add_project_args(doctor)

    integration = sub.add_parser("integration", help="Inspect supported agent integrations")
    integration_sub = integration.add_subparsers(dest="integration_command")
    ilist = integration_sub.add_parser("list", help="List supported integrations and detection status")
    _add_project_args(ilist)

    selfp = sub.add_parser("self", help="Inspect or upgrade the installed rff CLI")
    selfsub = selfp.add_subparsers(dest="self_command")
    selfsub.add_parser("version", help="Print the installed CLI version")
    selfsub.add_parser("check", help="Validate the installed CLI payload")
    sup = selfsub.add_parser("upgrade", help="Upgrade rff using uv from an explicit source")
    sup.add_argument("--from", dest="source", required=True, help="Path, package source, or git+https URL")
    sup.add_argument("--dry-run", action="store_true", help="Print the uv command without running it")

    audit = sub.add_parser("audit", help="Operate the RFF audit control plane", add_help=False)
    audit.add_argument("-h", "--help", dest="audit_help", action="store_true")
    audit.add_argument("audit_args", nargs=argparse.REMAINDER, help="Audit command and arguments")

    config = sub.add_parser("config", help="Inspect or update project-local RFF configuration", add_help=False)
    config.add_argument("-h", "--help", dest="config_help", action="store_true")
    config.add_argument("config_args", nargs=argparse.REMAINDER, help="Configuration command and arguments")

    return p


def _interactive(args: argparse.Namespace) -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and not getattr(args, "non_interactive", False)


def _project_was_explicit(args: argparse.Namespace) -> bool:
    return any(
        (
            getattr(args, "path", None) is not None,
            getattr(args, "project", None) is not None,
            bool(getattr(args, "here", False)),
        )
    )


def _looks_like_release_bundle(path: Path) -> bool:
    return (
        (path / "install.sh").is_file()
        and (path / "VERSION").is_file()
        and (path / "runtime-feature-falsifier" / "SKILL.md").is_file()
    )


def choose_project(ui: UI) -> Path:
    cwd = Path.cwd().resolve()
    default = None if _looks_like_release_bundle(cwd) else cwd
    while True:
        if default is None:
            raw = input("Target project: ").strip()
        else:
            raw = input(f"Target project [{default}]: ").strip()
        if not raw and default is not None:
            return default
        if not raw:
            print(ui.warn("Enter an existing project directory."))
            continue
        raw = raw.strip('\"\'')
        target = Path(raw).expanduser().resolve()
        if target.exists() and target.is_dir():
            return target
        print(ui.warn(f"Project directory does not exist: {target}"))


def resolve_project(args: argparse.Namespace, *, require_existing: bool = True) -> Path:
    candidates = [getattr(args, "path", None), getattr(args, "project", None)]
    if getattr(args, "here", False):
        candidates.append(Path.cwd())
    chosen = [p for p in candidates if p is not None]
    if len(chosen) > 1:
        # path + --project is only okay if they resolve to the same location.
        resolved = {str(Path(p).expanduser().resolve()) for p in chosen}
        if len(resolved) > 1:
            raise SystemExit("ERROR: specify only one of PATH, --project, or --here")
    target = Path(chosen[0]).expanduser() if chosen else Path.cwd()
    target = target.resolve()
    if require_existing and (not target.exists() or not target.is_dir()):
        raise SystemExit(f"ERROR: project directory does not exist: {target}")
    return target


def command_state(command: str | None) -> str | None:
    return shutil.which(command) if command else None


def marker_state(project: Path, markers: Iterable[str]) -> list[str]:
    return [marker for marker in markers if (project / marker).exists()]


def antigravity2_desktop_state() -> str | None:
    """Best-effort desktop-app detection; explicit selection remains authoritative."""
    candidates: list[Path] = []
    home = Path.home()
    if sys.platform == "darwin":
        candidates += [Path("/Applications/Antigravity.app"), home / "Applications/Antigravity.app"]
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = Path(local)
            candidates += [
                base / "Programs/Antigravity/Antigravity.exe",
                base / "Antigravity/Antigravity.exe",
            ]
    else:
        candidates += [
            Path("/usr/share/applications/antigravity.desktop"),
            home / ".local/share/applications/antigravity.desktop",
        ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def detected_clients(project: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for client in CLIENTS:
        if client.key == "antigravity2":
            out[client.key] = antigravity2_desktop_state() is not None
        else:
            out[client.key] = bool(command_state(client.command) or marker_state(project, client.project_markers))
    return out


def installed_version(path: Path) -> str | None:
    vf = path / "VERSION"
    if vf.is_file():
        value = vf.read_text(encoding="utf-8", errors="replace").strip()
        return value or None
    skill = path / "SKILL.md"
    if skill.is_file() and "name: runtime-feature-falsifier" in skill.read_text(encoding="utf-8", errors="replace"):
        return "legacy/unknown"
    return None


def show_detection(project: Path, ui: UI) -> dict[str, bool]:
    detected = detected_clients(project)
    ui.title(f"Runtime Feature Falsifier v{VERSION}")
    print(f"Project: {ui.bold(str(project))}\n")
    print(ui.bold("Detected agents"))
    for client in CLIENTS:
        found = detected[client.key]
        mark = ui.ok("[x]") if found else ui.dim("[ ]")
        detail = ""
        exe = command_state(client.command)
        markers = marker_state(project, client.project_markers)
        desktop = antigravity2_desktop_state() if client.key == "antigravity2" else None
        if desktop:
            detail = ui.dim(f"  {desktop}")
        elif exe:
            detail = ui.dim(f"  {exe}")
        elif markers:
            detail = ui.dim(f"  project marker: {', '.join(markers)}")
        print(f"  {mark} {client.label}{detail}")

    installed: list[str] = []
    shared = project / ".agents/skills/runtime-feature-falsifier"
    claude = project / ".claude/skills/runtime-feature-falsifier"
    opencode = project / ".opencode/skills/runtime-feature-falsifier"
    if (version := installed_version(shared)):
        installed.append(f"shared skill {version}")
    if (version := installed_version(claude)):
        installed.append(f"Claude skill {version}")
    if (version := installed_version(opencode)):
        installed.append(f"OpenCode skill {version}")
    if installed:
        print("\n" + ui.dim("Existing RFF: " + ", ".join(installed)))
    print()
    return detected


def integration_list(project: Path, ui: UI) -> int:
    detected = detected_clients(project)
    ui.title("Supported integrations")
    print(f"Project context: {project}\n")
    for client in CLIENTS:
        status = ui.ok("detected") if detected[client.key] else ui.dim("not detected")
        print(f"  {client.key:<12} {client.label:<34} {status:<18} {client.destination}")
    print("\nUse: rff init --here --integration <name>")
    return 0


def explicit_selection(args: argparse.Namespace, detected: dict[str, bool]) -> set[str] | None:
    selected: set[str] = set()
    values = list(getattr(args, "integration", None) or [])
    if getattr(args, "codex", False):
        values.append("codex")
    if getattr(args, "antigravity_all", False):
        values += ["antigravity2", "antigravity-cli"]
    if getattr(args, "antigravity2", False):
        values.append("antigravity2")
    if getattr(args, "antigravity_cli", False):
        values.append("antigravity-cli")
    if getattr(args, "gemini", False):
        values.append("gemini")
    if getattr(args, "opencode", False):
        values.append("opencode")
    if getattr(args, "claude", False):
        values.append("claude")
    if getattr(args, "both", False):
        values += ["codex", "claude"]
    if getattr(args, "all", False):
        values.append("all")
    if not values:
        return None
    for value in values:
        if value == "all":
            selected.update(CLIENT_BY_KEY)
        elif value == "antigravity":
            selected.update(GOOGLE_CLIENTS)
        elif value == "detected":
            selected.update(k for k, v in detected.items() if v)
        else:
            selected.add(value)
    return selected


def parse_choices(raw: str, detected: dict[str, bool]) -> set[str]:
    raw = raw.strip().lower()
    if not raw:
        return {k for k, v in detected.items() if v}
    tokens = {part.strip() for part in raw.replace(" ", ",").split(",") if part.strip()}
    if tokens & {"0", "q", "quit"}:
        return set()
    if tokens & {"1", "detected"}:
        return {k for k, v in detected.items() if v}
    if tokens & {"8", "all"}:
        return set(CLIENT_BY_KEY)
    number_to_key = {
        "2": "codex",
        "3": "antigravity2",
        "4": "antigravity-cli",
        "5": "gemini",
        "6": "opencode",
        "7": "claude",
    }
    aliases = {
        "agy": "antigravity-cli",
        "antigravity-2": "antigravity2",
        **{k: k for k in CLIENT_BY_KEY},
    }
    result: set[str] = set()
    for token in tokens:
        if token in {"antigravity", "google"}:
            result.update(GOOGLE_CLIENTS)
            continue
        key = number_to_key.get(token) or aliases.get(token)
        if not key:
            raise ValueError(f"unknown selection: {token}")
        result.add(key)
    return result


def choose_integrations(detected: dict[str, bool], ui: UI) -> set[str]:
    count = sum(detected.values())
    print(ui.bold("Install for"))
    if count:
        print(f"  [1] All detected ({count}) {ui.dim('(recommended)')}")
    else:
        print(f"  [1] All detected (0) {ui.dim('(none found)')}")
    rows = [
        ("2", "codex"),
        ("3", "antigravity2"),
        ("4", "antigravity-cli"),
        ("5", "gemini"),
        ("6", "opencode"),
        ("7", "claude"),
    ]
    for n, key in rows:
        client = CLIENT_BY_KEY[key]
        status = ui.ok("detected") if detected.get(key) else ui.dim("not detected")
        print(f"  [{n}] {client.label:<34} {status}")
    print("  [8] All supported")
    print("  [0] Cancel")
    if count:
        print(ui.dim("  Tip: choose multiple with commas, e.g. 2,3"))
    while True:
        raw = input(f"Selection{' [1]' if count else ''}: ")
        effective = raw if raw.strip() else ("1" if count else "")
        try:
            selected = parse_choices(effective, detected)
        except ValueError as exc:
            print(ui.warn(str(exc)))
            continue
        if selected:
            return selected
        if raw.strip().lower() in {"0", "q", "quit"}:
            return set()
        print(ui.warn("No integrations selected. Choose one or cancel."))


def yes_no(prompt: str, default: bool, ui: UI) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        raw = input(prompt + suffix).strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print(ui.warn("Please answer y or n."))


def validate_payload(selected: set[str] | None = None) -> list[str]:
    selected = selected or set(CLIENT_BY_KEY)
    required = [SOURCE_SKILL / "SKILL.md", SOURCE_SKILL / "scripts/auditctl.py", VERSION_FILE]
    if "claude" in selected:
        required += [SOURCE_CLAUDE_AGENT, SOURCE_CLAUDE_HOOKS / "claude_pretool_guard.py", SOURCE_CLAUDE_HOOKS / "claude_stop_gate.py"]
    if selected & GOOGLE_CLIENTS:
        required += [SOURCE_GOOGLE_AGENT / "agent.md", SOURCE_GOOGLE_HOOKS / "agy_pretool_guard.py", GOOGLE_HOOK_INSTALLER]
    if "opencode" in selected:
        required += [SOURCE_OPENCODE_AGENT, SOURCE_SKILL / "references/OPENCODE.md"]
    return [str(p) for p in required if not p.exists()]


def atomic_replace_tree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{dst.name}.stage.", dir=dst.parent))
    backup = dst.parent / f".{dst.name}.backup.{os.getpid()}"
    try:
        shutil.rmtree(stage)
        shutil.copytree(src, stage, symlinks=True)
        if backup.exists():
            shutil.rmtree(backup) if backup.is_dir() else backup.unlink()
        if dst.exists() or dst.is_symlink():
            dst.rename(backup)
        stage.rename(dst)
        if backup.exists():
            shutil.rmtree(backup) if backup.is_dir() else backup.unlink()
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if backup.exists() and not dst.exists():
            backup.rename(dst)
        raise


def atomic_replace_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, stage_name = tempfile.mkstemp(prefix=f".{dst.name}.stage.", dir=dst.parent)
    os.close(fd)
    stage = Path(stage_name)
    backup = dst.parent / f".{dst.name}.backup.{os.getpid()}"
    try:
        shutil.copy2(src, stage)
        if backup.exists():
            backup.unlink()
        if dst.exists() or dst.is_symlink():
            dst.rename(backup)
        stage.rename(dst)
        if backup.exists():
            backup.unlink()
    except Exception:
        stage.unlink(missing_ok=True)
        if backup.exists() and not dst.exists():
            backup.rename(dst)
        raise


def destination_plan(project: Path, selected: set[str], strict_google_hooks: bool) -> list[tuple[str, Path]]:
    plan: list[tuple[str, Path]] = []
    if selected & ({"codex", "gemini"} | GOOGLE_CLIENTS):
        plan.append(("Shared Agent Skill", project / ".agents/skills/runtime-feature-falsifier"))
    if "opencode" in selected:
        plan += [
            ("OpenCode skill", project / ".opencode/skills/runtime-feature-falsifier"),
            ("OpenCode auditor", project / ".opencode/agents/runtime-feature-auditor.md"),
        ]
    if selected & GOOGLE_CLIENTS:
        plan.append(("Antigravity auditor", project / ".agents/agents/runtime-feature-auditor"))
        if strict_google_hooks:
            plan.append(("Antigravity strict hooks", project / ".agents/runtime-feature-falsifier-hooks"))
            plan.append(("Antigravity hooks registry (merge)", project / ".agents/hooks.json"))
    if "claude" in selected:
        plan += [
            ("Claude skill", project / ".claude/skills/runtime-feature-falsifier"),
            ("Claude auditor", project / ".claude/agents/runtime-feature-auditor.md"),
            ("Claude hooks", project / ".claude/runtime-feature-falsifier-hooks"),
        ]
    return plan


def preflight_existing(project: Path, selected: set[str], strict_google_hooks: bool, force: bool) -> None:
    collisions = [
        path
        for label, path in destination_plan(project, selected, strict_google_hooks)
        if path.exists() and label != "Antigravity hooks registry (merge)"
    ]
    if collisions and not force:
        lines = "\n  ".join(str(p) for p in collisions)
        raise SystemExit(f"ERROR: destination already exists:\n  {lines}\nUse --force to upgrade/replace.")


def show_plan(project: Path, selected: set[str], strict_hooks: bool, ui: UI) -> bool:
    print("\n" + ui.bold("Installation plan"))
    print("  Integrations: " + ", ".join(c.label for c in CLIENTS if c.key in selected))
    print(f"  Project:      {project}")
    existing = False
    for label, path in destination_plan(project, selected, strict_hooks):
        if path.exists():
            action = ui.warn("UPDATE")
            existing = True
        else:
            action = ui.ok("INSTALL")
        print(f"  {action:<18} {label:<29} {path}")
    print()
    return existing


def write_version(skill_dst: Path) -> None:
    (skill_dst / "VERSION").write_text(VERSION + "\n", encoding="utf-8")


def install_shared(project: Path, ui: UI) -> None:
    dst = project / ".agents/skills/runtime-feature-falsifier"
    atomic_replace_tree(SOURCE_SKILL, dst)
    write_version(dst)
    print(f"  {ui.ok('OK')}  Shared Agent Skill -> {dst}")


def install_claude(project: Path, ui: UI) -> None:
    skill = project / ".claude/skills/runtime-feature-falsifier"
    agent = project / ".claude/agents/runtime-feature-auditor.md"
    hooks = project / ".claude/runtime-feature-falsifier-hooks"
    atomic_replace_tree(SOURCE_SKILL, skill)
    write_version(skill)
    atomic_replace_file(SOURCE_CLAUDE_AGENT, agent)
    atomic_replace_tree(SOURCE_CLAUDE_HOOKS, hooks)
    print(f"  {ui.ok('OK')}  Claude skill -> {skill}")
    print(f"  {ui.ok('OK')}  Claude auditor -> {agent}")
    print(f"  {ui.ok('OK')}  Claude hooks -> {hooks}")


def install_antigravity(project: Path, strict_hooks: bool, ui: UI) -> None:
    agent = project / ".agents/agents/runtime-feature-auditor"
    atomic_replace_tree(SOURCE_GOOGLE_AGENT, agent)
    print(f"  {ui.ok('OK')}  Antigravity auditor -> {agent / 'agent.md'}")
    if strict_hooks:
        hooks = project / ".agents/runtime-feature-falsifier-hooks"
        atomic_replace_tree(SOURCE_GOOGLE_HOOKS, hooks)
        proc = subprocess.run(
            [
                sys.executable,
                str(GOOGLE_HOOK_INSTALLER),
                "--target",
                str(project / ".agents/hooks.json"),
                "--hooks-dir",
                str(hooks),
            ],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"ERROR: failed to merge Antigravity hooks:\n{proc.stderr or proc.stdout}")
        print(f"  {ui.ok('OK')}  Antigravity strict hooks -> {hooks}")
        print(f"  {ui.ok('OK')}  Hooks registry merged -> {project / '.agents/hooks.json'}")


def install_opencode(project: Path, ui: UI) -> None:
    skill = project / ".opencode/skills/runtime-feature-falsifier"
    agent = project / ".opencode/agents/runtime-feature-auditor.md"
    atomic_replace_tree(SOURCE_SKILL, skill)
    write_version(skill)
    atomic_replace_file(SOURCE_OPENCODE_AGENT, agent)
    print(f"  {ui.ok('OK')}  OpenCode skill -> {skill}")
    print(f"  {ui.ok('OK')}  OpenCode auditor -> {agent}")


def cmd_init(args: argparse.Namespace) -> int:
    interactive = _interactive(args)
    ui = UI(interactive)
    if interactive and not _project_was_explicit(args):
        project = choose_project(ui)
        print()
    else:
        project = resolve_project(args)
    detected = detected_clients(project)
    selected = explicit_selection(args, detected)
    if selected is None:
        if not interactive:
            raise SystemExit("ERROR: non-interactive init requires --integration <name>")
        show_detection(project, ui)
        selected = choose_integrations(detected, ui)
    if not selected:
        print("Cancelled.")
        return 0

    strict_hooks = bool(args.strict_google_hooks)
    if strict_hooks and not (selected & GOOGLE_CLIENTS):
        raise SystemExit("ERROR: --strict-google-hooks requires --integration antigravity2 or antigravity-cli")
    # Strict Antigravity hooks are workspace-wide, so the simple wizard keeps
    # them opt-in. Use --strict-google-hooks when that stronger policy is desired.

    missing = validate_payload(selected)
    if missing:
        raise SystemExit("ERROR: installed rff payload is incomplete:\n  " + "\n  ".join(missing))

    existing = show_plan(project, selected, strict_hooks, ui)
    force = bool(args.force)
    if interactive:
        if existing and not force:
            force = yes_no("Existing managed files were detected. Update/replace them?", True, ui)
            if not force:
                print("Cancelled; no files changed.")
                return 0
        if not args.yes and not yes_no("Proceed with this installation plan?", True, ui):
            print("Cancelled; no files changed.")
            return 0
    else:
        preflight_existing(project, selected, strict_hooks, force)

    ui.title("Installing")
    if selected & ({"codex", "gemini"} | GOOGLE_CLIENTS):
        install_shared(project, ui)
    if selected & GOOGLE_CLIENTS:
        install_antigravity(project, strict_hooks, ui)
    if "opencode" in selected:
        install_opencode(project, ui)
    if "claude" in selected:
        install_claude(project, ui)

    print("\n" + ui.ok("Installation complete."))
    print(f"Version: {VERSION}")
    print(f"Project: {project}")
    if "codex" in selected:
        print("  Codex: use $runtime-feature-falsifier; use /plan first for SYSTEM audits.")
    if "antigravity2" in selected:
        print("  Antigravity 2.0: start/reopen the Project, confirm runtime-feature-falsifier is available, and select runtime-feature-auditor from custom agents.")
    if "antigravity-cli" in selected:
        print("  Antigravity CLI: run /skills to confirm the skill and /agents to select runtime-feature-auditor.")
    if selected & GOOGLE_CLIENTS and not strict_hooks:
        print("  Antigravity strict workspace hooks: not installed (safe default).")
    if "gemini" in selected:
        print("  Gemini CLI: run /skills reload (or restart), then confirm runtime-feature-falsifier.")
    if "opencode" in selected:
        print("  OpenCode: restart/start a fresh session, load skill ID runtime-feature-falsifier, or select runtime-feature-auditor.")
    if "claude" in selected:
        print("  Claude Code: invoke/select runtime-feature-auditor.")
    print("Audit artifacts: configurable via .rff.toml; inspect with `rff audit where`")
    return 0



# v2.9 audit control-plane -----------------------------------------------------
DEFAULT_OUTPUT_DIR = ".runtime-feature-audit"
RFF_CONFIG = ".rff.toml"
FORBIDDEN_OUTPUT_PARTS = {".git", ".github", ".gitlab", "node_modules", ".venv", "venv", "vendor", "build", "dist", "target", "__pycache__"}


def _discover_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / RFF_CONFIG).is_file():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return current


def _load_project_config(project: Path) -> dict:
    path = project / RFF_CONFIG
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise SystemExit(f"ERROR: invalid {RFF_CONFIG}: {exc}")
    return data if isinstance(data, dict) else {}


def _configured_output(project: Path) -> str:
    data = _load_project_config(project)
    audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
    value = audit.get("output_dir", DEFAULT_OUTPUT_DIR)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit("ERROR: [audit].output_dir must be a non-empty string")
    return value.strip()


def _validate_output_root(project: Path, raw: str, *, allow_deep: bool = False) -> tuple[Path, list[str]]:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        raise SystemExit("ERROR: audit.output_dir must be project-relative")
    if ".." in candidate.parts:
        raise SystemExit("ERROR: audit.output_dir must not contain '..' traversal")
    parts = tuple(x for x in candidate.parts if x not in {"", "."})
    if not parts:
        raise SystemExit("ERROR: audit.output_dir cannot resolve to the project root")
    if any(x in FORBIDDEN_OUTPUT_PARTS for x in parts):
        raise SystemExit("ERROR: audit.output_dir targets a reserved/generated directory")
    lowered = "/".join(parts).lower()
    forbidden_skill_fragments = (
        ".agents/skills/runtime-feature-falsifier",
        ".opencode/skills/runtime-feature-falsifier",
        ".claude/skills/runtime-feature-falsifier",
    )
    if any(fragment in lowered for fragment in forbidden_skill_fragments):
        raise SystemExit("ERROR: audit output must not be placed inside an installed RFF skill")
    resolved = (project / candidate).resolve()
    try:
        resolved.relative_to(project.resolve())
    except ValueError:
        raise SystemExit("ERROR: audit.output_dir resolves outside the project root")
    warnings: list[str] = []
    if len(parts) > 4:
        if not allow_deep:
            raise SystemExit("ERROR: audit.output_dir is deeper than 4 directories; use --allow-deep-output to acknowledge intentional deep placement")
        warnings.append(f"deep output root accepted by explicit override (depth={len(parts)})")
    if parts[0] in {"src", "source", "services", "packages", "app", "apps"}:
        warnings.append("audit output is inside a likely source-code tree; generated audit state is normally better kept in an artifact/quality directory")
    return resolved, warnings


def _uuid7() -> str:
    """Generate an RFC 9562 UUIDv7 using Unix epoch milliseconds + CSPRNG bits."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(UUID(int=value))


def _root_metadata_path(output_root: Path, name: str) -> Path:
    return output_root / name


def _read_pointer(output_root: Path, name: str) -> dict | None:
    path = _root_metadata_path(output_root, name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        raise SystemExit(f"ERROR: invalid RFF pointer file: {path}")


def _pointer_run(output_root: Path, name: str) -> Path | None:
    data = _read_pointer(output_root, name)
    if not data or not data.get("path"):
        return None
    path = (output_root / data["path"]).resolve()
    try:
        path.relative_to(output_root.resolve())
    except ValueError:
        raise SystemExit(f"ERROR: unsafe run pointer in {name}")
    return path


def _auditctl_path() -> Path:
    return SOURCE_SKILL / "scripts/auditctl.py"


def _run_auditctl(argv: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(_auditctl_path()), *argv]
    if capture:
        return subprocess.run(cmd, text=True, capture_output=True)
    return subprocess.run(cmd, text=True)


def _write_pointer(output_root: Path, name: str, audit_id: str, run_dir: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    rel = run_dir.resolve().relative_to(output_root.resolve()).as_posix()
    (_root_metadata_path(output_root, name)).write_text(json.dumps({"audit_id": audit_id, "path": rel}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_output_root(project: Path, override: str | None = None, *, allow_deep: bool = False) -> tuple[Path, list[str]]:
    return _validate_output_root(project, override or _configured_output(project), allow_deep=allow_deep)


def _resolve_run(output_root: Path, *, prefer_latest: bool = False) -> Path:
    order = ("latest.json", "active.json") if prefer_latest else ("active.json", "latest.json")
    for name in order:
        run = _pointer_run(output_root, name)
        if run is not None and run.is_dir():
            return run
    raise SystemExit("ERROR: no RFF audit run found; initialize one with `rff audit init`")


def _parse_json_stdout(proc: subprocess.CompletedProcess[str]) -> dict | None:
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def _audit_alias(tokens: list[str]) -> tuple[str, list[str]]:
    if not tokens:
        return "help", []
    two = tuple(tokens[:2])
    aliases = {
        ("plan", "validate"): "validate-plan",
        ("attempt", "start"): "attempt-start",
        ("attempt", "finish"): "attempt-finish",
        ("attempt", "batch"): "attempt-batch",
        ("hypothesis", "open"): "hypothesis-open",
        ("hypothesis", "update"): "hypothesis-update",
        ("investigation", "status"): "investigation-status",
    }
    if two in aliases:
        return aliases[two], tokens[2:]
    return tokens[0], tokens[1:]


def cmd_audit(args: argparse.Namespace) -> int:
    tokens = list(args.audit_args or [])
    if getattr(args, "audit_help", False):
        tokens = ["help"]
    action, rest = _audit_alias(tokens)
    project = _discover_project_root()

    if action in {"help", "-h", "--help"}:
        print("RFF audit control plane\n\nCore commands:\n  rff audit init [options]\n  rff audit where [--format json|text]\n  rff audit status [--format json|text]\n  rff audit plan validate\n  rff audit attempt start ...\n  rff audit attempt finish ...\n  rff audit hypothesis open ...\n  rff audit report\n  rff audit gate\n  rff audit policy --fail-on FALSIFIED\n  rff audit present [--presentation chat|markdown|json]\n  rff audit compare --baseline ... --current ...\n  rff audit abandon --reason ...\n  rff audit export --export-format sarif\n\nCONTROL commands above manage canonical RFF state. Target commands such as curl/docker/browser/sql are not RFF control commands.")
        return 0

    # Parse wrapper-only output-root flags while leaving controller flags intact.
    wrapper = argparse.ArgumentParser(add_help=False)
    wrapper.add_argument("--output-dir")
    wrapper.add_argument("--allow-deep-output", action="store_true")
    known, remaining = wrapper.parse_known_args(rest)
    output_root, warnings = _resolve_output_root(project, known.output_dir, allow_deep=known.allow_deep_output)

    if action == "where":
        active = _pointer_run(output_root, "active.json")
        latest = _pointer_run(output_root, "latest.json")
        fmt = "json"
        if "--format" in remaining:
            try: fmt = remaining[remaining.index("--format") + 1]
            except Exception: pass
        payload = {"project_root": str(project), "output_root": str(output_root), "active_run": str(active) if active else None, "latest_run": str(latest) if latest else None, "warnings": warnings}
        if fmt == "text":
            print(str(active or latest or output_root))
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if action == "init":
        active = _pointer_run(output_root, "active.json")
        if active and active.exists():
            raise SystemExit(f"ERROR: active audit already exists at {active}; gate/abandon it before creating another run")
        audit_id = "rff-" + _uuid7()
        run_dir = output_root / "runs" / audit_id
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        forwarded = ["init", "--audit-dir", str(run_dir), "--audit-id", audit_id, "--target-root", str(project), *remaining]
        proc = _run_auditctl(forwarded, capture=True)
        if proc.returncode == 0:
            # Persist control-plane location metadata into the plan so the final
            # canonical result records where this run lived and whether a deep
            # path override was acknowledged. The audit-plan schema permits
            # target extension metadata.
            plan_path = run_dir / "audit-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan.setdefault("target", {})["rff_output_root"] = str(output_root.relative_to(project))
            plan["target"]["rff_run_id"] = audit_id
            plan["target"]["rff_path_warnings"] = warnings
            plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _write_pointer(output_root, "active.json", audit_id, run_dir)
        payload = _parse_json_stdout(proc)
        if payload is not None:
            payload.update({"project_root": str(project), "output_root": str(output_root), "run_dir": str(run_dir), "path_warnings": warnings})
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            sys.stdout.write(proc.stdout); sys.stderr.write(proc.stderr)
        return proc.returncode

    if action == "abandon":
        run_dir = _resolve_run(output_root)
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--reason", required=True)
        aa = ap.parse_args(remaining)
        marker = run_dir / ".abandoned.json"
        marker.write_text(json.dumps({"audit_id": run_dir.name, "abandoned_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "reason": aa.reason}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_root / "active.json").unlink(missing_ok=True)
        (run_dir / ".active.json").unlink(missing_ok=True)
        print(json.dumps({"ok": True, "abandoned": str(run_dir), "reason": aa.reason}, indent=2, sort_keys=True))
        return 0

    if action == "compare":
        # compare is intentionally run-independent; resolve run IDs under current root.
        cparser = argparse.ArgumentParser(add_help=False)
        cparser.add_argument("--baseline", required=True)
        cparser.add_argument("--current", required=True)
        cparser.add_argument("--format", default="json")
        cparser.add_argument("--history-dir")
        cargs = cparser.parse_args(remaining)
        def resolve_ref(ref: str) -> str:
            p = Path(ref)
            if p.exists(): return str(p)
            candidate = output_root / "runs" / ref
            return str(candidate if candidate.exists() else p)
        compare_args = ["compare", "--baseline", resolve_ref(cargs.baseline), "--current", resolve_ref(cargs.current), "--format", cargs.format]
        history_dir = cargs.history_dir or str(output_root / "runs")
        compare_args += ["--history-dir", history_dir]
        return _run_auditctl(compare_args).returncode

    prefer_latest = action in {"present", "export"}
    run_dir = _resolve_run(output_root, prefer_latest=prefer_latest)
    mapped = "summary" if action == "status" else action
    forwarded = [mapped, "--audit-dir", str(run_dir), *remaining]
    if action == "gate" and "--require-report" not in forwarded:
        forwarded.append("--require-report")
    proc = _run_auditctl(forwarded, capture=(action == "gate"))
    if action == "gate":
        payload = _parse_json_stdout(proc)
        if proc.returncode == 0:
            audit_id = (payload or {}).get("audit_id") or run_dir.name
            _write_pointer(output_root, "latest.json", audit_id, run_dir)
            (output_root / "active.json").unlink(missing_ok=True)
        if payload is not None:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            sys.stdout.write(proc.stdout); sys.stderr.write(proc.stderr)
    return proc.returncode


def _write_output_config(project: Path, value: str) -> None:
    """Update only [audit].output_dir while preserving unrelated TOML text."""
    path = project / RFF_CONFIG
    rendered = "output_dir = " + json.dumps(value)
    if not path.exists():
        path.write_text("[audit]\n" + rendered + "\n", encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    audit_start: int | None = None
    audit_end = len(lines)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[audit]":
            audit_start = idx
            continue
        if audit_start is not None and idx > audit_start and stripped.startswith("[") and stripped.endswith("]"):
            audit_end = idx
            break
    if audit_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[audit]", rendered]
    else:
        replaced = False
        for idx in range(audit_start + 1, audit_end):
            stripped = lines[idx].lstrip()
            if stripped.startswith("output_dir") and "=" in stripped:
                prefix = lines[idx][: len(lines[idx]) - len(stripped)]
                lines[idx] = prefix + rendered
                replaced = True
                break
        if not replaced:
            lines.insert(audit_start + 1, rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_config(args: argparse.Namespace) -> int:
    tokens = list(args.config_args or [])
    if getattr(args, "config_help", False):
        tokens = ["show"]
    project = _discover_project_root()
    if not tokens or tokens[0] in {"show", "-h", "--help"}:
        raw = _configured_output(project)
        output, warnings = _validate_output_root(project, raw, allow_deep=True)
        print(json.dumps({"project_root": str(project), "config_file": str(project / RFF_CONFIG), "audit": {"output_dir": raw, "resolved_output_dir": str(output)}, "warnings": warnings}, indent=2, sort_keys=True))
        return 0
    if tokens[0] == "set":
        if len(tokens) < 3 or tokens[1] != "audit.output_dir":
            raise SystemExit("Usage: rff config set audit.output_dir <project-relative-path> [--allow-deep-output]")
        value = tokens[2]
        allow_deep = "--allow-deep-output" in tokens[3:]
        resolved, warnings = _validate_output_root(project, value, allow_deep=allow_deep)
        # Do not redirect an existing active audit.
        old_root, _ = _resolve_output_root(project, allow_deep=True)
        active = _pointer_run(old_root, "active.json")
        if active and active.exists() and old_root.resolve() != resolved.resolve():
            raise SystemExit(f"ERROR RFF_OUTPUT_LOCATION_CHANGED: active audit is at {active}; finish/abandon it before changing audit.output_dir")
        _write_output_config(project, value)
        print(json.dumps({"ok": True, "output_dir": value, "resolved_output_dir": str(resolved), "warnings": warnings}, indent=2, sort_keys=True))
        return 0
    raise SystemExit("Usage: rff config show | rff config set audit.output_dir <path>")

def cmd_doctor(args: argparse.Namespace) -> int:
    project = resolve_project(args)
    ui = UI(_interactive(args))
    errors = validate_payload()
    if errors:
        print("Payload: FAIL")
        for error in errors:
            print(f"  missing: {error}")
        return 2
    auditctl = SOURCE_SKILL / "scripts/auditctl.py"
    proc = subprocess.run([sys.executable, str(auditctl), "--help"], text=True, capture_output=True)
    if proc.returncode != 0 or "attempt-start" not in proc.stdout or "gate" not in proc.stdout:
        print("Payload: FAIL — auditctl smoke check failed")
        return 2
    print(f"CLI:     PASS (rff {VERSION})")
    print("Payload: PASS")
    show_detection(project, ui)
    return 0


def cmd_self_upgrade(args: argparse.Namespace) -> int:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("ERROR: uv is required for rff self upgrade")

    source = args.source
    local = Path(source).expanduser()
    if local.exists():
        local = local.resolve()
        if local.is_dir():
            wheels = sorted((local / "dist").glob("runtime_feature_falsifier_cli-*-py3-none-any.whl"))
            if wheels:
                cmd = [uv, "tool", "install", str(wheels[-1]), "--force"]
            else:
                cmd = [uv, "tool", "install", "runtime-feature-falsifier-cli", "--force", "--from", str(local)]
        else:
            cmd = [uv, "tool", "install", str(local), "--force"]
    else:
        cmd = [uv, "tool", "install", "runtime-feature-falsifier-cli", "--force", "--from", source]

    print("Upgrade command:")
    print("  " + " ".join(cmd))
    if args.dry_run:
        return 0
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "init":
        return cmd_init(args)
    if args.command == "detect":
        return 0 if not show_detection(resolve_project(args), UI(_interactive(args))) is None else 0
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "audit":
        return cmd_audit(args)
    if args.command == "config":
        return cmd_config(args)
    if args.command == "integration":
        if args.integration_command == "list":
            return integration_list(resolve_project(args), UI(_interactive(args)))
        parser.parse_args(["integration", "--help"])
        return 0
    if args.command == "self":
        if args.self_command == "version":
            print(VERSION)
            return 0
        if args.self_command == "check":
            errors = validate_payload()
            print(json.dumps({"ok": not errors, "version": VERSION, "errors": errors}, indent=2))
            return 0 if not errors else 2
        if args.self_command == "upgrade":
            return cmd_self_upgrade(args)
        parser.parse_args(["self", "--help"])
        return 0
    raise SystemExit(f"ERROR: unknown command: {args.command}")
