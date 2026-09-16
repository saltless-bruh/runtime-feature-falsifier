"""Demonstrate varied runtime probes using the v2 plan/log/gate control plane.

Teaching contract:
- JPEG, PNG, GIF, WebP, TIFF, SVG, and EPS inputs are accepted,
- representative non-images/corrupt inputs are rejected,
- accepted uploads are retrievable and causally reflect their input.

The target is intentionally FAKE_NOOP, so every planned probe should expose a
counterexample while STARTED/FINISHED chronology remains complete.
"""
from __future__ import annotations

import contextlib
import io
import json
import mimetypes
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

from _common import request, running, upload
from fake_upload_app import Handler

HERE = Path(__file__).resolve().parent
DIST_ROOT = HERE.parents[1]
SKILL_ROOT = DIST_ROOT / "runtime-feature-falsifier"
AUDITCTL = SKILL_ROOT / "scripts" / "auditctl.py"
CORPUS_GENERATOR = DIST_ROOT / "tests" / "tools" / "generate_upload_corpus.py"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
import auditctl  # noqa: E402
VALID_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".svg", ".eps"}
STRUCTURAL_INVALID = {"empty.png", "truncated.png", "renamed-text.png"}


def relation(path: Path) -> str:
    if path.name in STRUCTURAL_INVALID:
        return "INVALID"
    return "VALID" if path.suffix.lower() in VALID_EXTS else "INVALID"


def mime_for(path: Path) -> str:
    special = {".webp": "image/webp", ".tif": "image/tiff", ".tiff": "image/tiff", ".svg": "image/svg+xml", ".eps": "application/postscript"}
    return special.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def ctl(*args: str) -> dict:
    proc = subprocess.run([sys.executable, str(AUDITCTL), *args], check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def direct_start(audit: Path, feature_id: str, probe_id: str, action: str, attempt_id: str) -> None:
    args = SimpleNamespace(
        audit_dir=audit, format="json", feature_id=feature_id, probe_id=probe_id,
        attempt_id=attempt_id, action=action, repro_command=("start teaching HTTP server" if probe_id == "environment-start" else None), precondition=[],
        hypothesis_id=None, changed_variable=None, retry_reason=None, escalation_stage=None
    )
    with contextlib.redirect_stdout(io.StringIO()):
        rc = auditctl.cmd_attempt_start(args)
    assert rc == 0


def direct_finish(audit: Path, attempt_id: str, observed: str, side: str, *, result: str = "FALSIFIED", pattern: str = "FAKE_NOOP") -> None:
    args = SimpleNamespace(
        audit_dir=audit, format="json", attempt_id=attempt_id, observed=observed,
        side_effect_check=side, persistence_check=None, http_status=None, exit_code=None,
        evidence=[], result=result, failure_pattern=pattern, confidence="HIGH", notes=""
    )
    with contextlib.redirect_stdout(io.StringIO()):
        rc = auditctl.cmd_attempt_finish(args)
    assert rc == 0


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        temp = Path(td)
        audit = temp / ".runtime-feature-audit"
        corpus = temp / "corpus"
        subprocess.run([sys.executable, str(CORPUS_GENERATOR), str(corpus)], check=True, stdout=subprocess.DEVNULL)
        files = sorted(p for p in corpus.iterdir() if p.is_file())
        assert len(files) >= 15, "corpus unexpectedly small"

        print("stage: init", flush=True)
        ctl("init", "--audit-dir", str(audit), "--project-name", "teaching-fake-upload", "--environment", "local", "--scope", "upload-image")
        assert (audit / ".active.json").exists(), "init must mark audit active"

        probes = [{
            "probe_id": "environment-start",
            "probe_intent": "environment_start",
            "contract_relation": "ENVIRONMENT",
            "action": "Start the teaching HTTP service and verify it accepts connections",
            "expected": "Teaching runtime starts and is reachable",
            "effect_checks": ["HTTP service accepts a request"],
            "required": True,
        }]
        valid_seen = 0
        for idx, file in enumerate(files, 1):
            rel = relation(file)
            if rel == "VALID":
                valid_seen += 1
                intent = "baseline_valid" if valid_seen == 1 else "format_variation"
                expected = "Upload succeeds and object can be read back"
                effects = ["retrieved object exists and corresponds to uploaded content"]
            else:
                intent = "invalid_type_or_shape"
                expected = "Input is rejected and no object is created"
                effects = ["no persisted upload exists"]
            probes.append({
                "probe_id": f"file-{idx:02d}-{file.stem.lower().replace('_','-')}",
                "probe_intent": intent,
                "contract_relation": rel,
                "input_family": mime_for(file),
                "input_case": file.name,
                "action": f"Upload {file.name} through POST /upload and inspect required end effect",
                "expected": expected,
                "effect_checks": effects,
                "required": True,
            })

        # Dedicated differential and round-trip probes make the falsification obligations explicit.
        probes += [
            {
                "probe_id": "causal-two-distinct-pngs",
                "probe_intent": "causal_sensitivity",
                "contract_relation": "VALID",
                "input_family": "image/png",
                "input_case": "valid-a.png vs valid-b.png",
                "action": "Upload two materially distinct valid PNGs and compare returned identity/state",
                "expected": "Distinct uploads retain input-dependent identity/content",
                "effect_checks": ["returned or retrieved state causally distinguishes A from B"],
                "required": True,
            },
            {
                "probe_id": "round-trip-valid-png",
                "probe_intent": "round_trip",
                "contract_relation": "VALID",
                "input_family": "image/png",
                "input_case": "valid-a.png",
                "action": "Upload valid-a.png then retrieve the returned URL",
                "expected": "Retrieved object exists after successful upload",
                "effect_checks": ["read-back succeeds"],
                "required": True,
            },
        ]
        plan = json.loads((audit / "audit-plan.json").read_text())
        plan["target"]["startup_path"] = "start teaching HTTP server via running(Handler)"
        plan["features"] = [{
            "feature_id": "upload-image",
            "claim": "Supported image uploads persist and non-images/corrupt inputs are rejected",
            "claim_source": "teaching fixture contract",
            "entry_points": ["POST /upload teaching runtime"],
            "expected_end_effects": ["accepted objects are retrievable", "unsupported inputs are rejected"],
            "input_sensitive": True,
            "stateful": True,
            "dependency_sensitive": False,
            "probes": probes,
        }]
        (audit / "audit-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        print(f"stage: validate plan with {len(probes)} probes", flush=True)
        ctl("validate-plan", "--audit-dir", str(audit))

        file_by_name = {p.name: p for p in files}
        with running(Handler) as base:
            for n, probe in enumerate(probes, 1):
                print(f"probe {n}/{len(probes)}: {probe['probe_id']}", flush=True)
                aid = f"att-teach-{n:03d}"
                direct_start(audit, "upload-image", probe["probe_id"], probe["action"], aid)

                if probe["probe_id"] == "environment-start":
                    status, _, _ = request(base + "/missing")
                    direct_finish(audit, aid, f"runtime reachable; control status={status}", "HTTP server accepted a connection", result="SURVIVED", pattern="NONE_OBSERVED")
                    continue
                if probe["probe_id"] == "causal-two-distinct-pngs":
                    a = file_by_name["valid-a.png"]
                    b = file_by_name["valid-b.png"]
                    sa, pa, _ = upload(base, a.name, mime_for(a), a.read_bytes())
                    sb, pb, _ = upload(base, b.name, mime_for(b), b.read_bytes())
                    falsified = sa < 300 and sb < 300 and pa.get("id") == pb.get("id")
                    observed = f"A status/id={sa}/{pa.get('id')}; B status/id={sb}/{pb.get('id')}"
                    side = "Distinct valid inputs collapsed to the same canned upload id"
                elif probe["probe_id"] == "round-trip-valid-png":
                    file = file_by_name["valid-a.png"]
                    status, payload, _ = upload(base, file.name, mime_for(file), file.read_bytes())
                    read_status, _, _ = request(base + payload.get("url", "/missing")) if status < 300 else (status, b"", {})
                    falsified = status < 300 and read_status != 200
                    observed = f"upload_status={status}; readback_status={read_status}"
                    side = "Advertised returned object is not retrievable"
                else:
                    file = file_by_name[probe["input_case"]]
                    status, payload, _ = upload(base, file.name, mime_for(file), file.read_bytes())
                    if relation(file) == "VALID":
                        read_status, _, _ = request(base + payload.get("url", "/missing")) if status < 300 else (status, b"", {})
                        falsified = status < 300 and read_status != 200
                        observed = f"upload_status={status}; readback_status={read_status}"
                        side = "Success response produced no retrievable stored object"
                    else:
                        falsified = status < 300 and payload.get("ok") is True
                        observed = f"invalid input upload_status={status}; payload={payload}"
                        side = "Contract-required rejection was bypassed with fake success"

                assert falsified, f"expected counterexample for {probe['probe_id']}"
                direct_finish(audit, aid, observed, side)

        print("stage: report", flush=True)
        ctl("report", "--audit-dir", str(audit))
        gate = ctl("gate", "--audit-dir", str(audit), "--require-report")
        assert gate["ok"]
        assert not (audit / ".active.json").exists(), "successful final gate must deactivate audit"
        assert (audit / ".complete.json").exists(), "successful final gate must write completion marker"
        events = [json.loads(line) for line in (audit / "attempts.jsonl").read_text().splitlines() if line.strip()]
        assert len(events) == len(probes) * 2
        assert sum(e["event_type"] == "STARTED" for e in events) == len(probes)
        assert sum(e["event_type"] == "FINISHED" for e in events) == len(probes)
        print(f"PASS: {len(probes)} varied runtime probes logged STARTED+FINISHED; hash-chain/final gate/lifecycle markers passed")


if __name__ == "__main__":
    main()
