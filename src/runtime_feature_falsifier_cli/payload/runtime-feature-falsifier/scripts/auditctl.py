#!/usr/bin/env python3
"""Deterministic control plane for Runtime Feature Falsifier audits.

Subcommands:
  init             Create an audit workspace and empty plan.
  validate-plan    Validate audit-plan.json and minimum falsification coverage.
  attempt-start    Append a STARTED event *before* a runtime probe.
  attempt-finish   Append a terminal event after the probe.
  hypothesis-open  Open a falsification hypothesis with a bounded investigation budget.
  hypothesis-update Append evidence/status to a hypothesis.
  investigation-status Summarize persistent investigation state.
  summary          Summarize attempts and derived feature verdicts.
  report           Generate feature-matrix.md and audit-report.md.
  gate             Refuse finalization when the audit is incomplete or inconsistent.

All machine-facing output is JSON unless --format text is explicitly selected.
No external Python packages are required; the JSON Schema engine is vendored with the skill.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import stat
from collections import Counter, defaultdict
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# Keep runtime audits self-contained: the release vendors fastjsonschema (BSD)
# under the skill root so auditctl works without pip/venv setup. Bundled schemas
# target JSON Schema Draft 7, which fastjsonschema implements.
_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))
try:
    import fastjsonschema
except ImportError as exc:  # pragma: no cover - release validator prevents this
    raise SystemExit(f"missing bundled JSON Schema engine: {exc}")

try:
    import fcntl  # Unix; used when available for append serialization.
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt  # Windows; used for cross-process file locking.
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

RESULTS = {"SURVIVED", "FALSIFIED", "BLOCKED", "INCONCLUSIVE"}
RELATIONS = {"VALID", "INVALID", "BOUNDARY", "CONTRACT_UNKNOWN", "ENVIRONMENT"}
PATTERNS = {
    "TODO", "PLACEHOLDER", "FAKE_NOOP", "BTTLP", "HARDCODED",
    "HARDCODED_SUSPECTED", "MOCK_ONLY", "PARTIAL_IMPLEMENTATION",
    "REAL_BUT_BROKEN", "AUDIT_ENVIRONMENT_INTERFERENCE", "NONE_OBSERVED", "UNKNOWN_PATTERN",
}
CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
SEVERITY = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
HYPOTHESIS_STATUSES = {"OPEN", "SUPPORTED", "REFUTED", "CONFIRMED", "BLOCKED", "BUDGET_EXHAUSTED"}
HYPOTHESIS_TERMINAL = {"REFUTED", "CONFIRMED", "BLOCKED", "BUDGET_EXHAUSTED"}
ESCALATION_STAGES = {str(i) for i in range(12)}
INTENTS = {
    "environment_start", "runtime_identity", "environment_collision", "baseline_valid", "valid_variation", "format_variation",
    "causal_sensitivity", "invalid_type_or_shape", "mime_extension_mismatch",
    "corrupt_input", "boundary_min", "boundary_max", "boundary_above_max",
    "state_transition", "round_trip", "persistence", "repeat_or_duplicate",
    "authorization", "dependency_authenticity", "workflow_completion",
    "hardcode_discrimination", "mock_discrimination", "bttlp_discrimination",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "assets"
_CURRENT_AUDIT_DIR: Path | None = None
MAX_JCS_SAFE_INTEGER = (1 << 53) - 1
RFF_VERSION = "2.9.5"
LEGACY_PRODUCER_VERSIONS = {"2.9.0", "2.9.1", "2.9.2", "2.9.3", "2.9.4"}
SUPPORTED_PRODUCER_VERSIONS = LEGACY_PRODUCER_VERSIONS | {RFF_VERSION}
SEAL_STATE_UNSEALED = "UNSEALED_VALID"
SEAL_STATE_SEALED = "SEALED_VALID"
SEAL_STATE_LEGACY = "LEGACY_SEAL_REQUIRES_MIGRATION"
SEAL_STATE_ERROR = "INTEGRITY_ERROR"
LEGACY_COMPLETION_MARKER_KEYS = {
    "audit_id", "audit_mode", "completed_at_utc", "sealed", "next_run_policy"
}
LEGACY_DIGEST_SEAL_KEYS = {
    "schema_version", "producer", "audit_id", "audit_mode", "state", "sealed",
    "canonicalization", "subjects", "completed_at_utc", "next_run_policy",
}
LEGACY_V293_SEAL_KEYS = {
    "schema_version", "producer", "audit_id", "audit_mode", "state", "sealed",
    "canonicalization", "subjects", "completed_at_utc", "sealed_at_utc",
    "next_run_policy",
}
LEGACY_V294_SEAL_KEYS = {
    "schema_version", "producer", "audit_id", "audit_mode", "state", "sealed",
    "canonicalization", "subjects", "completed_at_utc", "sealed_at_utc",
    "completion_time_provenance", "next_run_policy",
}
LEGACY_MIGRATION_MESSAGE = (
    f"legacy v2.9.x completion marker/seal detected; run `rff audit gate` with v{RFF_VERSION} "
    "to revalidate and migrate this audit to the current digest-bound seal format"
)
NEXT_RUN_POLICY = (
    "After remediation or a re-audit, use a fresh auditor instance and a new audit workspace; "
    "do not append to this sealed run."
)


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-I-JSON numeric constant is not permitted: {token}")


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object member name: {key!r}")
        obj[key] = value
    return obj


def _validate_unicode_scalars(value: Any, path: str = "$") -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError(f"invalid Unicode scalar value at {path}: {exc}") from exc
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode_scalars(key, f"{path}.<member-name>")
            _validate_unicode_scalars(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _validate_unicode_scalars(item, f"{path}[{idx}]")


def strict_json_loads(text: str, source: str = "<json>") -> Any:
    """Parse control-plane JSON with I-JSON-oriented fail-closed rules.

    Duplicate object names and the non-standard NaN/Infinity constants are
    rejected before schema validation. Unicode scalar validity is checked after
    parsing. Canonical result number-domain rules are enforced separately by the
    RFF JCS profile because ordinary audit inputs may legitimately contain finite
    floating-point values in non-canonical auxiliary fields.
    """
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid strict JSON in {source}: {exc}") from exc
    _validate_unicode_scalars(value)
    return value


def load_schema(name: str) -> dict[str, Any]:
    value = strict_json_loads((SCHEMA_DIR / name).read_text(encoding="utf-8"), f"schema {name}")
    if not isinstance(value, dict):
        raise SystemExit(f"invalid bundled schema {name}: root must be an object")
    return value


_SCHEMA_VALIDATOR_CACHE: dict[str, Any] = {}


def _local_schema_handler(uri: str) -> dict[str, Any]:
    """Resolve schema references strictly from the bundled assets directory.

    RFF never performs network schema resolution. Only the basename of an RFF
    schema URI may resolve, and it must exist under SCHEMA_DIR.
    """
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"} and parsed.netloc != "runtime-feature-falsifier.dev":
        raise ValueError(f"remote schema host is not permitted: {uri}")
    name = Path(parsed.path).name
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+\.schema\.json", name):
        raise ValueError(f"unsupported schema reference: {uri}")
    candidate = (SCHEMA_DIR / name).resolve()
    candidate.relative_to(SCHEMA_DIR.resolve())
    if not candidate.is_file():
        raise ValueError(f"unbundled schema reference: {uri}")
    value = strict_json_loads(candidate.read_text(encoding="utf-8"), f"schema {name}")
    if not isinstance(value, dict):
        raise ValueError(f"bundled schema root must be an object: {name}")
    return value


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate with the bundled JSON Schema Draft 7 engine.

    The exact same engine and schema files are used for runtime report/gate
    validation and release/self-test validation. Local references are resolved
    only from bundled assets; network retrieval is never permitted. Validation
    is observational: schema defaults are disabled so canonical artifacts cannot
    be mutated as a side effect of validation.
    """
    key = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        validator = _SCHEMA_VALIDATOR_CACHE.get(key)
        if validator is None:
            validator = fastjsonschema.compile(
                schema,
                handlers={"https": _local_schema_handler, "http": _local_schema_handler},
                use_default=False,
            )
            _SCHEMA_VALIDATOR_CACHE[key] = validator
        validator(instance)
        return []
    except fastjsonschema.JsonSchemaException as exc:
        # Preserve the controller's historical list[str] error contract while
        # surfacing the validator's precise data path and rule message.
        detail = getattr(exc, "message", str(exc))
        name = getattr(exc, "name", None)
        if name and name != "data":
            detail = f"{name}: {detail}"
        return [f"{path}: {detail}"]

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def _utc_timestamp_error(value: Any, label: str) -> str | None:
    """Require an RFC3339-style, offset-aware UTC timestamp for seal provenance."""
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        return f"{label} must be an RFC3339 UTC timestamp ending in Z or +00:00"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return f"{label} is not a valid calendar timestamp"
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        return f"{label} must use UTC offset +00:00/Z"
    return None


def _seal_metadata_errors(metadata: Any, label: str = "seal metadata") -> list[str]:
    """Validate timestamp semantics and provenance claims not expressible in Draft 7 alone."""
    if not isinstance(metadata, dict):
        return [f"{label} is missing or invalid"]
    errors: list[str] = []
    for field in ("completed_at_utc", "sealed_at_utc"):
        error = _utc_timestamp_error(metadata.get(field), f"{label}.{field}")
        if error:
            errors.append(error)
    provenance = metadata.get("completion_time_provenance")
    allowed = {
        "v2.9.5-gate",
        "v2.9.4-gate",
        "v2.9.4-seal-bound",
        "legacy-marker-unverified",
        "pre-v2.9.3-seal-unbound",
        "v2.9.3-seal-unbound",
    }
    if provenance not in allowed:
        errors.append(f"{label}.completion_time_provenance is invalid: {provenance}")
    migration = metadata.get("migration")
    if provenance == "v2.9.5-gate" and migration is not None:
        errors.append(f"{label}.migration must be absent for a native v2.9.5 seal")
    if provenance != "v2.9.5-gate" and not isinstance(migration, dict):
        errors.append(f"{label}.migration is required for inherited completion-time provenance")
    completed = metadata.get("completed_at_utc")
    sealed = metadata.get("sealed_at_utc")
    if not errors and isinstance(completed, str) and isinstance(sealed, str):
        cdt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        sdt = datetime.fromisoformat(sealed.replace("Z", "+00:00"))
        if sdt < cdt and provenance == "v2.9.5-gate":
            errors.append(f"{label}.sealed_at_utc precedes completed_at_utc")
    return errors


def _seal_metadata_from_seal(seal: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "completed_at_utc": seal.get("completed_at_utc"),
        "sealed_at_utc": seal.get("sealed_at_utc"),
        "completion_time_provenance": seal.get("completion_time_provenance"),
        "next_run_policy": seal.get("next_run_policy"),
    }
    if "migration" in seal:
        metadata["migration"] = seal.get("migration")
    return metadata


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def emit(obj: Any, fmt: str = "json") -> None:
    if isinstance(obj, dict) and _CURRENT_AUDIT_DIR is not None:
        obj = dict(obj)
        obj["chain_heads"] = chain_heads(audit_paths(_CURRENT_AUDIT_DIR))
    if fmt == "json":
        print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if isinstance(obj, dict) and "ok" in obj:
            print("PASS" if obj["ok"] else "FAIL")
            for msg in obj.get("errors", []):
                print(f"- {msg}")
            for msg in obj.get("warnings", []):
                print(f"warning: {msg}")
            if obj.get("chain_heads"):
                print("chain_heads: " + json.dumps(obj["chain_heads"], sort_keys=True))
        else:
            print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _jsonl_lock_path(path: Path) -> Path:
    return path.with_name("." + path.name + ".lock")


def _safe_chain_head(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        with exclusive_lock(_jsonl_lock_path(path)):
            events = _read_events_unlocked(path)
            return str(events[-1].get("event_sha256", "")) if events else ""
    except Exception:
        return "UNREADABLE"


def chain_heads(paths: dict[str, Path]) -> dict[str, str]:
    return {
        "attempts": _safe_chain_head(paths["log"]),
        "hypotheses": _safe_chain_head(paths["hypotheses"]),
    }


def with_heads(payload: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    out = dict(payload)
    out["chain_heads"] = chain_heads(paths)
    return out


@contextmanager
def exclusive_lock(lock_path: Path):
    """Cross-process lock for JSONL writers on POSIX and Windows."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            if os.path.getsize(lock_path) == 0:
                os.write(fd, b"0")
                os.fsync(fd)
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        elif msvcrt is not None:  # pragma: no cover - Windows
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        os.close(fd)


def audit_paths(audit_dir: Path) -> dict[str, Path]:
    return {
        "dir": audit_dir,
        "plan": audit_dir / "audit-plan.json",
        "log": audit_dir / "attempts.jsonl",
        "hypotheses": audit_dir / "hypothesis-ledger.jsonl",
        "evidence": audit_dir / "evidence",
        "matrix": audit_dir / "feature-matrix.md",
        "report": audit_dir / "audit-report.md",
        "baseline": audit_dir / "tracked-source-baseline.json",
        "genesis": audit_dir / "audit-genesis.json",
        "inventory": audit_dir / "feature-inventory.json",
        "coverage": audit_dir / "system-coverage.md",
        "active": audit_dir / ".active.json",
        "complete": audit_dir / ".complete.json",
        "report_state": audit_dir / ".report-state.json",
        "result": audit_dir / "audit-result.json",
        "findings": audit_dir / "findings.json",
        "summary_md": audit_dir / "audit-summary.md",
        "manifest": audit_dir / "result-manifest.json",
        "log_lock": audit_dir / ".attempts.jsonl.lock",
        "hypothesis_lock": audit_dir / ".hypothesis-ledger.jsonl.lock",
        "control_lock": audit_dir / ".audit-control.lock",
    }


def _workspace_state_errors(paths: dict[str, Path], *, require_active: bool) -> list[str]:
    """Enforce the controller lifecycle boundary before any mutating command."""
    errors: list[str] = []
    if paths["complete"].exists():
        errors.append(
            f"audit workspace is sealed: {paths['complete']}; sealed runs are immutable. "
            "Create a fresh audit workspace for any re-audit or remediation run."
        )
        return errors
    if require_active:
        if not paths["active"].exists():
            errors.append("audit workspace is not ACTIVE; initialize a fresh audit run before mutating it")
            return errors
        try:
            active = load_json(paths["active"])
            plan = load_json(paths["plan"])
        except SystemExit as exc:
            return [str(exc)]
        if not isinstance(active, dict):
            errors.append(".active.json must be a JSON object")
        elif active.get("audit_id") != plan.get("audit_id"):
            errors.append("active audit_id does not match audit-plan.json")
        if isinstance(active, dict) and active.get("audit_mode") != plan.get("audit_mode"):
            errors.append("active audit_mode does not match audit-plan.json")
        if not errors:
            errors.extend(_genesis_integrity_errors(paths, plan))
            if isinstance(active, dict) and paths["genesis"].exists():
                try:
                    genesis = load_json(paths["genesis"])
                    if active.get("genesis_jcs_sha256") != _genesis_digest(genesis):
                        errors.append("active marker genesis digest does not match audit-genesis.json")
                except SystemExit as exc:
                    errors.append(str(exc))
    return errors


def workspace_locked_command(func):
    """Serialize one control-plane command across the entire audit workspace."""
    @wraps(func)
    def wrapper(args: argparse.Namespace) -> int:
        paths = audit_paths(args.audit_dir)
        with exclusive_lock(paths["control_lock"]):
            return func(args)
    return wrapper


def active_mutation_command(func):
    """Serialize a mutating command and reject mutation outside ACTIVE state."""
    @wraps(func)
    def wrapper(args: argparse.Namespace) -> int:
        paths = audit_paths(args.audit_dir)
        with exclusive_lock(paths["control_lock"]):
            errors = _workspace_state_errors(paths, require_active=True)
            if errors:
                emit({"ok": False, "errors": errors}, getattr(args, "format", "json"))
                return 2
            return func(args)
    return wrapper


def _baseline_digest(baseline: dict[str, Any]) -> str:
    return canonical_sha256(baseline)


def _genesis_digest(genesis: dict[str, Any]) -> str:
    return canonical_sha256(genesis)


def _build_audit_genesis(
    plan: dict[str, Any], baseline_root: Path, baseline: dict[str, Any], *, provenance: str
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "audit_id": plan.get("audit_id"),
        "audit_mode": plan.get("audit_mode", "FEATURE"),
        "created_at_utc": plan.get("created_at_utc"),
        "target_root": str(baseline_root.resolve()),
        "tracked_source_snapshot_format": baseline.get("snapshot_format", "legacy-content-only"),
        "tracked_source_baseline_jcs_sha256": _baseline_digest(baseline),
        "baseline_provenance": provenance,
    }


def _genesis_integrity_errors(paths: dict[str, Path], plan: dict[str, Any] | None = None) -> list[str]:
    """Bind the mutable baseline file to an init/migration-time genesis record."""
    if not paths["genesis"].exists():
        return ["audit genesis record missing; tracked-source baseline is not authoritatively bound"]
    if not paths["baseline"].exists():
        return ["tracked-source baseline missing"]
    try:
        genesis = load_json(paths["genesis"])
        baseline = load_json(paths["baseline"])
        if plan is None:
            plan = load_json(paths["plan"])
    except SystemExit as exc:
        return [str(exc)]
    errors = validate_schema(genesis, load_schema("audit-genesis.schema.json"), "audit-genesis")
    if not isinstance(genesis, dict) or not isinstance(baseline, dict) or not isinstance(plan, dict):
        return errors + ["audit genesis, baseline, and plan roots must be JSON objects"]
    if genesis.get("audit_id") != plan.get("audit_id"):
        errors.append("audit genesis audit_id does not match audit-plan.json")
    if genesis.get("audit_mode") != plan.get("audit_mode", "FEATURE"):
        errors.append("audit genesis audit_mode does not match audit-plan.json")
    actual_baseline = _baseline_digest(baseline)
    if genesis.get("tracked_source_baseline_jcs_sha256") != actual_baseline:
        errors.append("tracked-source baseline digest does not match immutable audit genesis")
    if genesis.get("tracked_source_snapshot_format") != baseline.get("snapshot_format", "legacy-content-only"):
        errors.append("tracked-source snapshot format does not match audit genesis")
    return errors


def _ensure_legacy_genesis(paths: dict[str, Path], plan: dict[str, Any]) -> None:
    """Create a v2.9.5 genesis only during a successful legacy migration."""
    if paths["genesis"].exists():
        return
    baseline = load_json(paths["baseline"])
    if not isinstance(baseline, dict):
        raise SystemExit("legacy tracked-source baseline must be a JSON object")
    active = load_json(paths["active"]) if paths["active"].exists() else {}
    target_root = Path(active.get("target_root") or baseline.get("git_root") or paths["dir"].parent)
    genesis = _build_audit_genesis(
        plan, target_root, baseline, provenance="legacy-baseline-unbound-at-migration"
    )
    schema_errors = validate_schema(genesis, load_schema("audit-genesis.schema.json"), "audit-genesis")
    if schema_errors:
        raise SystemExit("invalid generated audit genesis: " + "; ".join(schema_errors))
    _atomic_write_text(paths["genesis"], json.dumps(genesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _fsync_directory(paths["dir"])


def load_json(path: Path) -> Any:
    try:
        return strict_json_loads(path.read_text(encoding="utf-8"), str(path))
    except FileNotFoundError:
        raise SystemExit(f"missing required file: {path}")
    except (UnicodeDecodeError, ValueError) as exc:
        raise SystemExit(str(exc))


def _file_sha256_unlocked(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sha256(path: Path) -> str | None:
    """Hash a file; canonical JSONL reads share the writer lock.

    This prevents report/gate freshness checks from hashing a file while another
    process is midway through an append.
    """
    if path.suffix == ".jsonl":
        with exclusive_lock(_jsonl_lock_path(path)):
            return _file_sha256_unlocked(path)
    return _file_sha256_unlocked(path)


def report_state_snapshot(paths: dict[str, Path], plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": plan.get("audit_id"),
        "plan_sha256": file_sha256(paths["plan"]),
        "attempts_sha256": file_sha256(paths["log"]),
        "hypotheses_sha256": file_sha256(paths["hypotheses"]),
        "inventory_sha256": file_sha256(paths["inventory"]) if plan.get("audit_mode") == "SYSTEM" else None,
    }


def find_feature_probe(plan: dict[str, Any], feature_id: str, probe_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for feature in plan.get("features", []):
        if feature.get("feature_id") == feature_id:
            for probe in feature.get("probes", []):
                if probe.get("probe_id") == probe_id:
                    return feature, probe
            raise SystemExit(f"probe not found in feature {feature_id}: {probe_id}")
    raise SystemExit(f"feature not found in plan: {feature_id}")


def validate_plan_obj(plan: Any) -> dict[str, Any]:
    errors: list[str] = validate_schema(plan, load_schema("audit-plan.schema.json"))
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["audit plan must be a JSON object"], "warnings": []}
    if plan.get("schema_version") != "2.0":
        errors.append('schema_version must be "2.0"')
    audit_mode = plan.get("audit_mode", "FEATURE")
    if audit_mode not in {"FEATURE", "SYSTEM"}:
        errors.append("audit_mode must be FEATURE or SYSTEM")
    for key in ("audit_id", "audit_mode", "target", "features"):
        if key not in plan:
            errors.append(f"missing top-level field: {key}")
    if not isinstance(plan.get("features"), list) or not plan.get("features"):
        errors.append("features must be a non-empty array")
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    feature_ids: set[str] = set()
    probe_ids: set[str] = set()
    for idx, feature in enumerate(plan["features"], 1):
        prefix = f"feature[{idx}]"
        fid = feature.get("feature_id")
        if not isinstance(fid, str) or not ID_RE.match(fid):
            errors.append(f"{prefix}.feature_id must be a stable lowercase-ish id")
            continue
        if fid in feature_ids:
            errors.append(f"duplicate feature_id: {fid}")
        feature_ids.add(fid)
        for key in ("claim", "claim_source", "entry_points", "expected_end_effects"):
            if not feature.get(key):
                errors.append(f"{fid}: missing/non-empty {key}")
        for flag in ("input_sensitive", "stateful", "dependency_sensitive"):
            if not isinstance(feature.get(flag), bool):
                errors.append(f"{fid}: {flag} must be boolean")
        probes = feature.get("probes")
        if not isinstance(probes, list) or not probes:
            errors.append(f"{fid}: probes must be a non-empty array")
            continue
        intents: list[str] = []
        for pidx, probe in enumerate(probes, 1):
            pfx = f"{fid}.probe[{pidx}]"
            pid = probe.get("probe_id")
            if not isinstance(pid, str) or not ID_RE.match(pid):
                errors.append(f"{pfx}.probe_id must be a stable id")
            elif pid in probe_ids:
                errors.append(f"duplicate probe_id across audit: {pid}")
            else:
                probe_ids.add(pid)
            intent = probe.get("probe_intent")
            intents.append(intent)
            if not isinstance(intent, str) or not intent:
                errors.append(f"{pfx}: missing probe_intent")
            elif intent not in INTENTS:
                warnings.append(f"{pfx}: custom probe_intent {intent!r}; ensure it is specific")
            relation = probe.get("contract_relation")
            if relation not in RELATIONS:
                errors.append(f"{pfx}: contract_relation must be one of {sorted(RELATIONS)}")
            if not probe.get("expected"):
                errors.append(f"{pfx}: expected is required")
            if "required" not in probe or not isinstance(probe.get("required"), bool):
                errors.append(f"{pfx}: required must be boolean")
            if not isinstance(probe.get("effect_checks", []), list):
                errors.append(f"{pfx}: effect_checks must be an array")

        # Coverage gates. They are conditional on feature shape rather than blindly universal.
        required_intents = [p.get("probe_intent") for p in probes if p.get("required", True)]
        if "baseline_valid" not in required_intents:
            errors.append(f"{fid}: required probes must include baseline_valid")
        if feature.get("input_sensitive"):
            if not any(i in required_intents for i in ("valid_variation", "format_variation")):
                errors.append(f"{fid}: input_sensitive feature needs valid_variation or format_variation")
            if "causal_sensitivity" not in required_intents:
                errors.append(f"{fid}: input_sensitive feature needs causal_sensitivity")
            if not any(i in required_intents for i in ("invalid_type_or_shape", "corrupt_input", "boundary_min", "boundary_max")):
                errors.append(f"{fid}: input_sensitive feature needs at least one negative/boundary probe")
        if feature.get("stateful"):
            if not any(i in required_intents for i in ("state_transition", "round_trip", "persistence", "workflow_completion")):
                errors.append(f"{fid}: stateful feature needs a state/end-effect probe")
        if feature.get("dependency_sensitive") and "dependency_authenticity" not in required_intents:
            errors.append(f"{fid}: dependency_sensitive feature needs dependency_authenticity")

    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    if not str(target.get("startup_path", "")).strip():
        errors.append("target.startup_path is required; document the real supported startup path")
    if not str(target.get("runtime_identity_expectation", "")).strip():
        errors.append("target.runtime_identity_expectation is required; state what process/server/build must actually be running")

    required_preflight = {
        "environment_start": "establish startup health",
        "runtime_identity": "prove what runtime/process is actually serving the target",
        "environment_collision": "check audit/test/live shared-resource interference",
    }
    for preflight_intent, purpose in required_preflight.items():
        probes_for_intent = [
            p for feature in plan.get("features", []) for p in feature.get("probes", [])
            if isinstance(p, dict) and p.get("required", True) and p.get("probe_intent") == preflight_intent
        ]
        if not probes_for_intent:
            errors.append(f"audit requires one required {preflight_intent} probe to {purpose}")
            continue
        if not any(p.get("contract_relation") == "ENVIRONMENT" for p in probes_for_intent):
            errors.append(f"{preflight_intent} probe must use contract_relation=ENVIRONMENT")
        if preflight_intent in {"runtime_identity", "environment_collision"} and not any(p.get("effect_checks") for p in probes_for_intent):
            errors.append(f"{preflight_intent} probe requires at least one effect_check")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "feature_count": len(feature_ids), "probe_count": len(probe_ids)}


def validate_inventory_obj(inventory: Any, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    errors: list[str] = validate_schema(inventory, load_schema("feature-inventory.schema.json"))
    warnings: list[str] = []
    if not isinstance(inventory, dict):
        return {"ok": False, "errors": ["feature inventory must be a JSON object"], "warnings": []}
    if inventory.get("schema_version") != "1.0":
        errors.append('feature inventory schema_version must be "1.0"')
    if not inventory.get("audit_id"):
        errors.append("feature inventory missing audit_id")
    status = inventory.get("discovery_status")
    if status == "IN_PROGRESS":
        errors.append("feature inventory discovery is still IN_PROGRESS; finish discovery before validation")
    elif status not in {"COMPLETE", "PARTIAL_BLOCKED"}:
        errors.append("feature inventory discovery_status must be COMPLETE or PARTIAL_BLOCKED")
    blockers = inventory.get("discovery_blockers", [])
    if status == "PARTIAL_BLOCKED" and not blockers:
        errors.append("PARTIAL_BLOCKED discovery requires at least one discovery_blocker")
    if status == "COMPLETE" and blockers:
        errors.append("COMPLETE discovery cannot retain discovery_blockers; use PARTIAL_BLOCKED")
    sources = inventory.get("discovery_sources")
    if not isinstance(sources, list) or not sources:
        errors.append("feature inventory requires at least one discovery source")
        sources = []
    kinds = {str(x.get("kind", "")).strip() for x in sources if isinstance(x, dict)}
    if len(kinds) < 2:
        if status == "COMPLETE":
            errors.append("COMPLETE SYSTEM discovery requires at least two materially independent discovery-source kinds")
        else:
            warnings.append("SYSTEM discovery used fewer than two source kinds; limitation must remain explicit")
    workflow = inventory.get("workflow_coverage")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("applicable"), bool):
        errors.append("workflow_coverage.applicable must be boolean")
        workflow = {"applicable": False}
    elif workflow.get("applicable") is False and not str(workflow.get("reason", "")).strip():
        errors.append("workflow_coverage requires a reason when applicable=false")
    features = inventory.get("features")
    if not isinstance(features, list) or not features:
        errors.append("feature inventory features must be a non-empty array")
        features = []
    seen: set[str] = set()
    in_scope: set[str] = set()
    workflow_ids: set[str] = set()
    excluded: set[str] = set()
    for idx, item in enumerate(features, 1):
        pfx = f"inventory.feature[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{pfx} must be an object")
            continue
        fid = item.get("feature_id")
        if not isinstance(fid, str) or not ID_RE.match(fid):
            errors.append(f"{pfx}.feature_id must be a stable lowercase-ish id")
            continue
        if fid in seen:
            errors.append(f"duplicate inventory feature_id: {fid}")
        seen.add(fid)
        if item.get("kind") not in {"CAPABILITY", "FUNCTION", "WORKFLOW", "SYSTEM"}:
            errors.append(f"{fid}: kind must be CAPABILITY, FUNCTION, WORKFLOW, or SYSTEM")
        if not item.get("claim"):
            errors.append(f"{fid}: inventory claim is required")
        if not isinstance(item.get("claim_sources"), list) or not item.get("claim_sources"):
            errors.append(f"{fid}: claim_sources must be a non-empty array")
        if not isinstance(item.get("surfaces"), list) or not item.get("surfaces"):
            errors.append(f"{fid}: surfaces must be a non-empty array")
        disposition = item.get("disposition")
        if disposition == "IN_SCOPE":
            in_scope.add(fid)
            if item.get("kind") == "WORKFLOW":
                workflow_ids.add(fid)
        elif disposition == "EXCLUDED":
            excluded.add(fid)
            basis = item.get("exclusion_basis")
            if basis not in {"USER_SCOPE", "NOT_RUNTIME_CAPABILITY", "DUPLICATE_ALIAS", "OUTSIDE_TARGET_SYSTEM"}:
                errors.append(f"{fid}: EXCLUDED item requires exclusion_basis USER_SCOPE, NOT_RUNTIME_CAPABILITY, DUPLICATE_ALIAS, or OUTSIDE_TARGET_SYSTEM")
            if not str(item.get("exclusion_reason", "")).strip():
                errors.append(f"{fid}: EXCLUDED inventory item requires exclusion_reason")
        else:
            errors.append(f"{fid}: disposition must be IN_SCOPE or EXCLUDED")
    if workflow.get("applicable") is True and not workflow_ids:
        errors.append("workflow_coverage.applicable=true requires at least one IN_SCOPE WORKFLOW inventory item")
    unmapped: list[str] = []
    extra_planned: list[str] = []
    if plan is not None:
        if inventory.get("audit_id") and plan.get("audit_id") and inventory.get("audit_id") != plan.get("audit_id"):
            errors.append("feature inventory audit_id does not match audit plan")
        plan_ids = {f.get("feature_id") for f in plan.get("features", []) if isinstance(f, dict) and f.get("feature_id")}
        unmapped = sorted(in_scope - plan_ids)
        extra_planned = sorted(plan_ids - in_scope)
        if unmapped:
            errors.append("SYSTEM inventory has IN_SCOPE features missing from audit plan: " + ", ".join(unmapped))
        if extra_planned:
            errors.append("SYSTEM audit plan contains features not marked IN_SCOPE in inventory: " + ", ".join(extra_planned))
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "discovered_count": len(seen),
        "in_scope_count": len(in_scope),
        "excluded_count": len(excluded),
        "workflow_count": len(workflow_ids),
        "unmapped": unmapped,
        "extra_planned": extra_planned,
        "discovery_status": status,
        "discovery_blockers": blockers,
    }


def _read_events_unlocked(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                event = strict_json_loads(line, f"{log_path}:{lineno}")
            except ValueError as exc:
                raise SystemExit(f"invalid JSONL at {log_path}:{lineno}: {exc}")
            if not isinstance(event, dict):
                raise SystemExit(f"invalid JSONL at {log_path}:{lineno}: event root must be an object")
            event["_line"] = lineno
            events.append(event)
    return events


def read_events(log_path: Path) -> list[dict[str, Any]]:
    """Read a canonical JSONL snapshot while holding the same lock as writers."""
    with exclusive_lock(_jsonl_lock_path(log_path)):
        return _read_events_unlocked(log_path)


def verify_hash_chain(events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    previous = ""
    expected_seq = 1
    schema = None
    for event in events:
        line = event.get("_line", "?")
        if event.get("sequence") != expected_seq:
            errors.append(f"attempts.jsonl line {line}: sequence {event.get('sequence')} != expected {expected_seq}")
        if event.get("prev_event_sha256", "") != previous:
            errors.append(f"attempts.jsonl line {line}: prev_event_sha256 chain mismatch")
        stated = event.get("event_sha256")
        material = {k: v for k, v in event.items() if k not in {"event_sha256", "_line"}}
        actual = hashlib.sha256(jdump(material).encode("utf-8")).hexdigest()
        if stated != actual:
            errors.append(f"attempts.jsonl line {line}: event_sha256 mismatch")
        previous = stated or ""
        expected_seq += 1
    return errors


def _event_schema_for(log_path: Path) -> dict[str, Any] | None:
    if log_path.name == "attempts.jsonl":
        return load_schema("attempt-event.schema.json")
    if log_path.name == "hypothesis-ledger.jsonl":
        return load_schema("hypothesis-event.schema.json")
    return None


def _ledger_integrity_errors(log_path: Path, events: list[dict[str, Any]]) -> list[str]:
    errors = verify_hash_chain(events) if events else []
    schema = _event_schema_for(log_path)
    if schema is not None:
        for event in events:
            errors.extend(validate_schema(
                {k: v for k, v in event.items() if k != "_line"},
                schema,
                f"{log_path.name}:{event.get('_line', '?')}",
            ))
    return errors


def append_events(log_path: Path, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atomically serialize one or more hash-chained events under a cross-platform lock."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _jsonl_lock_path(log_path)
    completed: list[dict[str, Any]] = []
    with exclusive_lock(lock_path):
        existing = _read_events_unlocked(log_path)
        existing_errors = _ledger_integrity_errors(log_path, existing)
        if existing_errors:
            raise SystemExit(
                f"refusing to append to corrupt {log_path.name}: " + "; ".join(existing_errors)
            )
        prev_hash = existing[-1].get("event_sha256", "") if existing else ""
        sequence = len(existing) + 1
        schema = _event_schema_for(log_path)
        for raw_payload in payloads:
            payload = dict(raw_payload)
            payload["sequence"] = sequence
            payload["prev_event_sha256"] = prev_hash
            material = dict(payload)
            payload["event_sha256"] = hashlib.sha256(jdump(material).encode("utf-8")).hexdigest()
            if schema is not None:
                schema_errors = validate_schema(payload, schema)
                if schema_errors:
                    raise SystemExit("event failed bundled schema validation: " + "; ".join(schema_errors))
            completed.append(payload)
            prev_hash = payload["event_sha256"]
            sequence += 1
        if completed:
            with log_path.open("a", encoding="utf-8", newline="") as fh:
                for payload in completed:
                    fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    return completed


def append_event(log_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return append_events(log_path, [payload])[0]


def fold_attempts(events: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    attempts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for event in events:
        aid = event.get("attempt_id")
        et = event.get("event_type")
        if not aid:
            errors.append(f"line {event.get('_line')}: missing attempt_id")
            continue
        if et == "STARTED":
            if aid in attempts:
                errors.append(f"duplicate STARTED event for {aid}")
            attempts[aid] = {"start": event, "finish": None}
        elif et == "FINISHED":
            if aid not in attempts:
                errors.append(f"FINISHED without STARTED for {aid}")
                attempts[aid] = {"start": None, "finish": event}
            elif attempts[aid]["finish"] is not None:
                errors.append(f"duplicate FINISHED event for {aid}")
            else:
                attempts[aid]["finish"] = event
        else:
            errors.append(f"line {event.get('_line')}: event_type must be STARTED or FINISHED")
    return attempts, errors


def fold_hypotheses(events: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    hypotheses: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for event in events:
        hid = event.get("hypothesis_id")
        et = event.get("event_type")
        if not hid:
            errors.append(f"hypothesis line {event.get('_line')}: missing hypothesis_id")
            continue
        if et == "HYPOTHESIS_OPENED":
            if hid in hypotheses:
                errors.append(f"duplicate HYPOTHESIS_OPENED for {hid}")
                continue
            hypotheses[hid] = {
                "hypothesis_id": hid,
                "feature_id": event.get("feature_id"),
                "statement": event.get("statement"),
                "status": "OPEN",
                "max_attempts": int(event.get("max_attempts", 6)),
                "trigger_attempt_id": event.get("trigger_attempt_id"),
                "next_probe": event.get("next_probe", ""),
                "evidence_for": list(event.get("evidence_for") or []),
                "evidence_against": list(event.get("evidence_against") or []),
                "updates": 0,
            }
        elif et == "HYPOTHESIS_UPDATED":
            if hid not in hypotheses:
                errors.append(f"HYPOTHESIS_UPDATED without open for {hid}")
                continue
            h = hypotheses[hid]
            status = event.get("status")
            if status not in HYPOTHESIS_STATUSES:
                errors.append(f"invalid hypothesis status for {hid}: {status}")
                continue
            h["status"] = status
            h["next_probe"] = event.get("next_probe", h.get("next_probe", ""))
            h["evidence_for"].extend(event.get("evidence_for") or [])
            h["evidence_against"].extend(event.get("evidence_against") or [])
            h["updates"] += 1
            h["last_changed_variable"] = event.get("changed_variable", "")
            h["notes"] = event.get("notes", "")
        else:
            errors.append(f"hypothesis line {event.get('_line')}: invalid event_type {et!r}")
    return hypotheses, errors


def hypothesis_attempt_counts(attempt_events: list[dict[str, Any]]) -> Counter:
    counts: Counter = Counter()
    for event in attempt_events:
        if event.get("event_type") == "STARTED" and event.get("hypothesis_id"):
            counts[event["hypothesis_id"]] += 1
    return counts


def investigation_summary(paths: dict[str, Path]) -> dict[str, Any]:
    events = read_events(paths["hypotheses"])
    errors = verify_hash_chain(events) if events else []
    hypotheses, fold_errors = fold_hypotheses(events)
    errors.extend(fold_errors)
    attempt_events = read_events(paths["log"])
    counts = hypothesis_attempt_counts(attempt_events)
    rows = []
    for hid, h in sorted(hypotheses.items()):
        row = dict(h)
        row["attempts_used"] = counts.get(hid, 0)
        row["attempts_remaining"] = max(0, h.get("max_attempts", 0) - counts.get(hid, 0))
        rows.append(row)
    active = [r for r in rows if r.get("status") not in HYPOTHESIS_TERMINAL]
    return {
        "ok": not errors,
        "errors": errors,
        "hypothesis_count": len(rows),
        "active_count": len(active),
        "active_hypotheses": [r["hypothesis_id"] for r in active],
        "hypotheses": rows,
    }


def apply_investigation_verdicts(summary: dict[str, Any], investigation: dict[str, Any]) -> dict[str, Any]:
    """Project terminal investigation uncertainty into feature verdicts.

    A confirmed hypothesis must already be backed by a FALSIFIED attempt (gate rule),
    so FALSIFIED remains dominant. BLOCKED/BUDGET_EXHAUSTED hypotheses prevent a
    surviving matrix from being reported as NOT_FALSIFIED.
    """
    by_feature: dict[str, set[str]] = defaultdict(set)
    for h in investigation.get("hypotheses", []):
        if h.get("feature_id") and h.get("status"):
            by_feature[h["feature_id"]].add(h["status"])
    for feature in summary.get("features", []):
        if feature.get("verdict") == "FALSIFIED":
            continue
        statuses = by_feature.get(feature.get("feature_id", ""), set())
        if "BUDGET_EXHAUSTED" in statuses:
            feature["verdict"] = "INCONCLUSIVE"
        elif "BLOCKED" in statuses:
            feature["verdict"] = "BLOCKED"
    return summary


def derive_summary(plan: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    attempts, fold_errors = fold_attempts(events)
    by_probe: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in attempts.values():
        if pair.get("finish"):
            by_probe[pair["finish"].get("probe_id", "")].append(pair["finish"])
    features_out: list[dict[str, Any]] = []
    for feature in plan.get("features", []):
        terminals: list[dict[str, Any]] = []
        required_missing: list[str] = []
        required_blocked: list[str] = []
        required_inconclusive: list[str] = []
        for probe in feature.get("probes", []):
            pid = probe.get("probe_id", "")
            rows = by_probe.get(pid, [])
            terminals.extend(rows)
            if probe.get("required", True):
                if not rows:
                    required_missing.append(pid or "<missing>")
                elif not any(r.get("result") in {"SURVIVED", "FALSIFIED"} for r in rows):
                    if any(r.get("result") == "INCONCLUSIVE" for r in rows):
                        required_inconclusive.append(pid)
                    else:
                        required_blocked.append(pid)
        if any(r.get("result") == "FALSIFIED" for r in terminals):
            verdict = "FALSIFIED"
        elif required_missing:
            verdict = "INCOMPLETE"
        elif required_inconclusive:
            verdict = "INCONCLUSIVE"
        elif required_blocked:
            verdict = "BLOCKED"
        elif terminals:
            verdict = "NOT_FALSIFIED"
        else:
            verdict = "INCOMPLETE"
        patterns = sorted({r.get("failure_pattern") for r in terminals if r.get("failure_pattern") not in (None, "NONE_OBSERVED")})
        features_out.append({
            "feature_id": feature.get("feature_id"),
            "claim": feature.get("claim"),
            "verdict": verdict,
            "terminal_attempts": len(terminals),
            "required_missing": required_missing,
            "required_blocked": required_blocked,
            "required_inconclusive": required_inconclusive,
            "patterns": patterns,
            "results": dict(Counter(r.get("result") for r in terminals)),
        })
    return {
        "audit_id": plan.get("audit_id"),
        "event_count": len(events),
        "attempt_count": len(attempts),
        "open_attempts": sorted(aid for aid, pair in attempts.items() if pair.get("start") and not pair.get("finish")),
        "fold_errors": fold_errors,
        "features": features_out,
    }



def tracked_source_snapshot(root: Path) -> dict[str, Any]:
    """Capture Git-tracked worktree content plus Git-significant type/mode state.

    The old content-only baseline missed chmod +x, symlink/type changes, and gitlinks.
    v2.9.5 records both index identity and the live worktree representation.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return {
            "available": False,
            "snapshot_format": "rff-git-worktree-v2",
            "reason": "target is not a readable Git worktree",
        }
    git_root = Path(top)
    try:
        head = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        raw = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--stage", "-z"],
            check=True, capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        return {
            "available": False,
            "snapshot_format": "rff-git-worktree-v2",
            "reason": f"git snapshot failed: {exc}",
        }

    files: dict[str, dict[str, Any]] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            meta, rel_raw = item.split(b"\t", 1)
            index_mode, index_oid, stage = meta.decode("ascii").split(" ", 2)
            rel = rel_raw.decode("utf-8", errors="surrogateescape")
        except ValueError:
            continue
        path = git_root / rel
        entry: dict[str, Any] = {
            "index_mode": index_mode,
            "index_oid": index_oid,
            "stage": stage,
        }
        try:
            st = path.lstat()
        except FileNotFoundError:
            entry.update({"worktree_type": "MISSING", "worktree_mode": "MISSING", "content_sha256": None})
            files[rel] = entry
            continue

        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(path)
            entry.update({
                "worktree_type": "SYMLINK",
                "worktree_mode": "120000",
                "content_sha256": hashlib.sha256(target.encode("utf-8", errors="surrogateescape")).hexdigest(),
                "symlink_target": target,
            })
        elif stat.S_ISREG(st.st_mode):
            worktree_mode = "100755" if (st.st_mode & stat.S_IXUSR) else "100644"
            entry.update({
                "worktree_type": "FILE",
                "worktree_mode": worktree_mode,
                "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        elif stat.S_ISDIR(st.st_mode) and index_mode == "160000":
            try:
                submodule_head = subprocess.run(
                    ["git", "-C", str(path), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
            except (FileNotFoundError, subprocess.CalledProcessError):
                submodule_head = None
            entry.update({
                "worktree_type": "GITLINK",
                "worktree_mode": "160000",
                "content_sha256": None,
                "submodule_head": submodule_head,
            })
        else:
            entry.update({
                "worktree_type": "OTHER",
                "worktree_mode": oct(stat.S_IFMT(st.st_mode)),
                "content_sha256": None,
            })
        files[rel] = entry

    return {
        "available": True,
        "snapshot_format": "rff-git-worktree-v2",
        "git_root": str(git_root),
        "head": head,
        "tracked_file_count": len(files),
        "files": files,
    }


def _legacy_tracked_source_snapshot(root: Path) -> dict[str, Any]:
    """Reproduce the v2.9.0-v2.9.4 content-only snapshot for migration checks."""
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        git_root = Path(top)
        head = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        raw = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "-z"],
            check=True, capture_output=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return {"available": False, "reason": f"legacy git snapshot failed: {exc}"}
    files: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8", errors="surrogateescape")
        path = git_root / rel
        if not path.is_file():
            files[rel] = "<MISSING>"
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "available": True,
        "git_root": str(git_root),
        "head": head,
        "tracked_file_count": len(files),
        "files": files,
    }


def compare_tracked_source(baseline: dict[str, Any]) -> list[str]:
    if not baseline.get("available"):
        return []
    root = Path(baseline["git_root"])
    current = (
        tracked_source_snapshot(root)
        if baseline.get("snapshot_format") == "rff-git-worktree-v2"
        else _legacy_tracked_source_snapshot(root)
    )
    if not current.get("available"):
        return ["could not re-read Git tracked-source snapshot during final gate"]
    errors: list[str] = []
    before = baseline.get("files", {})
    after = current.get("files", {})
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        preview = ", ".join(changed[:12]) + (" ..." if len(changed) > 12 else "")
        errors.append(
            f"tracked source/test files changed during audit ({len(changed)}): {preview}. "
            "Restore only with user approval; do not hide the mutation."
        )
    return errors

@workspace_locked_command
def cmd_init(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["evidence"].mkdir(parents=True, exist_ok=True)
    if paths["complete"].exists():
        emit({"ok": False, "errors": [
            f"audit workspace is sealed: {paths['complete']}. For a re-audit after remediation, use a fresh auditor instance and a new audit directory (for example .runtime-feature-audit-run2)."
        ]}, args.format)
        return 2
    if paths["plan"].exists() and not args.force:
        emit({"ok": False, "errors": [f"plan already exists: {paths['plan']} (use --force to replace)"]}, args.format)
        return 2
    audit_id = args.audit_id or f"rff-{uuid4().hex[:10]}"
    audit_mode = args.mode.upper()
    plan = {
        "schema_version": "2.0",
        "audit_id": audit_id,
        "audit_mode": audit_mode,
        "created_at_utc": utc_now(),
        "target": {
            "project": args.project_name,
            "environment": args.environment,
            "scope": args.scope,
            "startup_path": "",
            "runtime_identity_expectation": "",
            "collision_surfaces": [],
        },
        "features": [],
    }
    paths["plan"].write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    if audit_mode == "SYSTEM":
        inventory = {
            "schema_version": "1.0",
            "audit_id": audit_id,
            "discovery_status": "IN_PROGRESS",
            "discovery_sources": [],
            "discovery_blockers": [],
            "workflow_coverage": {"applicable": False, "reason": "Populate after system discovery."},
            "features": [],
        }
        paths["inventory"].write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    baseline_root = args.target_root.resolve()
    baseline = tracked_source_snapshot(baseline_root)
    _atomic_write_text(paths["baseline"], json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    genesis = _build_audit_genesis(plan, baseline_root, baseline, provenance="v2.9.5-init")
    genesis_errors = validate_schema(genesis, load_schema("audit-genesis.schema.json"), "audit-genesis")
    if genesis_errors:
        emit({"ok": False, "errors": genesis_errors}, args.format)
        return 2
    _atomic_write_text(paths["genesis"], json.dumps(genesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if paths["complete"].exists():
        paths["complete"].unlink()
    if paths["report_state"].exists():
        paths["report_state"].unlink()
    _atomic_write_text(paths["active"], json.dumps({
        "audit_id": audit_id,
        "audit_mode": audit_mode,
        "started_at_utc": utc_now(),
        "target_root": str(baseline_root),
        "genesis_jcs_sha256": _genesis_digest(genesis),
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _fsync_directory(paths["dir"])
    next_step = "Populate features/probes, then run validate-plan."
    if audit_mode == "SYSTEM":
        next_step = "Populate feature-inventory.json and audit-plan.json from whole-system discovery, then run validate-plan."
    emit(with_heads({
        "ok": True,
        "audit_mode": audit_mode,
        "audit_dir": str(paths["dir"]),
        "plan": str(paths["plan"]),
        "inventory": str(paths["inventory"]) if audit_mode == "SYSTEM" else None,
        "tracked_source_baseline": baseline.get("available", False),
        "tracked_source_root": str(baseline_root),
        "next": next_step,
    }, paths), args.format)
    return 0


def validate_system_files(paths: dict[str, Path], plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("audit_mode", "FEATURE") != "SYSTEM":
        return {"ok": True, "errors": [], "warnings": [], "applicable": False}
    if not paths["inventory"].exists():
        return {"ok": False, "errors": [f"SYSTEM audit requires feature inventory: {paths['inventory']}"], "warnings": [], "applicable": True}
    inventory = load_json(paths["inventory"])
    result = validate_inventory_obj(inventory, plan)
    result["applicable"] = True
    return result


@workspace_locked_command
def cmd_validate_plan(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    plan = load_json(paths["plan"])
    result = validate_plan_obj(plan)
    system_result = validate_system_files(paths, plan)
    result["errors"].extend(system_result.get("errors", []))
    result["warnings"].extend(system_result.get("warnings", []))
    result["ok"] = not result["errors"]
    if system_result.get("applicable"):
        result["system_inventory"] = {k: v for k, v in system_result.items() if k not in {"ok", "errors", "warnings", "applicable"}}
    emit(with_heads(result, paths), args.format)
    return 0 if result["ok"] else 2


PREFLIGHT_INTENTS = {"environment_start", "runtime_identity", "environment_collision"}


def preflight_finishes(events: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
    attempts, errors = fold_attempts(events)
    if errors:
        return []
    return [
        pair.get("finish") or {}
        for pair in attempts.values()
        if (pair.get("start") or {}).get("probe_intent") == intent and pair.get("finish")
    ]


def startup_health_survived(events: list[dict[str, Any]]) -> bool:
    return any(x.get("result") == "SURVIVED" for x in preflight_finishes(events, "environment_start"))


def runtime_identity_completed(events: list[dict[str, Any]]) -> bool:
    return bool(preflight_finishes(events, "runtime_identity"))


def collision_check_survived(events: list[dict[str, Any]]) -> bool:
    return any(x.get("result") == "SURVIVED" for x in preflight_finishes(events, "environment_collision"))


def preflight_error_for(intent: str, events: list[dict[str, Any]]) -> str | None:
    if intent == "environment_start":
        return None
    if not startup_health_survived(events):
        return "startup health has not completed SURVIVED; finish environment_start first"
    if intent == "runtime_identity":
        return None
    if not runtime_identity_completed(events):
        return "runtime identity has not completed; finish runtime_identity before collision/feature probes"
    if intent == "environment_collision":
        return None
    if not collision_check_survived(events):
        return "audit environment collision check has not completed SURVIVED; isolate shared queues/DB schemas/buckets/ports/tenants before feature probes"
    return None


@active_mutation_command
def cmd_attempt_start(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    plan = load_json(paths["plan"])
    valid = validate_plan_obj(plan)
    system_valid = validate_system_files(paths, plan)
    combined_errors = [*valid["errors"], *system_valid.get("errors", [])]
    if combined_errors:
        emit({"ok": False, "errors": ["audit plan/system inventory is invalid; run validate-plan first", *combined_errors]}, args.format)
        return 2
    feature, probe = find_feature_probe(plan, args.feature_id, args.probe_id)
    if (feature.get("dependency_sensitive") or probe.get("probe_intent") in PREFLIGHT_INTENTS) and not str(args.repro_command or "").strip():
        emit(with_heads({"ok": False, "errors": [
            f"{feature['feature_id']}/{probe['probe_id']} requires --repro-command because it is preflight/dependency-sensitive"
        ]}, paths), args.format)
        return 2
    existing_attempt_events = read_events(paths["log"])
    attempt_errors = _ledger_integrity_errors(paths["log"], existing_attempt_events)
    existing_attempts, fold_errors = fold_attempts(existing_attempt_events)
    attempt_errors.extend(fold_errors)
    if attempt_errors:
        emit({"ok": False, "errors": attempt_errors}, args.format)
        return 2
    preflight_error = preflight_error_for(probe.get("probe_intent", ""), existing_attempt_events)
    if preflight_error:
        emit(with_heads({"ok": False, "errors": [preflight_error]}, paths), args.format)
        return 2
    prior_same_probe = [pair for pair in existing_attempts.values() if pair.get("start") and pair["start"].get("probe_id") == args.probe_id]
    if prior_same_probe and not (getattr(args, "changed_variable", None) or getattr(args, "retry_reason", None)):
        emit({"ok": False, "errors": [
            f"repeated probe {args.probe_id} requires --changed-variable or --retry-reason; identical retries are not persistent investigation"
        ]}, args.format)
        return 2
    if getattr(args, "hypothesis_id", None):
        hstate = investigation_summary(paths)
        if not hstate.get("ok"):
            emit({"ok": False, "errors": hstate.get("errors", [])}, args.format)
            return 2
        h = next((x for x in hstate.get("hypotheses", []) if x.get("hypothesis_id") == args.hypothesis_id), None)
        if not h:
            emit({"ok": False, "errors": [f"hypothesis not found: {getattr(args, "hypothesis_id", None)}"]}, args.format)
            return 2
        if h.get("status") in HYPOTHESIS_TERMINAL:
            emit({"ok": False, "errors": [f"cannot attach attempt to terminal hypothesis {getattr(args, "hypothesis_id", None)}: {h.get('status')}"]}, args.format)
            return 2
        if h.get("attempts_used", 0) >= h.get("max_attempts", 0):
            emit({"ok": False, "errors": [f"hypothesis budget exhausted for {getattr(args, "hypothesis_id", None)}; close it as BUDGET_EXHAUSTED or justify a new hypothesis"]}, args.format)
            return 2
        if h.get("feature_id") != args.feature_id:
            emit({"ok": False, "errors": [f"hypothesis {getattr(args, "hypothesis_id", None)} belongs to {h.get('feature_id')}, not {args.feature_id}"]}, args.format)
            return 2
    attempt_id = args.attempt_id or f"att-{uuid4().hex[:12]}"
    if attempt_id in existing_attempts:
        emit({"ok": False, "errors": [f"duplicate attempt_id: {attempt_id}"]}, args.format)
        return 2
    event = append_event(paths["log"], {
        "schema_version": "2.0",
        "event_type": "STARTED",
        "attempt_id": attempt_id,
        "timestamp_utc": utc_now(),
        "feature_id": feature["feature_id"],
        "feature_claim": feature["claim"],
        "probe_id": probe["probe_id"],
        "probe_intent": probe["probe_intent"],
        "contract_relation": probe["contract_relation"],
        "input_family": probe.get("input_family", ""),
        "input_case": probe.get("input_case", ""),
        "entry_point": probe.get("entry_point") or (feature.get("entry_points") or [""])[0],
        "expected": probe["expected"],
        "effect_checks": probe.get("effect_checks", []),
        "action": args.action or probe.get("action", ""),
        "repro_command": args.repro_command,
        "preconditions": args.precondition,
        "hypothesis_id": getattr(args, "hypothesis_id", None),
        "changed_variable": getattr(args, "changed_variable", None),
        "retry_reason": getattr(args, "retry_reason", None),
        "escalation_stage": getattr(args, "escalation_stage", None),
    })
    emit(with_heads({"ok": True, "attempt_id": attempt_id, "sequence": event["sequence"], "event_sha256": event["event_sha256"]}, paths), args.format)
    return 0


@active_mutation_command
def cmd_attempt_finish(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    events = read_events(paths["log"])
    errors = _ledger_integrity_errors(paths["log"], events)
    attempts, fold_errors = fold_attempts(events)
    errors.extend(fold_errors)
    if errors:
        emit({"ok": False, "errors": errors}, args.format)
        return 2
    pair = attempts.get(args.attempt_id)
    if not pair or not pair.get("start"):
        emit({"ok": False, "errors": [f"attempt has no STARTED event: {args.attempt_id}"]}, args.format)
        return 2
    if pair.get("finish"):
        emit({"ok": False, "errors": [f"attempt already finished: {args.attempt_id}"]}, args.format)
        return 2
    start = pair["start"]
    event = append_event(paths["log"], {
        "schema_version": "2.0",
        "event_type": "FINISHED",
        "attempt_id": args.attempt_id,
        "timestamp_utc": utc_now(),
        "feature_id": start["feature_id"],
        "feature_claim": start["feature_claim"],
        "probe_id": start["probe_id"],
        "probe_intent": start["probe_intent"],
        "contract_relation": start["contract_relation"],
        "input_family": start.get("input_family", ""),
        "input_case": start.get("input_case", ""),
        "observed": args.observed,
        "side_effect_check": args.side_effect_check,
        "persistence_check": args.persistence_check,
        "http_status": args.http_status,
        "exit_code": args.exit_code,
        "evidence_refs": args.evidence,
        "result": args.result,
        "failure_pattern": args.failure_pattern,
        "confidence": args.confidence,
        "severity": getattr(args, "severity", "MEDIUM"),
        "notes": args.notes,
        "hypothesis_id": start.get("hypothesis_id"),
        "changed_variable": start.get("changed_variable", ""),
        "retry_reason": start.get("retry_reason", ""),
        "escalation_stage": start.get("escalation_stage"),
    })
    emit(with_heads({"ok": True, "attempt_id": args.attempt_id, "result": args.result, "sequence": event["sequence"], "event_sha256": event["event_sha256"]}, paths), args.format)
    return 0


def _load_batch_input(value: str) -> dict[str, Any]:
    if value == "-":
        try:
            return json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON on stdin: {exc}")
    return load_json(Path(value))


@active_mutation_command
def cmd_attempt_batch(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    doc = _load_batch_input(args.input)
    schema_errors = validate_schema(doc, load_schema("attempt-batch.schema.json"))
    if schema_errors:
        emit(with_heads({"ok": False, "errors": schema_errors}, paths), args.format)
        return 2
    plan = load_json(paths["plan"])
    valid = validate_plan_obj(plan)
    system_valid = validate_system_files(paths, plan)
    errors = [*valid["errors"], *system_valid.get("errors", [])]
    if errors:
        emit(with_heads({"ok": False, "errors": ["audit plan/system inventory is invalid", *errors]}, paths), args.format)
        return 2
    phase = doc["phase"]
    specs = doc["attempts"]
    for idx, spec in enumerate(specs, 1):
        if not isinstance(spec, dict):
            errors.append(f"attempts[{idx}] must be an object")
            continue
        required = ("feature_id", "probe_id") if phase == "START" else ("attempt_id", "observed", "result")
        for key in required:
            if key not in spec or spec.get(key) in (None, ""):
                errors.append(f"attempts[{idx}] missing required field {key}")
        if phase == "FINISH":
            if spec.get("result") not in RESULTS:
                errors.append(f"attempts[{idx}].result must be one of {sorted(RESULTS)}")
            if spec.get("failure_pattern", "NONE_OBSERVED") not in PATTERNS:
                errors.append(f"attempts[{idx}].failure_pattern must be one of {sorted(PATTERNS)}")
            if spec.get("confidence", "MEDIUM") not in CONFIDENCE:
                errors.append(f"attempts[{idx}].confidence must be one of {sorted(CONFIDENCE)}")
        if spec.get("escalation_stage") is not None and str(spec.get("escalation_stage")) not in ESCALATION_STAGES:
            errors.append(f"attempts[{idx}].escalation_stage must be 0-11")
    if errors:
        emit({"ok": False, "phase": phase, "errors": errors}, args.format)
        return 2
    current = read_events(paths["log"])
    ledger_errors = _ledger_integrity_errors(paths["log"], current)
    attempts, fold_errors = fold_attempts(current)
    ledger_errors.extend(fold_errors)
    if ledger_errors:
        emit(with_heads({"ok": False, "errors": ledger_errors}, paths), args.format)
        return 2
    if phase == "START":
        # Respect preflight ordering even in batch mode. A batch may contain only probes
        # whose prerequisites were already completed before this batch; this prevents
        # pre-registering feature probes before identity/isolation evidence exists.
        for spec in specs:
            try:
                _, planned_probe = find_feature_probe(plan, spec["feature_id"], spec["probe_id"])
            except (KeyError, SystemExit):
                continue
            preflight_error = preflight_error_for(planned_probe.get("probe_intent", ""), current)
            if preflight_error:
                emit(with_heads({"ok": False, "phase": phase, "errors": [preflight_error]}, paths), args.format)
                return 2
    payloads: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    if phase == "START":
        pending_probe_ids: list[str] = []
        hstate = investigation_summary(paths)
        hmap = {x.get("hypothesis_id"): x for x in hstate.get("hypotheses", [])} if hstate.get("ok") else {}
        h_pending: Counter[str] = Counter()
        for spec in specs:
            feature, probe = find_feature_probe(plan, spec["feature_id"], spec["probe_id"])
            repro = spec.get("repro_command")
            if (feature.get("dependency_sensitive") or probe.get("probe_intent") in PREFLIGHT_INTENTS) and not str(repro or "").strip():
                errors.append(f"{feature['feature_id']}/{probe['probe_id']} requires repro_command")
            prior_same = [pair for pair in attempts.values() if pair.get("start") and pair["start"].get("probe_id") == spec["probe_id"]]
            prior_same += [x for x in pending_probe_ids if x == spec["probe_id"]]
            if prior_same and not (spec.get("changed_variable") or spec.get("retry_reason")):
                errors.append(f"repeated probe {spec['probe_id']} requires changed_variable or retry_reason")
            hid = spec.get("hypothesis_id")
            if hid:
                h = hmap.get(hid)
                if not h:
                    errors.append(f"hypothesis not found: {hid}")
                elif h.get("status") in HYPOTHESIS_TERMINAL:
                    errors.append(f"cannot attach attempt to terminal hypothesis {hid}: {h.get('status')}")
                elif h.get("feature_id") != feature["feature_id"]:
                    errors.append(f"hypothesis {hid} belongs to {h.get('feature_id')}, not {feature['feature_id']}")
                elif h.get("attempts_used", 0) + h_pending[hid] >= h.get("max_attempts", 0):
                    errors.append(f"hypothesis budget exhausted for {hid}")
                h_pending[hid] += 1
            aid = spec.get("attempt_id") or f"att-{uuid4().hex[:12]}"
            if aid in attempts or any(x.get("attempt_id") == aid for x in payloads):
                errors.append(f"duplicate attempt_id: {aid}")
            payloads.append({
                "schema_version": "2.0", "event_type": "STARTED", "attempt_id": aid,
                "timestamp_utc": utc_now(), "feature_id": feature["feature_id"], "feature_claim": feature["claim"],
                "probe_id": probe["probe_id"], "probe_intent": probe["probe_intent"], "contract_relation": probe["contract_relation"],
                "input_family": probe.get("input_family", ""), "input_case": probe.get("input_case", ""),
                "entry_point": probe.get("entry_point") or (feature.get("entry_points") or [""])[0], "expected": probe["expected"],
                "effect_checks": probe.get("effect_checks", []), "action": spec.get("action") or probe.get("action", ""),
                "repro_command": repro, "preconditions": spec.get("preconditions", []), "hypothesis_id": hid,
                "changed_variable": spec.get("changed_variable"), "retry_reason": spec.get("retry_reason"),
                "escalation_stage": str(spec.get("escalation_stage")) if spec.get("escalation_stage") is not None else None,
            })
            outputs.append({"attempt_id": aid, "feature_id": feature["feature_id"], "probe_id": probe["probe_id"]})
            pending_probe_ids.append(spec["probe_id"])
    else:
        seen_finish: set[str] = set()
        for spec in specs:
            aid = spec["attempt_id"]
            if aid in seen_finish:
                errors.append(f"duplicate FINISH in batch for {aid}")
                continue
            seen_finish.add(aid)
            pair = attempts.get(aid)
            if not pair or not pair.get("start"):
                errors.append(f"attempt has no STARTED event: {aid}")
                continue
            if pair.get("finish"):
                errors.append(f"attempt already finished: {aid}")
                continue
            start = pair["start"]
            payloads.append({
                "schema_version": "2.0", "event_type": "FINISHED", "attempt_id": aid, "timestamp_utc": utc_now(),
                "feature_id": start["feature_id"], "feature_claim": start["feature_claim"], "probe_id": start["probe_id"],
                "probe_intent": start["probe_intent"], "contract_relation": start["contract_relation"],
                "input_family": start.get("input_family", ""), "input_case": start.get("input_case", ""),
                "observed": spec["observed"], "side_effect_check": spec.get("side_effect_check"),
                "persistence_check": spec.get("persistence_check"), "http_status": spec.get("http_status"),
                "exit_code": spec.get("exit_code"), "evidence_refs": spec.get("evidence", []), "result": spec["result"],
                "failure_pattern": spec.get("failure_pattern", "NONE_OBSERVED"), "confidence": spec.get("confidence", "MEDIUM"),
                "severity": spec.get("severity", "MEDIUM"),
                "notes": spec.get("notes", ""), "hypothesis_id": start.get("hypothesis_id"),
                "changed_variable": start.get("changed_variable", ""), "retry_reason": start.get("retry_reason", ""),
                "escalation_stage": start.get("escalation_stage"),
            })
            outputs.append({"attempt_id": aid, "result": spec["result"]})
    if errors:
        emit(with_heads({"ok": False, "phase": phase, "errors": errors}, paths), args.format)
        return 2
    events = append_events(paths["log"], payloads)
    for out, event in zip(outputs, events):
        out["sequence"] = event["sequence"]
        out["event_sha256"] = event["event_sha256"]
    emit(with_heads({"ok": True, "phase": phase, "count": len(events), "attempts": outputs}, paths), args.format)
    return 0


@active_mutation_command
def cmd_hypothesis_open(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    plan = load_json(paths["plan"])
    if not any(f.get("feature_id") == args.feature_id for f in plan.get("features", [])):
        emit({"ok": False, "errors": [f"feature not found in plan: {args.feature_id}"]}, args.format)
        return 2
    hid = args.hypothesis_id or f"hyp-{uuid4().hex[:12]}"
    existing = read_events(paths["hypotheses"])
    ledger_errors = _ledger_integrity_errors(paths["hypotheses"], existing)
    hypotheses, fold_errors = fold_hypotheses(existing)
    ledger_errors.extend(fold_errors)
    if args.max_attempts < 1:
        ledger_errors.append("max_attempts must be >= 1")
    if args.trigger_attempt_id:
        attempt_events = read_events(paths["log"])
        ledger_errors.extend(_ledger_integrity_errors(paths["log"], attempt_events))
        attempts, attempt_errors = fold_attempts(attempt_events)
        ledger_errors.extend(attempt_errors)
        if args.trigger_attempt_id not in attempts:
            ledger_errors.append(f"trigger attempt not found: {args.trigger_attempt_id}")
    if ledger_errors:
        emit({"ok": False, "errors": ledger_errors}, args.format)
        return 2
    if hid in hypotheses:
        emit({"ok": False, "errors": [f"hypothesis already exists: {hid}"]}, args.format)
        return 2
    event = append_event(paths["hypotheses"], {
        "schema_version": "1.0",
        "event_type": "HYPOTHESIS_OPENED",
        "hypothesis_id": hid,
        "timestamp_utc": utc_now(),
        "feature_id": args.feature_id,
        "statement": args.statement,
        "trigger_attempt_id": args.trigger_attempt_id,
        "next_probe": args.next_probe,
        "max_attempts": args.max_attempts,
        "evidence_for": args.evidence_for,
        "evidence_against": args.evidence_against,
    })
    emit(with_heads({"ok": True, "hypothesis_id": hid, "status": "OPEN", "sequence": event["sequence"], "event_sha256": event["event_sha256"]}, paths), args.format)
    return 0


@active_mutation_command
def cmd_hypothesis_update(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    existing = read_events(paths["hypotheses"])
    errors = _ledger_integrity_errors(paths["hypotheses"], existing)
    hypotheses, fold_errors = fold_hypotheses(existing)
    errors.extend(fold_errors)
    h = hypotheses.get(args.hypothesis_id)
    if not h:
        errors.append(f"hypothesis not found: {getattr(args, "hypothesis_id", None)}")
    elif h.get("status") in HYPOTHESIS_TERMINAL:
        errors.append(f"hypothesis already terminal: {getattr(args, "hypothesis_id", None)} ({h.get('status')})")
    if args.status in {"OPEN", "SUPPORTED"} and not (args.next_probe or h and h.get("next_probe")):
        errors.append("non-terminal hypothesis update requires next_probe")
    if args.status == "CONFIRMED" and not (args.evidence_for or h and h.get("evidence_for")):
        errors.append("CONFIRMED hypothesis requires evidence_for")
    if args.status == "REFUTED" and not (args.evidence_against or h and h.get("evidence_against")):
        errors.append("REFUTED hypothesis requires evidence_against")
    if args.status in {"BLOCKED", "BUDGET_EXHAUSTED"} and not str(args.notes).strip():
        errors.append(f"{args.status} hypothesis requires explanatory --notes")
    if errors:
        emit({"ok": False, "errors": errors}, args.format)
        return 2
    event = append_event(paths["hypotheses"], {
        "schema_version": "1.0",
        "event_type": "HYPOTHESIS_UPDATED",
        "hypothesis_id": getattr(args, "hypothesis_id", None),
        "timestamp_utc": utc_now(),
        "status": args.status,
        "evidence_for": args.evidence_for,
        "evidence_against": args.evidence_against,
        "next_probe": args.next_probe,
        "changed_variable": getattr(args, "changed_variable", None),
        "notes": args.notes,
    })
    emit(with_heads({"ok": True, "hypothesis_id": getattr(args, "hypothesis_id", None), "status": args.status, "sequence": event["sequence"], "event_sha256": event["event_sha256"]}, paths), args.format)
    return 0


@workspace_locked_command
def cmd_investigation_status(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    emit(with_heads(investigation_summary(paths), paths), args.format)
    return 0


@workspace_locked_command
def cmd_summary(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    plan = load_json(paths["plan"])
    events = read_events(paths["log"])
    investigation = investigation_summary(paths)
    summary = apply_investigation_verdicts(derive_summary(plan, events), investigation)
    summary["investigation"] = investigation
    emit(with_heads(summary, paths), args.format)
    return 0


REPORTING_MISUSE_PATTERNS = [
    (re.compile(r"\bverified reality\b", re.I), "'verified reality' is prohibited as a surviving-feature verdict"),
    (re.compile(r"\b(run\s*\d*|feature|system|capability|function)\s*\(?verified\)?\b", re.I), "'verified' must not replace NOT_FALSIFIED as an audit verdict"),
    (re.compile(r"\bverdict\s*[:=-]\s*(verified|proven|alive|fully working|confirmed working)\b", re.I), "non-RFF verdict synonym detected"),
    (re.compile(r"\b(all|every)\s+(features?|functions?|capabilities?)\b[^\n.]{0,80}\b(verified|proven|alive|fully working|confirmed working)\b", re.I), "whole-scope success language overstates finite falsification"),
]


def reporting_language_errors(text: str, label: str) -> list[str]:
    errors: list[str] = []
    for pattern, message in REPORTING_MISUSE_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            errors.append(f"{label}:{line_no}: {message}: {match.group(0)!r}")
    return errors


def cmd_terminology_check(args: argparse.Namespace) -> int:
    errors: list[str] = []
    checked: list[str] = []
    for raw in args.input:
        path = Path(raw)
        if not path.exists():
            errors.append(f"missing input file: {path}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        errors.extend(reporting_language_errors(text, str(path)))
        checked.append(str(path))
    emit({"ok": not errors, "errors": errors, "checked": checked}, args.format)
    return 0 if not errors else 2


# v2.9 canonical result helpers -------------------------------------------------

def _reject_floats(value: Any, path: str = "$") -> None:
    """RFF canonical result contract intentionally excludes floating-point values."""
    if isinstance(value, float):
        raise ValueError(f"floating-point value is not permitted in canonical RFF JSON: {path}")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _reject_floats(item, f"{path}[{idx}]")


def _reject_unsafe_jcs_integers(value: Any, path: str = "$") -> None:
    """Keep canonical integer values inside the RFC 7493 interoperable range."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < -MAX_JCS_SAFE_INTEGER or value > MAX_JCS_SAFE_INTEGER:
            raise ValueError(
                f"integer outside interoperable JCS range at {path}: {value}; "
                f"use a string for exact larger values"
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_unsafe_jcs_integers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _reject_unsafe_jcs_integers(item, f"{path}[{idx}]")


def _jcs_string(value: str) -> str:
    # ensure_ascii=False preserves Unicode scalar values; json.dumps supplies the
    # JSON-required escapes for quotes, reverse solidus, and control characters.
    # Lone UTF-16 surrogates are rejected because JCS requires valid Unicode.
    value.encode("utf-8", errors="strict")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _jcs_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        raise ValueError("floating-point values are not permitted in the RFF JCS profile")
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_jcs_text(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JCS object member names must be strings")
        # RFC 8785 sorts object property names by UTF-16 code units. Big-endian
        # encoded UTF-16 bytes preserve unsigned code-unit lexicographic order.
        keys = sorted(value, key=lambda key: key.encode("utf-16be", errors="strict"))
        return "{" + ",".join(_jcs_string(key) + ":" + _jcs_text(value[key]) for key in keys) + "}"
    raise ValueError(f"unsupported canonical JSON type: {type(value).__name__}")


def jcs_bytes(value: Any) -> bytes:
    """RFC 8785/JCS bytes for RFF's strict float-free canonical profile."""
    _validate_unicode_scalars(value)
    _reject_floats(value)
    _reject_unsafe_jcs_integers(value)
    return _jcs_text(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(jcs_bytes(value)).hexdigest()


def _producer_errors(*docs: tuple[str, dict[str, Any] | None]) -> list[str]:
    """Cross-bind producer identity while allowing explicitly compatible patch producers."""
    errors: list[str] = []
    seen: list[tuple[str, dict[str, Any]]] = []
    for label, doc in docs:
        if not isinstance(doc, dict):
            continue
        producer = doc.get("producer")
        if not isinstance(producer, dict):
            errors.append(f"{label} producer is missing or invalid")
            continue
        if producer.get("name") != "runtime-feature-falsifier":
            errors.append(f"{label} producer name is not runtime-feature-falsifier")
        version = producer.get("version")
        if version not in SUPPORTED_PRODUCER_VERSIONS:
            errors.append(f"{label} producer version is not an explicitly supported 2.9 patch: {version}")
        seen.append((label, producer))
    if seen:
        baseline_label, baseline = seen[0]
        for label, producer in seen[1:]:
            if producer != baseline:
                errors.append(f"producer mismatch between {baseline_label} and {label}")
    return errors


def _stage_text(path: Path, text: str) -> Path:
    """Write and fsync a same-directory private temporary for atomic replacement."""
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return tmp


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace one file from an fsync'd same-directory private temporary."""
    tmp = _stage_text(path, text)
    try:
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _fsync_directory(path: Path) -> None:
    """Best-effort POSIX directory-entry durability; non-POSIX semantics are platform-dependent."""
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _looks_like_legacy_completion_marker(value: Any) -> bool:
    """Recognize the exact completion-marker shape emitted by released v2.9.0-v2.9.2."""
    return isinstance(value, dict) and set(value) == LEGACY_COMPLETION_MARKER_KEYS


def _legacy_completion_errors(marker: dict[str, Any], result: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Validate a historical completion marker without treating it as a v2.9.3 seal."""
    errors: list[str] = []
    producer_errors = _producer_errors(("audit-result", result), ("result-manifest", manifest))
    errors.extend(producer_errors)
    result_producer = result.get("producer", {}) if isinstance(result.get("producer"), dict) else {}
    if result_producer.get("version") not in {"2.9.0", "2.9.1", "2.9.2"}:
        errors.append("legacy completion marker is only valid for producer versions 2.9.0-2.9.2")
    audit = result.get("audit", {}) if isinstance(result.get("audit"), dict) else {}
    if marker.get("audit_id") != audit.get("audit_id"):
        errors.append("legacy completion marker audit_id does not match audit-result")
    if marker.get("audit_mode") != audit.get("mode"):
        errors.append("legacy completion marker audit_mode does not match audit-result")
    if marker.get("sealed") is not True:
        errors.append("legacy completion marker must declare sealed=true")
    timestamp_error = _utc_timestamp_error(marker.get("completed_at_utc"), "legacy completion marker completed_at_utc")
    if timestamp_error:
        errors.append(timestamp_error)
    if not isinstance(marker.get("next_run_policy"), str) or not marker.get("next_run_policy"):
        errors.append("legacy completion marker next_run_policy is missing or invalid")
    if audit.get("gate") != "PASSED":
        errors.append("legacy completion marker exists but audit-result gate is not PASSED")
    return errors


def _looks_like_legacy_digest_seal(value: Any) -> bool:
    """Recognize the pre-v2.9.3 digest-bound seal emitted by the 2.9.2 hardening patch."""
    return isinstance(value, dict) and set(value) == LEGACY_DIGEST_SEAL_KEYS


def _legacy_digest_seal_errors(seal: dict[str, Any], result: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Validate a historical digest-bound seal as migration input, never as a current seal."""
    errors: list[str] = []
    errors.extend(_producer_errors(("audit-result", result), ("result-manifest", manifest), ("legacy-audit-seal", seal)))
    producer = seal.get("producer", {}) if isinstance(seal.get("producer"), dict) else {}
    if producer.get("version") not in {"2.9.0", "2.9.1", "2.9.2"}:
        errors.append("legacy digest-bound audit seal is only valid for producer versions 2.9.0-2.9.2")
    if seal.get("schema_version") != "1.0.0":
        errors.append("legacy digest-bound audit seal schema_version must be 1.0.0")
    if seal.get("canonicalization") != "RFC8785-JCS-float-free-profile":
        errors.append("legacy digest-bound audit seal canonicalization profile is invalid")
    if seal.get("state") != "PASSED" or seal.get("sealed") is not True:
        errors.append("legacy digest-bound audit seal must declare PASSED and sealed=true")
    audit = result.get("audit", {}) if isinstance(result.get("audit"), dict) else {}
    if seal.get("audit_id") != audit.get("audit_id"):
        errors.append("legacy audit seal audit_id does not match audit-result")
    if seal.get("audit_mode") != audit.get("mode"):
        errors.append("legacy audit seal audit_mode does not match audit-result")
    if audit.get("gate") != "PASSED":
        errors.append("legacy audit seal exists but audit-result gate is not PASSED")
    timestamp_error = _utc_timestamp_error(seal.get("completed_at_utc"), "legacy audit seal completed_at_utc")
    if timestamp_error:
        errors.append(timestamp_error)
    if not isinstance(seal.get("next_run_policy"), str) or not seal.get("next_run_policy"):
        errors.append("legacy audit seal next_run_policy is missing or invalid")
    subjects = seal.get("subjects", {}) if isinstance(seal.get("subjects"), dict) else {}
    try:
        if subjects.get("audit_result_jcs_sha256") != canonical_sha256(result):
            errors.append("legacy audit seal result digest mismatch")
        if subjects.get("result_manifest_jcs_sha256") != canonical_sha256(manifest):
            errors.append("legacy audit seal manifest digest mismatch")
    except ValueError as exc:
        errors.append(f"legacy audit seal canonical hashing failed: {exc}")
    return errors


def _looks_like_v293_seal(value: Any) -> bool:
    """Recognize the native v2.9.3 seal whose local provenance metadata was not manifest-bound."""
    if not isinstance(value, dict):
        return False
    keys = set(value)
    return keys == LEGACY_V293_SEAL_KEYS or keys == (LEGACY_V293_SEAL_KEYS | {"migration"})


def _v293_seal_errors(seal: dict[str, Any], result: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """Validate a genuine v2.9.3 strong seal as a historical migration input."""
    errors: list[str] = []
    errors.extend(_producer_errors(("audit-result", result), ("result-manifest", manifest), ("v2.9.3-audit-seal", seal)))
    producer = seal.get("producer", {}) if isinstance(seal.get("producer"), dict) else {}
    if producer.get("version") != "2.9.3":
        errors.append("v2.9.3 audit seal must declare producer version 2.9.3")
    if seal.get("schema_version") != "1.0.0":
        errors.append("v2.9.3 audit seal schema_version must be 1.0.0")
    if seal.get("canonicalization") != "RFC8785-JCS-float-free-profile":
        errors.append("v2.9.3 audit seal canonicalization profile is invalid")
    if seal.get("state") != "PASSED" or seal.get("sealed") is not True:
        errors.append("v2.9.3 audit seal must declare PASSED and sealed=true")
    audit = result.get("audit", {}) if isinstance(result.get("audit"), dict) else {}
    if seal.get("audit_id") != audit.get("audit_id"):
        errors.append("v2.9.3 audit seal audit_id does not match audit-result")
    if seal.get("audit_mode") != audit.get("mode"):
        errors.append("v2.9.3 audit seal audit_mode does not match audit-result")
    if audit.get("gate") != "PASSED":
        errors.append("v2.9.3 audit seal exists but audit-result gate is not PASSED")
    for field in ("completed_at_utc", "sealed_at_utc"):
        timestamp_error = _utc_timestamp_error(seal.get(field), f"v2.9.3 audit seal {field}")
        if timestamp_error:
            errors.append(timestamp_error)
    if not isinstance(seal.get("next_run_policy"), str) or not seal.get("next_run_policy"):
        errors.append("v2.9.3 audit seal next_run_policy is missing or invalid")
    subjects = seal.get("subjects", {}) if isinstance(seal.get("subjects"), dict) else {}
    try:
        if subjects.get("audit_result_jcs_sha256") != canonical_sha256(result):
            errors.append("v2.9.3 audit seal result digest mismatch")
        if subjects.get("result_manifest_jcs_sha256") != canonical_sha256(manifest):
            errors.append("v2.9.3 audit seal manifest digest mismatch")
    except ValueError as exc:
        errors.append(f"v2.9.3 audit seal canonical hashing failed: {exc}")
    migration = seal.get("migration")
    if migration is not None:
        if not isinstance(migration, dict):
            errors.append("v2.9.3 audit seal migration metadata is invalid")
        else:
            if migration.get("from_producer_version") not in {"2.9.0", "2.9.1", "2.9.2"}:
                errors.append("v2.9.3 audit seal migration producer is invalid")
            if migration.get("from_format") not in {"legacy-completion-marker", "digest-bound-pre-v2.9.3-seal"}:
                errors.append("v2.9.3 audit seal migration format is invalid")
    return errors


def _looks_like_v294_seal(value: Any) -> bool:
    """Recognize the manifest-bound v2.9.4 seal as migration input to v2.9.5."""
    if not isinstance(value, dict):
        return False
    producer = value.get("producer") if isinstance(value.get("producer"), dict) else {}
    if producer.get("version") != "2.9.4":
        return False
    keys = set(value)
    return keys == LEGACY_V294_SEAL_KEYS or keys == (LEGACY_V294_SEAL_KEYS | {"migration"})


def _v294_seal_errors(seal: dict[str, Any], result: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_producer_errors(("audit-result", result), ("result-manifest", manifest), ("v2.9.4-audit-seal", seal)))
    producer = seal.get("producer", {}) if isinstance(seal.get("producer"), dict) else {}
    if producer.get("version") != "2.9.4":
        errors.append("v2.9.4 audit seal must declare producer version 2.9.4")
    if seal.get("schema_version") != "1.0.0":
        errors.append("v2.9.4 audit seal schema_version must be 1.0.0")
    if seal.get("canonicalization") != "RFC8785-JCS-float-free-profile":
        errors.append("v2.9.4 audit seal canonicalization profile is invalid")
    if seal.get("state") != "PASSED" or seal.get("sealed") is not True:
        errors.append("v2.9.4 audit seal must declare PASSED and sealed=true")
    audit = result.get("audit", {}) if isinstance(result.get("audit"), dict) else {}
    if seal.get("audit_id") != audit.get("audit_id"):
        errors.append("v2.9.4 audit seal audit_id does not match audit-result")
    if seal.get("audit_mode") != audit.get("mode"):
        errors.append("v2.9.4 audit seal audit_mode does not match audit-result")
    if audit.get("gate") != "PASSED":
        errors.append("v2.9.4 audit seal exists but audit-result gate is not PASSED")
    for field in ("completed_at_utc", "sealed_at_utc"):
        timestamp_error = _utc_timestamp_error(seal.get(field), f"v2.9.4 audit seal {field}")
        if timestamp_error:
            errors.append(timestamp_error)
    provenance = seal.get("completion_time_provenance")
    old_allowed = {"v2.9.4-gate", "legacy-marker-unverified", "pre-v2.9.3-seal-unbound", "v2.9.3-seal-unbound"}
    if provenance not in old_allowed:
        errors.append(f"v2.9.4 audit seal completion_time_provenance is invalid: {provenance}")
    migration = seal.get("migration")
    if provenance == "v2.9.4-gate" and migration is not None:
        errors.append("native v2.9.4 audit seal must not contain migration metadata")
    if provenance != "v2.9.4-gate" and not isinstance(migration, dict):
        errors.append("migrated v2.9.4 audit seal requires migration metadata")
    if not isinstance(seal.get("next_run_policy"), str) or not seal.get("next_run_policy"):
        errors.append("v2.9.4 audit seal next_run_policy is missing or invalid")
    seal_metadata = _seal_metadata_from_seal(seal)
    if manifest.get("seal_metadata") != seal_metadata:
        errors.append("v2.9.4 audit seal metadata does not match result-manifest seal_metadata")
    subjects = seal.get("subjects", {}) if isinstance(seal.get("subjects"), dict) else {}
    try:
        if subjects.get("audit_result_jcs_sha256") != canonical_sha256(result):
            errors.append("v2.9.4 audit seal result digest mismatch")
        if subjects.get("result_manifest_jcs_sha256") != canonical_sha256(manifest):
            errors.append("v2.9.4 audit seal manifest digest mismatch")
    except ValueError as exc:
        errors.append(f"v2.9.4 audit seal canonical hashing failed: {exc}")
    return errors


def _build_seal_metadata(
    *,
    completed_at: str | None = None,
    sealed_at: str | None = None,
    migration: dict[str, Any] | None = None,
    completion_time_provenance: str = "v2.9.5-gate",
) -> dict[str, Any]:
    """Build manifest-bound seal metadata before the seal hashes the manifest."""
    seal_time = sealed_at or utc_now()
    metadata: dict[str, Any] = {
        "completed_at_utc": completed_at or seal_time,
        "sealed_at_utc": seal_time,
        "completion_time_provenance": completion_time_provenance,
        "next_run_policy": NEXT_RUN_POLICY,
    }
    if migration is not None:
        metadata["migration"] = dict(migration)
    errors = _seal_metadata_errors(metadata)
    if errors:
        raise ValueError("invalid seal metadata: " + "; ".join(errors))
    return metadata


def _build_audit_seal(
    paths: dict[str, Path],
    result: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build the v2.9.5 seal; all descriptive provenance is bound through manifest.seal_metadata."""
    metadata = manifest.get("seal_metadata")
    metadata_errors = _seal_metadata_errors(metadata, "result-manifest.seal_metadata")
    if metadata_errors:
        raise ValueError("invalid manifest seal metadata: " + "; ".join(metadata_errors))
    seal = {
        "schema_version": "1.0.0",
        "producer": dict(result["producer"]),
        "audit_id": result["audit"]["audit_id"],
        "audit_mode": result["audit"]["mode"],
        "state": "PASSED",
        "sealed": True,
        "canonicalization": "RFC8785-JCS-float-free-profile",
        "subjects": {
            "audit_result_jcs_sha256": canonical_sha256(result),
            "result_manifest_jcs_sha256": canonical_sha256(manifest),
        },
        **dict(metadata),
    }
    return seal


def _seal_state(paths: dict[str, Path], result: dict[str, Any], manifest: dict[str, Any]) -> tuple[str, list[str], dict[str, Any] | None]:
    """Derive gate state from authoritative sealing evidence.

    Released v2.9.0-v2.9.2 completion markers, pre-release digest-bound 2.9.2
    seals, and native v2.9.3 seals are migration inputs, never current v2.9.5 seals.
    """
    gate = result.get("audit", {}).get("gate") if isinstance(result.get("audit"), dict) else None
    if not paths["complete"].exists():
        if manifest.get("seal_metadata") is not None:
            return SEAL_STATE_ERROR, ["result-manifest seal_metadata exists but no audit seal exists"], None
        if gate == "PENDING":
            return SEAL_STATE_UNSEALED, [], None
        return SEAL_STATE_ERROR, ["audit-result claims PASSED but no audit seal exists"], None
    try:
        seal = load_json(paths["complete"])
    except SystemExit as exc:
        return SEAL_STATE_ERROR, [f"invalid audit seal: {exc}"], None
    if not isinstance(seal, dict):
        return SEAL_STATE_ERROR, ["audit seal root must be a JSON object"], None

    # Historical released marker: recognize exact old shape and require migration.
    if _looks_like_legacy_completion_marker(seal):
        errors = _legacy_completion_errors(seal, result, manifest)
        if errors:
            return SEAL_STATE_ERROR, errors, seal
        return SEAL_STATE_LEGACY, [], seal

    # The unpublished 2.9.2 hardening candidate already emitted a digest-bound seal.
    # Treat that exact historical shape as migration input so local work is not stranded.
    if _looks_like_legacy_digest_seal(seal):
        errors = _legacy_digest_seal_errors(seal, result, manifest)
        if errors:
            return SEAL_STATE_ERROR, errors, seal
        return SEAL_STATE_LEGACY, [], seal

    # Native v2.9.3 seals cryptographically bind result+manifest, but their local
    # completion/migration metadata is not manifest-bound. Upgrade them through gate.
    if _looks_like_v293_seal(seal):
        errors = _v293_seal_errors(seal, result, manifest)
        if errors:
            return SEAL_STATE_ERROR, errors, seal
        return SEAL_STATE_LEGACY, [], seal

    # Native v2.9.4 seals bind their descriptive metadata through the manifest,
    # but v2.9.5 adds lifecycle/genesis authority and therefore migrates them.
    if _looks_like_v294_seal(seal):
        errors = _v294_seal_errors(seal, result, manifest)
        if errors:
            return SEAL_STATE_ERROR, errors, seal
        return SEAL_STATE_LEGACY, [], seal

    errors = validate_schema(seal, load_schema("audit-seal.schema.json"), "audit-seal")
    errors.extend(_producer_errors(("audit-result", result), ("result-manifest", manifest), ("audit-seal", seal)))
    audit = result.get("audit", {}) if isinstance(result.get("audit"), dict) else {}
    if seal.get("audit_id") != audit.get("audit_id"):
        errors.append("audit seal audit_id does not match audit-result")
    if seal.get("audit_mode") != audit.get("mode"):
        errors.append("audit seal audit_mode does not match audit-result")
    if gate != "PASSED":
        errors.append("audit seal exists but audit-result gate is not PASSED")
    subjects = seal.get("subjects", {}) if isinstance(seal.get("subjects"), dict) else {}
    try:
        if subjects.get("audit_result_jcs_sha256") != canonical_sha256(result):
            errors.append("audit seal result digest mismatch")
        if subjects.get("result_manifest_jcs_sha256") != canonical_sha256(manifest):
            errors.append("audit seal manifest digest mismatch")
    except ValueError as exc:
        errors.append(f"audit seal canonical hashing failed: {exc}")

    seal_metadata = _seal_metadata_from_seal(seal)
    errors.extend(_seal_metadata_errors(seal_metadata, "audit-seal metadata"))
    manifest_metadata = manifest.get("seal_metadata")
    errors.extend(_seal_metadata_errors(manifest_metadata, "result-manifest.seal_metadata"))
    if isinstance(manifest_metadata, dict) and seal_metadata != manifest_metadata:
        errors.append("audit seal metadata does not match result-manifest seal_metadata")
    if errors:
        return SEAL_STATE_ERROR, errors, seal

    seal_producer = seal.get("producer", {}) if isinstance(seal.get("producer"), dict) else {}
    if seal_producer.get("version") in LEGACY_PRODUCER_VERSIONS:
        # Historical strong seals are migration inputs only; current v2.9.5 metadata
        # binding is required before a seal is accepted as authoritative.
        return SEAL_STATE_LEGACY, [], seal
    if seal_producer.get("version") != RFF_VERSION:
        return SEAL_STATE_ERROR, [f"digest-bound audit seal producer must be {RFF_VERSION}"], seal
    return SEAL_STATE_SEALED, [], seal


def _legacy_migration_context(paths: dict[str, Path]) -> dict[str, Any] | None:
    """Return migration metadata for a validated legacy seal/marker, if present."""
    if not paths["complete"].exists() or not paths["result"].exists() or not paths["manifest"].exists():
        return None
    try:
        result = load_json(paths["result"])
        manifest = load_json(paths["manifest"])
    except SystemExit:
        return None
    if not isinstance(result, dict) or not isinstance(manifest, dict):
        return None
    state, errors, seal = _seal_state(paths, result, manifest)
    if state != SEAL_STATE_LEGACY or errors or not isinstance(seal, dict):
        return None
    producer = result.get("producer", {}) if isinstance(result.get("producer"), dict) else {}
    prior_migration = None
    if _looks_like_legacy_completion_marker(seal):
        from_format = "legacy-completion-marker"
        provenance = "legacy-marker-unverified"
    elif _looks_like_legacy_digest_seal(seal):
        from_format = "digest-bound-pre-v2.9.3-seal"
        provenance = "pre-v2.9.3-seal-unbound"
    elif _looks_like_v293_seal(seal):
        from_format = "v2.9.3-digest-seal"
        provenance = "v2.9.3-seal-unbound"
        if isinstance(seal.get("migration"), dict):
            prior_migration = dict(seal["migration"])
    elif _looks_like_v294_seal(seal):
        from_format = "v2.9.4-manifest-bound-seal"
        provenance = seal.get("completion_time_provenance")
        if provenance == "v2.9.4-gate":
            provenance = "v2.9.4-seal-bound"
        if isinstance(seal.get("migration"), dict):
            prior_migration = dict(seal["migration"])
    else:
        return None
    migration = {
        "from_producer_version": producer.get("version"),
        "from_format": from_format,
    }
    if prior_migration is not None:
        migration["prior_migration"] = prior_migration
    return {
        "completed_at_utc": seal.get("completed_at_utc"),
        "completion_time_provenance": provenance,
        "migration": migration,
    }

def _commit_gate_files(
    paths: dict[str, Path],
    result: dict[str, Any],
    manifest: dict[str, Any],
    seal: dict[str, Any],
    *,
    genesis: dict[str, Any] | None = None,
) -> None:
    """Seal-last multi-file commit with an explicit pre-seal durability barrier."""
    preseal_payloads: list[tuple[Path, str]] = []
    if genesis is not None:
        preseal_payloads.append((
            paths["genesis"],
            json.dumps(genesis, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        ))
    preseal_payloads.extend([
        (paths["result"], json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"),
        (paths["manifest"], json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"),
    ])
    seal_payload = (paths["complete"], json.dumps(seal, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temps: list[tuple[Path, Path]] = []
    try:
        for dest, text in [*preseal_payloads, seal_payload]:
            temps.append((dest, _stage_text(dest, text)))

        # First make all data/provenance artifacts durably named. A crash here is
        # an explicitly uncommitted partial state and must have no current seal.
        for dest, tmp in temps[:-1]:
            os.replace(tmp, dest)
        _fsync_directory(paths["dir"])

        # The seal is the logical commit record and is installed only after the
        # pre-seal directory entries have crossed a durability barrier.
        seal_dest, seal_tmp = temps[-1]
        os.replace(seal_tmp, seal_dest)
        _fsync_directory(paths["dir"])
    finally:
        for _dest, tmp in temps:
            if tmp.exists():
                tmp.unlink()



def bytes_sha256(path: Path) -> str | None:
    return file_sha256(path)


def _finding_fingerprint(feature_id: str, start: dict[str, Any], finish: dict[str, Any]) -> str:
    semantic = {
        "feature_id": feature_id,
        "contract_relation": start.get("contract_relation"),
        "entry_point": start.get("entry_point"),
        "probe_intent": start.get("probe_intent"),
        "failure_pattern": finish.get("failure_pattern"),
    }
    return "sha256:" + canonical_sha256(semantic)


def _result_semantic_errors(result: dict[str, Any]) -> list[str]:
    """Validate derived/cross-field truths that JSON Schema cannot express cleanly."""
    errors: list[str] = []
    features = result.get("features", [])
    findings = result.get("findings", [])
    hypotheses = result.get("hypotheses", [])

    feature_ids = [f.get("feature_id") for f in features]
    if len(feature_ids) != len(set(feature_ids)):
        errors.append("audit-result semantic invariant failed: duplicate feature_id")

    expected_counts = {
        key: sum(1 for feature in features if feature.get("verdict") == key)
        for key in ("FALSIFIED", "NOT_FALSIFIED", "BLOCKED", "INCONCLUSIVE", "INCOMPLETE")
    }
    if result.get("verdict_counts") != expected_counts:
        errors.append("audit-result semantic invariant failed: verdict_counts does not equal features[].verdict tally")

    finding_ids = [f.get("finding_id") for f in findings]
    if len(finding_ids) != len(set(finding_ids)):
        errors.append("audit-result semantic invariant failed: duplicate finding_id")

    known_features = set(feature_ids)
    by_feature: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        feature_id = finding.get("feature_id")
        if feature_id not in known_features:
            errors.append(f"audit-result semantic invariant failed: finding references unknown feature_id {feature_id!r}")
        by_feature[feature_id].append(finding.get("finding_id"))

    for feature in features:
        feature_id = feature.get("feature_id")
        expected_ids = by_feature.get(feature_id, [])
        if feature.get("finding_ids") != expected_ids:
            errors.append(f"audit-result semantic invariant failed: {feature_id} finding_ids projection does not match findings")
        if feature.get("verdict") == "FALSIFIED" and not expected_ids:
            errors.append(f"audit-result semantic invariant failed: FALSIFIED feature {feature_id} has no finding")
        if feature.get("verdict") != "FALSIFIED" and expected_ids:
            errors.append(f"audit-result semantic invariant failed: non-FALSIFIED feature {feature_id} has falsification findings")

    coverage = result.get("coverage", {})
    if coverage.get("planned") != len(features):
        errors.append("audit-result semantic invariant failed: coverage.planned does not equal feature count")
    if coverage.get("audited") != sum(1 for f in features if f.get("probe_summary", {}).get("terminal_attempts", 0) > 0):
        errors.append("audit-result semantic invariant failed: coverage.audited does not equal audited feature count")
    if coverage.get("completed_required_probes", 0) > coverage.get("required_probes", 0):
        errors.append("audit-result semantic invariant failed: completed required probes exceed required probes")

    for hypothesis in hypotheses:
        expected_remaining = max(0, hypothesis.get("max_attempts", 0) - hypothesis.get("attempts_used", 0))
        if hypothesis.get("attempts_remaining") != expected_remaining:
            errors.append(f"audit-result semantic invariant failed: {hypothesis.get('hypothesis_id')} attempts_remaining is inconsistent")

    return errors


def _result_contract_errors(result: dict[str, Any]) -> list[str]:
    """Validate canonical syntax, full Draft-7 structure, and RFF semantics."""
    errors = validate_schema(result, load_schema("audit-result.schema.json"), "audit-result")
    try:
        jcs_bytes(result)
    except ValueError as exc:
        errors.append(str(exc))
    if not errors:
        errors.extend(_result_semantic_errors(result))
    return errors


def build_structured_result(paths: dict[str, Path], plan: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    investigation = investigation_summary(paths)
    summary = apply_investigation_verdicts(derive_summary(plan, events), investigation)
    attempts, _ = fold_attempts(events)
    terminal_by_feature: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for pair in attempts.values():
        if pair.get("start") and pair.get("finish"):
            terminal_by_feature[pair["finish"].get("feature_id", "")].append((pair["start"], pair["finish"]))

    findings: list[dict[str, Any]] = []
    for feature in sorted(summary["features"], key=lambda x: x["feature_id"]):
        for start, finish in terminal_by_feature.get(feature["feature_id"], []):
            if finish.get("result") != "FALSIFIED":
                continue
            pattern = finish.get("failure_pattern", "UNKNOWN_PATTERN")
            findings.append({
                "finding_id": "",  # assigned deterministically after sort
                "fingerprint": _finding_fingerprint(feature["feature_id"], start, finish),
                "feature_id": feature["feature_id"],
                "title": f"{feature['feature_id']} falsified by {finish.get('probe_id','runtime probe')}",
                "verdict": "FALSIFIED",
                "implementation_pattern": pattern,
                "severity": finish.get("severity", "MEDIUM"),
                "confidence": finish.get("confidence", "MEDIUM"),
                "claim": feature.get("claim", ""),
                "expected": start.get("expected", ""),
                "observed": finish.get("observed", ""),
                "reproduction": {
                    "command": start.get("repro_command") or "",
                    "entry_point": start.get("entry_point") or "",
                    "probe_id": finish.get("probe_id") or "",
                    "repeatable": bool(start.get("repro_command")),
                },
                "evidence": [{
                    "path": ref,
                    "sha256": file_sha256((Path(ref) if Path(ref).is_absolute() else paths["dir"] / ref)),
                    "kind": "runtime_evidence",
                } for ref in (finish.get("evidence_refs") or [])],
                "hypothesis_id": finish.get("hypothesis_id") or start.get("hypothesis_id"),
            })
    findings.sort(key=lambda x: (x["feature_id"], x["fingerprint"], x["reproduction"]["probe_id"]))
    for idx, finding in enumerate(findings, 1):
        finding["finding_id"] = f"RFF-{idx:03d}"

    counts = Counter(f["verdict"] for f in summary["features"])
    required_probe_count = sum(1 for f in plan.get("features", []) for p in f.get("probes", []) if p.get("required", True))
    completed_probe_ids = {pair["finish"].get("probe_id") for pair in attempts.values() if pair.get("finish")}
    completed_required = sum(1 for f in plan.get("features", []) for p in f.get("probes", []) if p.get("required", True) and p.get("probe_id") in completed_probe_ids)
    inv_count = in_scope_count = excluded_count = unmapped_count = 0
    if plan.get("audit_mode", "FEATURE") == "SYSTEM" and paths["inventory"].exists():
        inv = load_json(paths["inventory"])
        items = [x for x in inv.get("features", []) if isinstance(x, dict)]
        inv_count = len(items)
        in_scope = [x for x in items if x.get("disposition") == "IN_SCOPE"]
        excluded = [x for x in items if x.get("disposition") == "EXCLUDED"]
        plan_ids = {f.get("feature_id") for f in plan.get("features", []) if isinstance(f, dict)}
        inv_count, in_scope_count, excluded_count = len(items), len(in_scope), len(excluded)
        unmapped_count = sum(1 for x in in_scope if x.get("feature_id") not in plan_ids)

    preflight: dict[str, Any] = {}
    for intent in ("environment_start", "runtime_identity", "environment_collision"):
        finishes = [pair["finish"] for pair in attempts.values() if pair.get("start", {}).get("probe_intent") == intent and pair.get("finish")]
        preflight[intent] = {
            "status": "SURVIVED" if any(x.get("result") == "SURVIVED" for x in finishes) else (finishes[-1].get("result") if finishes else "MISSING"),
            "attempt_count": len(finishes),
        }

    timestamps = [e.get("timestamp_utc") for e in events if e.get("timestamp_utc")]
    target = plan.get("target", {})
    baseline = load_json(paths["baseline"]) if paths["baseline"].exists() else {}
    result = {
        "schema_version": "1.0.0",
        "producer": {"name": "runtime-feature-falsifier", "version": RFF_VERSION},
        "audit": {
            "audit_id": plan.get("audit_id"),
            "mode": plan.get("audit_mode", "FEATURE"),
            "status": "COMPLETE" if not summary.get("open_attempts") and not any(f["verdict"] == "INCOMPLETE" for f in summary["features"]) and investigation.get("active_count", 0) == 0 else "INCOMPLETE",
            "gate": "PENDING",
            "started_at": min(timestamps) if timestamps else None,
            "finished_at": max(timestamps) if timestamps else None,
        },
        "target": {
            "project": target.get("project", ""),
            "environment": target.get("environment", ""),
            "scope": target.get("scope", ""),
            "startup_path": target.get("startup_path", ""),
            "runtime_identity_expectation": target.get("runtime_identity_expectation", ""),
            "git_commit": baseline.get("head") if baseline.get("available") else None,
            "output_root": target.get("rff_output_root"),
            "run_id": target.get("rff_run_id") or plan.get("audit_id"),
            "path_warnings": target.get("rff_path_warnings", []),
        },
        "preflight": preflight,
        "coverage": {
            "discovered": inv_count if plan.get("audit_mode", "FEATURE") == "SYSTEM" else len(plan.get("features", [])),
            "in_scope": in_scope_count if plan.get("audit_mode", "FEATURE") == "SYSTEM" else len(plan.get("features", [])),
            "planned": len(plan.get("features", [])),
            "audited": sum(1 for f in summary["features"] if f["terminal_attempts"] > 0),
            "excluded": excluded_count,
            "unmapped": unmapped_count,
            "required_probes": required_probe_count,
            "completed_required_probes": completed_required,
        },
        "verdict_counts": {k: counts.get(k, 0) for k in ("FALSIFIED", "NOT_FALSIFIED", "BLOCKED", "INCONCLUSIVE", "INCOMPLETE")},
        "features": [{
            "feature_id": f["feature_id"],
            "claim": f["claim"],
            "verdict": f["verdict"],
            "implementation_patterns": f["patterns"],
            "probe_summary": {"terminal_attempts": f["terminal_attempts"], "required_missing": f["required_missing"]},
            "finding_ids": [x["finding_id"] for x in findings if x["feature_id"] == f["feature_id"]],
        } for f in summary["features"]],
        "findings": findings,
        "hypotheses": investigation.get("hypotheses", []),
        "integrity": {
            "attempt_chain_head": chain_heads(paths)["attempts"],
            "hypothesis_chain_head": chain_heads(paths)["hypotheses"],
            "plan_sha256": file_sha256(paths["plan"]),
            "inventory_sha256": file_sha256(paths["inventory"]) if paths["inventory"].exists() else None,
            "canonicalization": "RFC8785-JCS-float-free-profile",
        },
        "artifacts": {
            "findings": "findings.json",
            "summary": "audit-summary.md",
            "report": "audit-report.md",
            "feature_matrix": "feature-matrix.md",
            "system_coverage": "system-coverage.md" if plan.get("audit_mode", "FEATURE") == "SYSTEM" else None,
            "manifest": "result-manifest.json",
        },
    }
    return result, findings


def write_structured_outputs(paths: dict[str, Path], plan: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    result, findings = build_structured_result(paths, plan, events)
    errors = _result_contract_errors(result)
    if errors:
        raise SystemExit("invalid generated audit-result: " + "; ".join(errors))
    paths["result"].write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    findings_doc = {"schema_version": "1.0.0", "audit_id": result["audit"]["audit_id"], "findings": findings}
    findings_errors = validate_schema(findings_doc, load_schema("findings.schema.json"), "findings")
    if findings_errors:
        raise SystemExit("invalid generated findings projection: " + "; ".join(findings_errors))
    paths["findings"].write_text(json.dumps(findings_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    vc = result["verdict_counts"]
    cov = result["coverage"]
    summary_lines = [
        "# Runtime Feature Falsifier — Audit Summary", "",
        f"- Audit: `{result['audit']['audit_id']}`",
        f"- Mode: {result['audit']['mode']}",
        f"- Status: **{result['audit']['status']}**", "",
        "## Coverage", "",
        f"- Features audited/planned: {cov['audited']}/{cov['planned']}",
        f"- Required probes completed: {cov['completed_required_probes']}/{cov['required_probes']}", "",
        "## Verdicts", "",
        f"- FALSIFIED: {vc['FALSIFIED']}",
        f"- NOT_FALSIFIED: {vc['NOT_FALSIFIED']}",
        f"- BLOCKED: {vc['BLOCKED']}",
        f"- INCONCLUSIVE: {vc['INCONCLUSIVE']}",
        f"- INCOMPLETE: {vc['INCOMPLETE']}", "",
        "> `NOT_FALSIFIED` means no counterexample was found under the declared probe matrix; it is not proof of correctness or production readiness.", "",
    ]
    if findings:
        summary_lines += ["## Highest-impact findings", ""]
        rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for f in sorted(findings, key=lambda x: (rank.get(x["severity"], 9), x["finding_id"]))[:10]:
            summary_lines.append(f"- `{f['finding_id']}` [{f['severity']}] `{f['implementation_pattern']}` — {f['title']}")
        summary_lines.append("")
    paths["summary_md"].write_text("\n".join(summary_lines), encoding="utf-8")
    return result


def _expected_manifest_inputs(
    paths: dict[str, Path],
    *,
    genesis_override: dict[str, Any] | None = None,
    baseline_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    heads = chain_heads(paths)
    genesis = genesis_override if genesis_override is not None else (load_json(paths["genesis"]) if paths["genesis"].exists() else None)
    baseline = baseline_override if baseline_override is not None else (load_json(paths["baseline"]) if paths["baseline"].exists() else None)
    return {
        "plan_sha256": file_sha256(paths["plan"]),
        "inventory_sha256": file_sha256(paths["inventory"]) if paths["inventory"].exists() else None,
        "attempt_chain_head": heads["attempts"],
        "hypothesis_chain_head": heads["hypotheses"],
        "audit_genesis_jcs_sha256": _genesis_digest(genesis) if isinstance(genesis, dict) else None,
        "tracked_source_baseline_jcs_sha256": _baseline_digest(baseline) if isinstance(genesis, dict) and isinstance(baseline, dict) else None,
        "tracked_source_snapshot_format": baseline.get("snapshot_format", "legacy-content-only") if isinstance(genesis, dict) and isinstance(baseline, dict) else None,
    }


def _expected_manifest_outputs(paths: dict[str, Path], result: dict[str, Any], findings_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_result_jcs_sha256": canonical_sha256(result),
        "findings_jcs_sha256": canonical_sha256(findings_doc),
        "audit_summary_sha256": bytes_sha256(paths["summary_md"]),
        "audit_report_sha256": bytes_sha256(paths["report"]),
        "feature_matrix_sha256": bytes_sha256(paths["matrix"]),
        "system_coverage_sha256": bytes_sha256(paths["coverage"]) if paths["coverage"].exists() else None,
    }


def _top_level_mismatch_fields(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    return sorted(key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key))


def write_result_manifest(paths: dict[str, Path], result: dict[str, Any]) -> dict[str, Any]:
    findings_doc = load_json(paths["findings"])
    manifest = {
        "schema_version": "1.0.0",
        "producer": {"name": "runtime-feature-falsifier", "version": RFF_VERSION},
        "audit_id": result["audit"]["audit_id"],
        "canonicalization": "RFC8785-JCS-float-free-profile",
        "inputs": _expected_manifest_inputs(paths),
        "outputs": _expected_manifest_outputs(paths, result, findings_doc),
    }
    manifest_errors = validate_schema(manifest, load_schema("result-manifest.schema.json"), "result-manifest")
    if manifest_errors:
        raise SystemExit("invalid generated result manifest: " + "; ".join(manifest_errors))
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _partial_migration_metadata_from_legacy_seal(
    seal: dict[str, Any], manifest_metadata: dict[str, Any]
) -> dict[str, Any] | None:
    """Reconstruct metadata staged before a seal-last migration crash."""
    migration = manifest_metadata.get("migration") if isinstance(manifest_metadata.get("migration"), dict) else None
    if _looks_like_legacy_completion_marker(seal):
        if not migration or migration.get("from_producer_version") not in {"2.9.0", "2.9.1", "2.9.2"}:
            return None
        expected_migration = {
            "from_producer_version": migration.get("from_producer_version"),
            "from_format": "legacy-completion-marker",
        }
        provenance = "legacy-marker-unverified"
    elif _looks_like_legacy_digest_seal(seal):
        producer = seal.get("producer", {}) if isinstance(seal.get("producer"), dict) else {}
        if producer.get("version") not in {"2.9.0", "2.9.1", "2.9.2"}:
            return None
        expected_migration = {
            "from_producer_version": producer.get("version"),
            "from_format": "digest-bound-pre-v2.9.3-seal",
        }
        provenance = "pre-v2.9.3-seal-unbound"
    elif _looks_like_v293_seal(seal):
        expected_migration = {
            "from_producer_version": "2.9.3",
            "from_format": "v2.9.3-digest-seal",
        }
        if isinstance(seal.get("migration"), dict):
            expected_migration["prior_migration"] = dict(seal["migration"])
        provenance = "v2.9.3-seal-unbound"
    elif _looks_like_v294_seal(seal):
        expected_migration = {
            "from_producer_version": "2.9.4",
            "from_format": "v2.9.4-manifest-bound-seal",
        }
        if isinstance(seal.get("migration"), dict):
            expected_migration["prior_migration"] = dict(seal["migration"])
        provenance = seal.get("completion_time_provenance")
        if provenance == "v2.9.4-gate":
            provenance = "v2.9.4-seal-bound"
    else:
        return None
    try:
        return _build_seal_metadata(
            completed_at=seal.get("completed_at_utc"),
            sealed_at=manifest_metadata.get("sealed_at_utc"),
            migration=expected_migration,
            completion_time_provenance=provenance,
        )
    except ValueError:
        return None


def _partial_migration_commit_context(paths: dict[str, Path]) -> dict[str, Any] | None:
    """Recognize, but never commit, a migration interrupted before the seal rename.

    This function is deliberately read-only. Gate may use the returned context to
    tolerate the stale legacy seal while it completes *all* final checks. Only the
    final successful gate path is allowed to install the current seal.
    """
    if not all(paths[k].exists() for k in ("complete", "result", "manifest", "findings", "plan")):
        return None
    try:
        result = load_json(paths["result"])
        manifest = load_json(paths["manifest"])
        findings_doc = load_json(paths["findings"])
        legacy_seal = load_json(paths["complete"])
    except SystemExit:
        return None
    if not all(isinstance(x, dict) for x in (result, manifest, findings_doc, legacy_seal)):
        return None
    producer = result.get("producer", {}) if isinstance(result.get("producer"), dict) else {}
    audit = result.get("audit", {}) if isinstance(result.get("audit"), dict) else {}
    metadata = manifest.get("seal_metadata") if isinstance(manifest.get("seal_metadata"), dict) else None
    if producer.get("version") != RFF_VERSION or audit.get("gate") != "PASSED" or metadata is None:
        return None
    if manifest.get("producer") != result.get("producer") or manifest.get("audit_id") != audit.get("audit_id"):
        return None
    if legacy_seal.get("audit_id") != audit.get("audit_id") or legacy_seal.get("audit_mode") != audit.get("mode"):
        return None
    expected_metadata = _partial_migration_metadata_from_legacy_seal(legacy_seal, metadata)
    if expected_metadata is None or metadata != expected_metadata:
        return None
    if _result_contract_errors(result):
        return None
    if validate_schema(manifest, load_schema("result-manifest.schema.json"), "result-manifest"):
        return None
    if _seal_metadata_errors(metadata, "result-manifest.seal_metadata"):
        return None
    try:
        plan = load_json(paths["plan"])
        events = read_events(paths["log"])
        expected_result, _ = build_structured_result(paths, plan, events)
        expected_result["producer"] = {"name": "runtime-feature-falsifier", "version": RFF_VERSION}
        expected_result["audit"]["gate"] = "PASSED"
        if expected_result != result:
            return None
        if manifest.get("inputs") != _expected_manifest_inputs(paths):
            return None
        if manifest.get("outputs") != _expected_manifest_outputs(paths, result, findings_doc):
            return None
    except (SystemExit, ValueError):
        return None
    return {
        "completed_at_utc": metadata.get("completed_at_utc"),
        "completion_time_provenance": metadata.get("completion_time_provenance"),
        "migration": metadata.get("migration"),
        "seal_metadata": dict(metadata),
    }



def validate_structured_outputs(
    paths: dict[str, Path],
    *,
    allow_unsealed_passed: bool = False,
    allow_legacy_seal_migration: bool = False,
    allow_partial_migration_commit: bool = False,
) -> list[str]:
    """Validate stored results against schemas, semantics, and live audit provenance."""
    errors: list[str] = []
    for key in ("result", "findings", "summary_md", "manifest"):
        if not paths[key].exists():
            errors.append(f"{paths[key].name} missing; run rff audit report")
    if errors:
        return errors
    try:
        result = load_json(paths["result"])
        findings_doc = load_json(paths["findings"])
        manifest = load_json(paths["manifest"])
    except SystemExit as exc:
        return [str(exc)]

    if not isinstance(result, dict) or not isinstance(findings_doc, dict) or not isinstance(manifest, dict):
        return ["canonical result, findings, and manifest roots must be JSON objects"]

    contract_errors = _result_contract_errors(result)
    errors.extend(contract_errors)
    errors.extend(validate_schema(findings_doc, load_schema("findings.schema.json"), "findings"))
    errors.extend(validate_schema(manifest, load_schema("result-manifest.schema.json"), "result-manifest"))
    errors.extend(_producer_errors(("audit-result", result), ("result-manifest", manifest)))
    seal_state, seal_errors, _seal = _seal_state(paths, result, manifest)
    unsealed_passed_recovery = (
        allow_unsealed_passed
        and not paths["complete"].exists()
        and isinstance(result.get("audit"), dict)
        and result["audit"].get("gate") == "PASSED"
    )
    partial_migration_context = (
        _partial_migration_commit_context(paths) if allow_partial_migration_commit else None
    )
    partial_migration_recovery = partial_migration_context is not None
    if seal_state == SEAL_STATE_LEGACY:
        if not allow_legacy_seal_migration:
            errors.append(LEGACY_MIGRATION_MESSAGE)
    elif not unsealed_passed_recovery and not partial_migration_recovery:
        errors.extend(seal_errors)

    result_audit_id = result.get("audit", {}).get("audit_id") if isinstance(result.get("audit"), dict) else None
    if findings_doc.get("audit_id") != result_audit_id:
        errors.append("findings.json audit_id does not match audit-result.json")
    if manifest.get("audit_id") != result_audit_id:
        errors.append("result-manifest audit_id does not match audit-result.json")
    if result.get("findings") != findings_doc.get("findings"):
        errors.append("findings.json does not match audit-result.json findings projection")

    # Re-derive the canonical result from the actual plan + append-only ledgers.
    # Stored projections are non-authoritative. The gate value is also derived:
    # PENDING requires no seal; PASSED requires a valid digest-bound seal.
    if not contract_errors:
        try:
            plan = load_json(paths["plan"])
            events = read_events(paths["log"])
            expected_result, _ = build_structured_result(paths, plan, events)
            expected_result["producer"] = result.get("producer")  # explicitly compatible 2.9 patch artifacts
            expected_result["audit"]["gate"] = (
                "PASSED"
                if seal_state in {SEAL_STATE_SEALED, SEAL_STATE_LEGACY} or unsealed_passed_recovery or partial_migration_recovery
                else "PENDING"
            )
            if result != expected_result:
                changed = _top_level_mismatch_fields(result, expected_result)
                errors.append(
                    "audit-result does not match authoritative audit state"
                    + (" (changed: " + ", ".join(changed) + ")" if changed else "")
                )
        except SystemExit as exc:
            errors.append(f"could not re-derive authoritative audit state: {exc}")

    inputs = manifest.get("inputs", {}) if isinstance(manifest.get("inputs"), dict) else {}
    expected_inputs = _expected_manifest_inputs(paths)
    result_producer = result.get("producer", {}) if isinstance(result.get("producer"), dict) else {}
    if result_producer.get("version") == RFF_VERSION:
        errors.extend(_genesis_integrity_errors(paths))
        for required_key in (
            "audit_genesis_jcs_sha256",
            "tracked_source_baseline_jcs_sha256",
            "tracked_source_snapshot_format",
        ):
            if required_key not in inputs or expected_inputs.get(required_key) is None:
                errors.append(f"current result-manifest missing authoritative input provenance: {required_key}")
    for key, value in expected_inputs.items():
        if inputs.get(key) != value:
            errors.append(f"result-manifest input provenance mismatch: {key}")

    outputs = manifest.get("outputs", {}) if isinstance(manifest.get("outputs"), dict) else {}
    try:
        expected_outputs = _expected_manifest_outputs(paths, result, findings_doc)
    except ValueError as exc:
        errors.append(f"canonical output hashing failed: {exc}")
        expected_outputs = {}
    for key, value in expected_outputs.items():
        if unsealed_passed_recovery and key == "audit_result_jcs_sha256":
            # A crash after result promotion but before manifest/seal may leave only
            # this digest stale. Do not mutate it here; the final successful gate
            # rebuilds the manifest before committing the seal.
            continue
        if outputs.get(key) != value:
            errors.append(f"result-manifest output digest mismatch: {key}")
    return errors

@active_mutation_command
def cmd_report(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    plan = load_json(paths["plan"])
    events = read_events(paths["log"])
    investigation = investigation_summary(paths)
    summary = apply_investigation_verdicts(derive_summary(plan, events), investigation)
    attempts, _ = fold_attempts(events)
    terminal_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in attempts.values():
        if pair.get("finish"):
            terminal_by_feature[pair["finish"].get("feature_id", "")].append(pair["finish"])

    matrix_lines = [
        "# Runtime Feature Matrix", "",
        "| Feature | Attempts | Verdict | Patterns | Strongest counterexample |",
        "|---|---:|---|---|---|",
    ]
    for f in summary["features"]:
        rows = terminal_by_feature.get(f["feature_id"], [])
        falsified = next((r for r in rows if r.get("result") == "FALSIFIED"), None)
        strongest = ""
        if falsified:
            strongest = f"{falsified.get('probe_id')}: {falsified.get('observed','')}"
        matrix_lines.append(f"| {f['feature_id']} | {f['terminal_attempts']} | {f['verdict']} | {', '.join(f['patterns']) or 'NONE_OBSERVED'} | {strongest.replace('|','/')} |")
    paths["matrix"].write_text("\n".join(matrix_lines) + "\n", encoding="utf-8")

    counts = Counter(f["verdict"] for f in summary["features"])
    system_result = validate_system_files(paths, plan)
    system_lines: list[str] = []
    if plan.get("audit_mode", "FEATURE") == "SYSTEM" and paths["inventory"].exists():
        inv = load_json(paths["inventory"])
        inv_features = inv.get("features", []) if isinstance(inv, dict) else []
        in_scope = [x for x in inv_features if isinstance(x, dict) and x.get("disposition") == "IN_SCOPE"]
        excluded = [x for x in inv_features if isinstance(x, dict) and x.get("disposition") == "EXCLUDED"]
        plan_ids = {f.get("feature_id") for f in plan.get("features", []) if isinstance(f, dict)}
        unmapped = [x.get("feature_id") for x in in_scope if x.get("feature_id") not in plan_ids]
        system_lines = [
            "# System Audit Coverage", "",
            f"- Discovery status: **{inv.get('discovery_status','')}**",
            f"- Discovered capabilities/workflows: {len(inv_features)}",
            f"- In scope: {len(in_scope)}",
            f"- Explicitly excluded: {len(excluded)}",
            f"- Planned: {len(plan.get('features', []))}",
            f"- Unmapped in-scope items: {len(unmapped)}",
            f"- Cross-feature workflow coverage applicable: {bool(inv.get('workflow_coverage',{}).get('applicable'))}",
            "",
        ]
        blockers = inv.get("discovery_blockers") or []
        if blockers:
            system_lines += ["## Discovery blockers", ""] + [f"- {b}" for b in blockers] + [""]
        if excluded:
            system_lines += ["## Explicit exclusions", ""] + [f"- `{x.get('feature_id')}` [{x.get('exclusion_basis','')}] — {x.get('exclusion_reason','')}" for x in excluded] + [""]
        if unmapped:
            system_lines += ["## Unmapped in-scope features", ""] + [f"- `{x}`" for x in unmapped] + [""]
        paths["coverage"].write_text("\n".join(system_lines), encoding="utf-8")
    report = [
        "# Runtime Feature Falsification Report", "",
        "## Scope", "",
        f"- Audit ID: `{plan.get('audit_id','')}`",
        f"- Project: {plan.get('target',{}).get('project','')}",
        f"- Environment: {plan.get('target',{}).get('environment','')}",
        f"- Scope: {plan.get('target',{}).get('scope','')}",
        f"- Audit mode: {plan.get('audit_mode','FEATURE')}", "",
        "## Executive result", "",
        f"- Features audited/planned: {len(summary['features'])}",
        f"- FALSIFIED: {counts.get('FALSIFIED',0)}",
        f"- NOT_FALSIFIED: {counts.get('NOT_FALSIFIED',0)}",
        f"- BLOCKED: {counts.get('BLOCKED',0)}",
        f"- INCONCLUSIVE: {counts.get('INCONCLUSIVE',0)}",
        f"- INCOMPLETE: {counts.get('INCOMPLETE',0)}", "",
        "> `NOT_FALSIFIED` means no counterexample was found in the completed declared matrix. It must not be rewritten as a verdict such as \"verified\", \"proven\", \"alive\", or \"fully working\"; it is not proof of correctness, completeness, or production readiness.", "",
        "## Runtime preflight", "",
        f"- Declared startup path: {plan.get('target',{}).get('startup_path','')}",
        f"- Expected runtime identity: {plan.get('target',{}).get('runtime_identity_expectation','')}",
        f"- Collision surfaces considered: {', '.join(plan.get('target',{}).get('collision_surfaces',[]) or []) or 'documented in environment_collision probe'}", "",
    ]
    if system_lines:
        report += ["## System discovery/coverage", ""] + system_lines[2:] + [""]
    report += [
        "## Feature results", "",
    ]
    for f in summary["features"]:
        report += [f"### {f['feature_id']}", "", f"- Claim: {f['claim']}", f"- Verdict: **{f['verdict']}**", f"- Terminal attempts: {f['terminal_attempts']}", f"- Patterns: {', '.join(f['patterns']) or 'NONE_OBSERVED'}"]
        for r in terminal_by_feature.get(f["feature_id"], []):
            if r.get("result") == "FALSIFIED":
                report += [f"- Counterexample `{r.get('probe_id')}`: {r.get('observed','')}", f"  - Pattern: {r.get('failure_pattern')}", f"  - Evidence: {', '.join(r.get('evidence_refs') or []) or '(no file evidence recorded)'}"]
        if f["required_missing"]:
            report += [f"- Missing required probes: {', '.join(f['required_missing'])}"]
        report.append("")
    inv = investigation
    report += ["## Persistent investigation", "", f"- Hypotheses opened: {inv.get('hypothesis_count',0)}", f"- Active hypotheses: {inv.get('active_count',0)}"]
    for h in inv.get("hypotheses", []):
        report += [f"- `{h.get('hypothesis_id')}` [{h.get('status')}] {h.get('statement')} — attempts {h.get('attempts_used')}/{h.get('max_attempts')}"]
    report += [""]
    report += ["## Attempt-log integrity", "", f"- Canonical event log: `attempts.jsonl`", f"- Events: {summary['event_count']}", f"- Attempts: {summary['attempt_count']}", f"- Open attempts: {', '.join(summary['open_attempts']) or 'none'}", ""]
    paths["report"].write_text("\n".join(report), encoding="utf-8")
    structured_result = write_structured_outputs(paths, plan, events)
    write_result_manifest(paths, structured_result)
    report_state = report_state_snapshot(paths, plan)
    report_state["generated_at_utc"] = utc_now()
    paths["report_state"].write_text(json.dumps(report_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = {
        "ok": True,
        "feature_matrix": str(paths["matrix"]),
        "audit_report": str(paths["report"]),
        "audit_summary": str(paths["summary_md"]),
        "audit_result": str(paths["result"]),
        "findings": str(paths["findings"]),
        "result_manifest": str(paths["manifest"]),
        "report_state": str(paths["report_state"]),
    }
    if paths["coverage"].exists():
        output["system_coverage"] = str(paths["coverage"])
    emit(with_heads(output, paths), args.format)
    return 0


@workspace_locked_command
def cmd_gate(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    errors: list[str] = []
    warnings: list[str] = []
    partial_migration_context: dict[str, Any] | None = None
    if not paths["plan"].exists():
        errors.append(f"missing audit plan: {paths['plan']}")
        emit({"ok": False, "errors": errors, "warnings": warnings}, args.format)
        return 2
    plan = load_json(paths["plan"])
    plan_result = validate_plan_obj(plan)
    errors.extend(plan_result["errors"])
    warnings.extend(plan_result["warnings"])
    system_result = validate_system_files(paths, plan)
    errors.extend(system_result.get("errors", []))
    warnings.extend(system_result.get("warnings", []))

    if not paths["complete"].exists():
        # An unsealed run may reach gate only while it is still the ACTIVE workspace.
        errors.extend(_workspace_state_errors(paths, require_active=True))
    elif paths["genesis"].exists():
        errors.extend(_genesis_integrity_errors(paths, plan))
    else:
        # Historical sealed audits predate the v2.9.5 genesis record. They may be
        # migrated, but the inherited baseline is explicitly marked unbound.
        try:
            existing_result = load_json(paths["result"])
        except SystemExit as exc:
            errors.append(str(exc))
        else:
            producer = existing_result.get("producer", {}) if isinstance(existing_result, dict) else {}
            if producer.get("version") == RFF_VERSION:
                errors.append("current sealed audit is missing audit-genesis.json")
            else:
                warnings.append("legacy audit has no genesis-bound tracked-source baseline; migration will bind the inherited baseline as unverified")

    if paths["baseline"].exists():
        baseline = load_json(paths["baseline"])
        if baseline.get("available"):
            errors.extend(compare_tracked_source(baseline))
        else:
            warnings.append("tracked-source mutation gate unavailable: " + baseline.get("reason", "unknown reason"))
    else:
        warnings.append("tracked-source baseline missing; source-mutation gate unavailable")
    events = read_events(paths["log"])
    if not events:
        errors.append("attempts.jsonl is missing or empty")
    errors.extend(verify_hash_chain(events))
    attempt_schema = load_schema("attempt-event.schema.json")
    for event in events:
        errors.extend(validate_schema({k: v for k, v in event.items() if k != "_line"}, attempt_schema, f"attempts.jsonl:{event.get('_line','?')}"))
    attempts, fold_errors = fold_attempts(events)
    errors.extend(fold_errors)

    hypothesis_events = read_events(paths["hypotheses"])
    hypotheses: dict[str, dict[str, Any]] = {}
    if hypothesis_events:
        errors.extend(verify_hash_chain(hypothesis_events))
        hypothesis_schema = load_schema("hypothesis-event.schema.json")
        for event in hypothesis_events:
            errors.extend(validate_schema({k: v for k, v in event.items() if k != "_line"}, hypothesis_schema, f"hypothesis-ledger.jsonl:{event.get('_line','?')}"))
        hypotheses, hypothesis_errors = fold_hypotheses(hypothesis_events)
        errors.extend(hypothesis_errors)
        h_attempt_counts = hypothesis_attempt_counts(events)
        # Build terminal-attempt lookup once for evidence coupling.
        linked_finishes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for pair in attempts.values():
            if pair.get("finish") and pair["finish"].get("hypothesis_id"):
                linked_finishes[pair["finish"]["hypothesis_id"]].append(pair["finish"])
        for hid, h in hypotheses.items():
            status = h.get("status")
            if status not in HYPOTHESIS_TERMINAL:
                errors.append(f"unresolved hypothesis at final gate: {hid} [{status}] — {h.get('statement')}")
            if status == "CONFIRMED":
                trigger = h.get("trigger_attempt_id")
                trigger_falsified = bool(trigger and attempts.get(trigger, {}).get("finish", {}).get("result") == "FALSIFIED")
                linked_falsified = any(x.get("result") == "FALSIFIED" for x in linked_finishes.get(hid, []))
                if not (trigger_falsified or linked_falsified):
                    errors.append(f"CONFIRMED hypothesis lacks a linked/trigger FALSIFIED runtime attempt: {hid}")
                if not h.get("evidence_for"):
                    errors.append(f"CONFIRMED hypothesis lacks evidence_for: {hid}")
            if status == "REFUTED" and not h.get("evidence_against"):
                errors.append(f"REFUTED hypothesis lacks evidence_against: {hid}")
            if status == "BUDGET_EXHAUSTED" and h_attempt_counts.get(hid, 0) < h.get("max_attempts", 0):
                errors.append(f"hypothesis marked BUDGET_EXHAUSTED before consuming declared budget: {hid}")

    # Runtime-entry evidence gates. These cannot cryptographically prove a public surface,
    # but they force reproducible startup, runtime-identity, and environment-isolation evidence.
    feature_by_id = {f.get("feature_id"): f for f in plan.get("features", []) if isinstance(f, dict)}
    finishes_by_intent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for aid, pair in attempts.items():
        start = pair.get("start") or {}
        finish = pair.get("finish") or {}
        feature = feature_by_id.get(start.get("feature_id"), {})
        intent = start.get("probe_intent")
        if intent in PREFLIGHT_INTENTS and finish:
            finishes_by_intent[intent].append(finish)
        if (feature.get("dependency_sensitive") or intent in PREFLIGHT_INTENTS) and not str(start.get("repro_command") or "").strip():
            errors.append(f"preflight/dependency-sensitive attempt lacks repro_command: {aid}")
        if intent in {"runtime_identity", "environment_collision"} and finish:
            if not (finish.get("evidence_refs") or finish.get("side_effect_check") or finish.get("persistence_check")):
                errors.append(f"{intent} attempt lacks corroborating evidence/effect observation: {aid}")

    startup_finishes = finishes_by_intent["environment_start"]
    identity_finishes = finishes_by_intent["runtime_identity"]
    collision_finishes = finishes_by_intent["environment_collision"]
    if not startup_finishes:
        errors.append("required environment_start probe never completed")
    elif not any(x.get("result") == "SURVIVED" for x in startup_finishes):
        non_startup_started = [pair for pair in attempts.values() if (pair.get("start") or {}).get("probe_intent") != "environment_start"]
        if non_startup_started:
            errors.append("non-startup probes were executed without a SURVIVED startup-health probe")
    if not identity_finishes:
        errors.append("required runtime_identity probe never completed")
    elif not any(x.get("result") == "SURVIVED" for x in identity_finishes):
        warnings.append("runtime identity did not SURVIVE; downstream probes characterize the deployed runtime and must not be described as proof of the intended source implementation")
    if not collision_finishes:
        errors.append("required environment_collision probe never completed")
    elif not any(x.get("result") == "SURVIVED" for x in collision_finishes):
        errors.append("audit environment collision/isolation did not SURVIVE; feature results may be contaminated by shared mutable resources")

    for aid, pair in attempts.items():
        start = pair.get("start") or {}
        if start.get("probe_intent") not in PREFLIGHT_INTENTS:
            if not startup_health_survived(events):
                errors.append(f"feature probe executed without healthy startup: {aid}")
            if not runtime_identity_completed(events):
                errors.append(f"feature probe executed before runtime identity was observed: {aid}")
            if not collision_check_survived(events):
                errors.append(f"feature probe executed before environment collision check SURVIVED: {aid}")

    # Ensure every STARTED attempt terminates. This catches crashes/timeouts that were never closed out.
    for aid, pair in attempts.items():
        if pair.get("start") and not pair.get("finish"):
            errors.append(f"open attempt without FINISHED event: {aid} ({pair['start'].get('probe_id')})")

    by_probe: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in attempts.values():
        if pair.get("finish"):
            by_probe[pair["finish"].get("probe_id", "")].append(pair["finish"])
    for feature in plan.get("features", []):
        for probe in feature.get("probes", []):
            if probe.get("required", True) and not by_probe.get(probe.get("probe_id", "")):
                errors.append(f"required probe never completed: {feature.get('feature_id')}/{probe.get('probe_id')}")

    # Evidence quality gates: fake-success claims require downstream observation, not status text alone.
    for pair in attempts.values():
        finish = pair.get("finish")
        start = pair.get("start") or {}
        if not finish:
            continue
        if finish.get("result") == "FALSIFIED":
            if not (finish.get("observed") and (finish.get("evidence_refs") or finish.get("side_effect_check") or finish.get("persistence_check"))):
                errors.append(f"falsified attempt lacks corroborating effect/evidence: {finish.get('attempt_id')}")
            if finish.get("failure_pattern") == "NONE_OBSERVED":
                errors.append(f"falsified attempt has NONE_OBSERVED pattern: {finish.get('attempt_id')}")
        if finish.get("result") == "SURVIVED" and start.get("effect_checks"):
            if not (finish.get("side_effect_check") or finish.get("persistence_check") or finish.get("evidence_refs")):
                errors.append(f"survived attempt with required effect_checks lacks downstream evidence: {finish.get('attempt_id')}")
        for ref in finish.get("evidence_refs") or []:
            ref_path = Path(ref)
            candidate = ref_path if ref_path.is_absolute() else paths["dir"] / ref_path
            if not candidate.exists():
                errors.append(f"evidence reference does not exist for {finish.get('attempt_id')}: {ref}")

    if args.require_report:
        if not paths["matrix"].exists():
            errors.append("feature-matrix.md missing; run auditctl report")
        if not paths["report"].exists():
            errors.append("audit-report.md missing; run auditctl report")
        if plan.get("audit_mode", "FEATURE") == "SYSTEM" and not paths["coverage"].exists():
            errors.append("system-coverage.md missing for SYSTEM audit; run auditctl report")
        if not paths["report_state"].exists():
            errors.append("report-state marker missing; run auditctl report after the final attempt/hypothesis update")
        else:
            recorded_state = load_json(paths["report_state"])
            current_state = report_state_snapshot(paths, plan)
            stale_fields = [k for k, v in current_state.items() if recorded_state.get(k) != v]
            if stale_fields:
                errors.append("generated report is stale relative to canonical audit state (changed: " + ", ".join(stale_fields) + "); rerun auditctl report")
        for report_path in (paths["report"], paths["matrix"], paths["coverage"]):
            if report_path.exists():
                errors.extend(reporting_language_errors(report_path.read_text(encoding="utf-8", errors="replace"), str(report_path)))
        if paths["summary_md"].exists():
            errors.extend(reporting_language_errors(paths["summary_md"].read_text(encoding="utf-8", errors="replace"), str(paths["summary_md"])))
        # Recovery detection is strictly read-only. A partial result/manifest
        # promotion may be tolerated during gate validation, but no repair helper
        # may install a seal or rewrite provenance before *all* final checks pass.
        partial_migration_context = _partial_migration_commit_context(paths)
        errors.extend(validate_structured_outputs(
            paths,
            allow_unsealed_passed=True,
            allow_legacy_seal_migration=True,
            allow_partial_migration_commit=partial_migration_context is not None,
        ))

    investigation = investigation_summary(paths)
    summary = apply_investigation_verdicts(derive_summary(plan, events), investigation)
    if any(f["verdict"] == "INCOMPLETE" for f in summary["features"]):
        errors.append("one or more features remain INCOMPLETE")

    ok = not errors
    if ok and args.require_report:
        existing_result = load_json(paths["result"])
        existing_manifest = load_json(paths["manifest"])
        existing_state, existing_state_errors, _existing_seal = _seal_state(paths, existing_result, existing_manifest)
        if existing_state == SEAL_STATE_SEALED and not existing_state_errors:
            # Gate is idempotent for an already-valid current seal; do not rewrite timestamps/provenance.
            if paths["active"].exists():
                paths["active"].unlink()
                _fsync_directory(paths["dir"])
        else:
            # Finalization is a seal-last commit. Result and manifest are prepared and
            # validated in memory; the digest-bound seal is the authoritative commit
            # record and is atomically installed last.
            migration_context = partial_migration_context or _legacy_migration_context(paths)
            gated_result = load_json(paths["result"])
            gated_result.setdefault("audit", {})["gate"] = "PASSED"
            # A successful v2.9.5 gate/migration emits unambiguous current producer metadata.
            gated_result["producer"] = {"name": "runtime-feature-falsifier", "version": RFF_VERSION}
            manifest = load_json(paths["manifest"])
            findings_doc = load_json(paths["findings"])
            staged_genesis: dict[str, Any] | None = None
            if not paths["genesis"].exists():
                if not migration_context:
                    errors.append("audit genesis record missing; current gate cannot establish authoritative source baseline")
                    ok = False
                else:
                    baseline = load_json(paths["baseline"])
                    active = load_json(paths["active"]) if paths["active"].exists() else {}
                    target_root = Path(active.get("target_root") or baseline.get("git_root") or paths["dir"].parent)
                    staged_genesis = _build_audit_genesis(
                        plan, target_root, baseline, provenance="legacy-baseline-unbound-at-migration"
                    )
                    genesis_errors = validate_schema(staged_genesis, load_schema("audit-genesis.schema.json"), "audit-genesis")
                    if genesis_errors:
                        errors.extend(genesis_errors)
                        ok = False
            gated_result_errors = _result_contract_errors(gated_result)
            if gated_result_errors:
                errors.extend(gated_result_errors)
                ok = False
            else:
                manifest["producer"] = dict(gated_result["producer"])
                prospective_genesis = staged_genesis if staged_genesis is not None else (load_json(paths["genesis"]) if paths["genesis"].exists() else None)
                manifest["inputs"] = _expected_manifest_inputs(
                    paths,
                    genesis_override=prospective_genesis if isinstance(prospective_genesis, dict) else None,
                )
                manifest["outputs"] = _expected_manifest_outputs(paths, gated_result, findings_doc)
                try:
                    if partial_migration_context and isinstance(partial_migration_context.get("seal_metadata"), dict):
                        manifest["seal_metadata"] = dict(partial_migration_context["seal_metadata"])
                    else:
                        manifest["seal_metadata"] = _build_seal_metadata(
                            completed_at=(migration_context or {}).get("completed_at_utc"),
                            migration=(migration_context or {}).get("migration"),
                            completion_time_provenance=(migration_context or {}).get(
                                "completion_time_provenance", "v2.9.5-gate"
                            ),
                        )
                except ValueError as exc:
                    errors.append(str(exc))
                    ok = False
                    manifest_errors = []
                    producer_errors = []
                else:
                    manifest_errors = validate_schema(manifest, load_schema("result-manifest.schema.json"), "result-manifest")
                    manifest_errors.extend(_seal_metadata_errors(manifest.get("seal_metadata"), "result-manifest.seal_metadata"))
                    producer_errors = _producer_errors(("audit-result", gated_result), ("result-manifest", manifest))
                if manifest_errors or producer_errors:
                    errors.extend(manifest_errors + producer_errors)
                    ok = False
                elif ok:
                    try:
                        seal = _build_audit_seal(paths, gated_result, manifest)
                    except ValueError as exc:
                        errors.append(str(exc))
                        ok = False
                    else:
                        seal_errors = validate_schema(seal, load_schema("audit-seal.schema.json"), "audit-seal")
                        seal_errors.extend(_seal_metadata_errors(_seal_metadata_from_seal(seal), "audit-seal metadata"))
                        seal_errors.extend(_producer_errors(("audit-result", gated_result), ("result-manifest", manifest), ("audit-seal", seal)))
                        if seal_errors:
                            errors.extend(seal_errors)
                            ok = False
                        else:
                            _commit_gate_files(paths, gated_result, manifest, seal, genesis=staged_genesis)
                            # Verify the committed bytes, not merely the in-memory objects.
                            committed_errors = validate_structured_outputs(paths)
                            if committed_errors:
                                errors.extend(committed_errors)
                                ok = False
                            elif paths["active"].exists():
                                paths["active"].unlink()
                                _fsync_directory(paths["dir"])

    result = {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "audit_id": plan.get("audit_id"),
        "audit_mode": plan.get("audit_mode", "FEATURE"),
        "system_inventory": {k: v for k, v in system_result.items() if k not in {"ok", "errors", "warnings", "applicable"}} if system_result.get("applicable") else None,
        "event_count": len(events),
        "attempt_count": len(attempts),
        "feature_verdicts": {f["feature_id"]: f["verdict"] for f in summary["features"]},
        "investigation": investigation,
        "next_run_policy": "If remediation follows this sealed audit, spawn a fresh auditor and initialize a new audit workspace for the next run.",
    }
    if result["ok"]:
        exit_code = 0
    else:
        joined = "\n".join(errors).lower()
        integrity_terms = ("hash", "chain", "stale", "digest", "tracked source", "schema", "manifest", "does not match", "mutation")
        blocked_terms = ("blocked", "unavailable", "collision/isolation did not survive", "environment collision")
        incomplete_terms = ("unresolved hypothesis", "open attempt", "required probe never", "incomplete", "never completed", "missing", "before runtime identity", "without healthy startup")
        if any(term in joined for term in integrity_terms):
            exit_code = 4
        elif any(term in joined for term in blocked_terms):
            exit_code = 5
        elif any(term in joined for term in incomplete_terms):
            exit_code = 3
        else:
            exit_code = 4
        result["exit_code"] = exit_code
    if result["ok"]:
        result["exit_code"] = 0
    emit(result, args.format)
    return exit_code



@workspace_locked_command
def cmd_policy(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    errors = validate_structured_outputs(paths)
    if errors:
        emit({"ok": False, "policy_passed": False, "errors": errors}, args.format)
        return 4
    result = load_json(paths["result"])
    fail_on = set(args.fail_on or [])
    matched: list[str] = []
    # Policy consumes authoritative feature verdicts, never denormalized counters.
    # validate_structured_outputs() already proved these features match the live
    # audit ledgers; verdict_counts remains a verified reporting projection only.
    verdicts = Counter(feature.get("verdict") for feature in result.get("features", []))
    if "FALSIFIED" in fail_on and verdicts.get("FALSIFIED", 0):
        matched.append("FALSIFIED")
    if "BLOCKED" in fail_on and verdicts.get("BLOCKED", 0):
        matched.append("BLOCKED")
    if "INCONCLUSIVE" in fail_on and verdicts.get("INCONCLUSIVE", 0):
        matched.append("INCONCLUSIVE")
    for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        token = f"SEVERITY_{severity}"
        if token in fail_on and any(f.get("severity") == severity for f in result.get("findings", [])):
            matched.append(token)
    payload = {"ok": not matched, "policy_passed": not matched, "fail_on": sorted(fail_on), "matched": matched, "audit_id": result.get("audit", {}).get("audit_id")}
    emit(payload, args.format)
    # 10 is intentionally reserved for product-acceptance policy rejection;
    # audit integrity/completeness use the 2-5 control-plane range.
    return 0 if not matched else 10


@workspace_locked_command
def cmd_present(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    errors = validate_structured_outputs(paths)
    if errors:
        emit({"ok": False, "errors": errors}, args.format)
        return 4
    result = load_json(paths["result"])
    if args.presentation == "json":
        emit({"ok": True, "audit": result["audit"], "coverage": result["coverage"], "verdict_counts": result["verdict_counts"], "findings": result["findings"], "artifacts": result["artifacts"]}, "json")
        return 0
    if args.presentation == "markdown":
        if not paths["summary_md"].exists():
            emit({"ok": False, "errors": ["audit-summary.md is missing"]}, args.format)
            return 4
        print(paths["summary_md"].read_text(encoding="utf-8"), end="")
        return 0
    vc, cov = result["verdict_counts"], result["coverage"]
    lines = [
        "Runtime Feature Falsifier — Audit Complete",
        "",
        f"Audit: {result['audit']['audit_id']}",
        f"Mode: {result['audit']['mode']}",
        f"Status: {result['audit']['status']}",
        "",
        f"Coverage: {cov['audited']}/{cov['planned']} features; {cov['completed_required_probes']}/{cov['required_probes']} required probes",
        f"FALSIFIED: {vc['FALSIFIED']}",
        f"NOT_FALSIFIED: {vc['NOT_FALSIFIED']}",
        f"BLOCKED: {vc['BLOCKED']}",
        f"INCONCLUSIVE: {vc['INCONCLUSIVE']}",
    ]
    if result["findings"]:
        lines += ["", "Highest-impact findings:"]
        rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for finding in sorted(result["findings"], key=lambda x: (rank.get(x["severity"], 9), x["finding_id"]))[:5]:
            lines.append(f"- {finding['finding_id']} [{finding['severity']}] {finding['implementation_pattern']} — {finding['title']}")
    lines += ["", "NOT_FALSIFIED means no counterexample was found under the declared probe matrix; it is not proof of correctness or production readiness."]
    print("\n".join(lines))
    return 0


def _validated_compare_result(raw: str | Path, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    requested = Path(raw)
    audit_dir = requested if requested.is_dir() else requested.parent
    if not requested.is_dir() and requested.name != "audit-result.json":
        return None, [f"{label}: compare requires an audit directory or audit-result.json path"]
    paths = audit_paths(audit_dir)
    with exclusive_lock(paths["control_lock"]):
        errors = validate_structured_outputs(paths)
        if errors:
            return None, [f"{label}: {error}" for error in errors]
        result = load_json(paths["result"])
        manifest = load_json(paths["manifest"])
        state, seal_errors, _seal = _seal_state(paths, result, manifest)
        if seal_errors:
            return None, [f"{label}: {error}" for error in seal_errors]
        if state != SEAL_STATE_SEALED:
            return None, [f"{label}: compare requires a current sealed audit; state={state}"]
        return result, []


def cmd_compare(args: argparse.Namespace) -> int:
    baseline, baseline_errors = _validated_compare_result(args.baseline, "baseline")
    current, current_errors = _validated_compare_result(args.current, "current")
    errors = baseline_errors + current_errors
    if errors or baseline is None or current is None:
        emit({"ok": False, "errors": errors or ["could not load validated comparison inputs"]}, args.format)
        return 4

    b = {x.get("fingerprint"): x for x in baseline.get("findings", [])}
    c = {x.get("fingerprint"): x for x in current.get("findings", [])}
    resolved = sorted(set(b) - set(c))
    candidates = set(c) - set(b)
    history_fingerprints: set[str] = set()
    if getattr(args, "history_dir", None):
        history_root = Path(args.history_dir)
        if history_root.is_dir():
            for result_path in sorted(history_root.glob("*/audit-result.json")):
                hist, hist_errors = _validated_compare_result(result_path, f"history:{result_path.parent.name}")
                if hist_errors or hist is None:
                    emit({"ok": False, "errors": hist_errors or [f"invalid history audit: {result_path.parent}"]}, args.format)
                    return 4
                hid = hist.get("audit", {}).get("audit_id")
                if hid in {baseline.get("audit", {}).get("audit_id"), current.get("audit", {}).get("audit_id")}:
                    continue
                history_fingerprints.update(
                    x.get("fingerprint") for x in hist.get("findings", []) if x.get("fingerprint")
                )
    regressed = sorted(candidates & history_fingerprints)
    new = sorted(candidates - history_fingerprints)
    persisting = sorted(set(b) & set(c))
    payload = {
        "ok": True,
        "baseline_audit_id": baseline.get("audit", {}).get("audit_id"),
        "current_audit_id": current.get("audit", {}).get("audit_id"),
        "new": [c[x]["finding_id"] for x in new],
        "persisting": [c[x]["finding_id"] for x in persisting],
        "resolved": [b[x]["finding_id"] for x in resolved],
        "regressed": [c[x]["finding_id"] for x in regressed],
        "by_fingerprint": {
            "new": new, "persisting": persisting, "resolved": resolved, "regressed": regressed
        },
    }
    emit(payload, args.format)
    return 0


@workspace_locked_command
def cmd_export(args: argparse.Namespace) -> int:
    paths = audit_paths(args.audit_dir)
    errors = validate_structured_outputs(paths)
    if errors:
        emit({"ok": False, "errors": errors}, args.format)
        return 4
    result = load_json(paths["result"])
    if args.export_format != "sarif":
        emit({"ok": False, "errors": [f"unsupported export format: {args.export_format}"]}, args.format)
        return 2
    sarif_results = []
    level = {"CRITICAL": "error", "HIGH": "error", "MEDIUM": "warning", "LOW": "note"}
    for f in result.get("findings", []):
        sarif_results.append({
            "ruleId": f.get("implementation_pattern", "RFF_FINDING"),
            "level": level.get(f.get("severity"), "warning"),
            "message": {"text": f"{f.get('finding_id')}: {f.get('title')} — {f.get('observed','')}"},
            "fingerprints": {"rffSemanticFingerprint": f.get("fingerprint", "")},
            "properties": {"featureId": f.get("feature_id"), "severity": f.get("severity"), "confidence": f.get("confidence"), "verdict": f.get("verdict")},
        })
    doc = {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json", "runs": [{"tool": {"driver": {"name": "Runtime Feature Falsifier", "version": RFF_VERSION}}, "results": sarif_results}]}
    out = Path(args.output) if args.output else paths["dir"] / "rff-results.sarif"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit({"ok": True, "format": "sarif", "output": str(out), "result_count": len(sarif_results)}, args.format)
    return 0

def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--audit-dir", type=Path, default=Path(".runtime-feature-audit"), help="Audit workspace (default: .runtime-feature-audit)")
    p.add_argument("--format", choices=("json", "text"), default="json", help="Output format (default: json)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Runtime Feature Falsifier deterministic audit controller")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="Create audit workspace and empty audit-plan.json")
    add_common(p)
    p.add_argument("--audit-id")
    p.add_argument("--project-name", default="current-project")
    p.add_argument("--environment", default="local")
    p.add_argument("--scope", default="user-requested feature scope")
    p.add_argument("--mode", choices=("feature", "system"), default="feature", help="Audit one/some features or the whole discovered system")
    p.add_argument("--target-root", type=Path, default=Path.cwd(), help="Target project root used for tracked-source integrity baseline")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("validate-plan", help="Validate plan structure and falsification coverage")
    add_common(p)
    p.set_defaults(func=cmd_validate_plan)

    p = sub.add_parser("attempt-start", help="Append STARTED event before executing one probe")
    add_common(p)
    p.add_argument("--feature-id", required=True)
    p.add_argument("--probe-id", required=True)
    p.add_argument("--attempt-id")
    p.add_argument("--action")
    p.add_argument("--repro-command")
    p.add_argument("--precondition", action="append", default=[])
    p.add_argument("--hypothesis-id", help="Attach this attempt to an open persistent-investigation hypothesis")
    p.add_argument("--changed-variable", help="Information-bearing variable changed from a prior attempt")
    p.add_argument("--retry-reason", help="Why an otherwise identical retry is informative (for example nondeterminism reproduction)")
    p.add_argument("--escalation-stage", choices=sorted(ESCALATION_STAGES), help="Investigation escalation stage 0-11")
    p.set_defaults(func=cmd_attempt_start)

    p = sub.add_parser("attempt-finish", help="Append FINISHED event after executing one probe")
    add_common(p)
    p.add_argument("--attempt-id", required=True)
    p.add_argument("--observed", required=True)
    p.add_argument("--side-effect-check")
    p.add_argument("--persistence-check")
    p.add_argument("--http-status", type=int)
    p.add_argument("--exit-code", type=int)
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--result", required=True, choices=sorted(RESULTS))
    p.add_argument("--failure-pattern", default="NONE_OBSERVED", choices=sorted(PATTERNS))
    p.add_argument("--confidence", default="MEDIUM", choices=sorted(CONFIDENCE))
    p.add_argument("--severity", default="MEDIUM", choices=sorted(SEVERITY), help="Impact severity if this attempt falsifies the feature; independent of implementation pattern")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_attempt_finish)

    p = sub.add_parser("attempt-batch", help="Atomically ingest many STARTED or FINISHED attempt events from one JSON document")
    add_common(p)
    p.add_argument("--input", required=True, help="JSON file path or '-' for stdin; see assets/attempt-batch.schema.json")
    p.set_defaults(func=cmd_attempt_batch)

    p = sub.add_parser("hypothesis-open", help="Open a bounded falsification hypothesis")
    add_common(p)
    p.add_argument("--feature-id", required=True)
    p.add_argument("--hypothesis-id")
    p.add_argument("--statement", required=True)
    p.add_argument("--trigger-attempt-id")
    p.add_argument("--next-probe", required=True)
    p.add_argument("--max-attempts", type=int, default=6)
    p.add_argument("--evidence-for", action="append", default=[])
    p.add_argument("--evidence-against", action="append", default=[])
    p.set_defaults(func=cmd_hypothesis_open)

    p = sub.add_parser("hypothesis-update", help="Append evidence/status to an existing hypothesis")
    add_common(p)
    p.add_argument("--hypothesis-id", required=True)
    p.add_argument("--status", required=True, choices=sorted(HYPOTHESIS_STATUSES))
    p.add_argument("--evidence-for", action="append", default=[])
    p.add_argument("--evidence-against", action="append", default=[])
    p.add_argument("--next-probe", default="")
    p.add_argument("--changed-variable", default="")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_hypothesis_update)

    p = sub.add_parser("investigation-status", help="Summarize open/terminal persistent-investigation hypotheses")
    add_common(p)
    p.set_defaults(func=cmd_investigation_status)

    p = sub.add_parser("terminology-check", help="Check a retrospective/summary for forbidden verdict inflation such as calling NOT_FALSIFIED verified or proven")
    p.add_argument("--input", action="append", required=True, help="Markdown/text file to scan; repeat for multiple files")
    p.add_argument("--format", choices=("json", "text"), default="json", help="Output format")
    p.set_defaults(func=cmd_terminology_check)

    p = sub.add_parser("summary", help="Summarize attempts and derived feature verdicts")
    add_common(p)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("report", help="Generate feature-matrix.md and audit-report.md")
    add_common(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("present", help="Render deterministic final presentation from audit-result.json")
    add_common(p)
    p.add_argument("--presentation", choices=("chat", "markdown", "json"), default="chat")
    p.set_defaults(func=cmd_present)

    p = sub.add_parser("compare", help="Compare two canonical audit-result.json files/directories")
    p.add_argument("--baseline", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--history-dir", help="Optional runs directory used to distinguish REGRESSED from NEW")
    p.add_argument("--format", choices=("json", "text"), default="json")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("policy", help="Apply an explicit product-acceptance policy to a canonical audit result")
    add_common(p)
    p.add_argument("--fail-on", action="append", choices=("FALSIFIED", "BLOCKED", "INCONCLUSIVE", "SEVERITY_CRITICAL", "SEVERITY_HIGH", "SEVERITY_MEDIUM", "SEVERITY_LOW"), default=[])
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("export", help="Export canonical findings to an interoperability format")
    add_common(p)
    p.add_argument("--export-format", choices=("sarif",), default="sarif")
    p.add_argument("--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("gate", help="Validate completeness/evidence; nonzero exit blocks finalization")
    add_common(p)
    p.add_argument("--require-report", action="store_true", help="Also require generated matrix/report")
    p.set_defaults(func=cmd_gate)
    return parser


def main() -> int:
    global _CURRENT_AUDIT_DIR
    args = build_parser().parse_args()
    _CURRENT_AUDIT_DIR = getattr(args, "audit_dir", None)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
