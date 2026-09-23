# RFF Control Plane, Output Roots, and Canonical Results

**Normative authority:** `SKILL.md` is normative. This reference explains the v2.9 control-plane workflow and must not redefine its rules.

## Three command classes

- **CONTROL** — `rff audit ...`. These commands may create or modify canonical RFF state.
- **TARGET** — commands/interactions against the audited product (`curl`, browser actions, Docker, SQL, application CLI). They may create runtime evidence but must not edit RFF canonical files directly.
- **ILLUSTRATION** — documentation examples only. Do not execute literally.

Agents must not confuse a TARGET command with an RFF tool. The RFF CLI records what happened; it does not replace the real runtime action.

## Output-root authority

The project root may contain `.rff.toml`:

```toml
[audit]
output_dir = ".artifacts/rff"
```

Resolution order is explicit `--output-dir`, project `.rff.toml`, then `.runtime-feature-audit`.

Normal paths must be project-relative, remain inside the project, avoid reserved/generated directories, and be no deeper than four path components. Deeper placement requires `--allow-deep-output`. RFF records a warning when the override is used.

Never hard-code `.runtime-feature-audit` in an agent workflow. Resolve with:

```bash
rff audit where --format json
```

## Run layout

The configured output root stores immutable runs:

```text
<output-root>/
├── active.json
├── latest.json
└── runs/
    └── rff-<uuidv7>/
        ├── audit-plan.json
        ├── feature-inventory.json        # SYSTEM mode
        ├── attempts.jsonl
        ├── hypothesis-ledger.jsonl
        ├── evidence/
        ├── audit-result.json
        ├── findings.json
        ├── result-manifest.json
        ├── .complete.json                # digest-bound audit seal after successful gate
        ├── audit-summary.md
        ├── feature-matrix.md
        ├── system-coverage.md            # SYSTEM mode
        └── audit-report.md
```

A sealed run is history. After remediation, initialize a new run.

## Canonical final output

`audit-result.json` is the canonical final result contract. `findings.json`, Markdown reports, `rff audit present`, and SARIF are projections of canonical state.

Agents must not manually author canonical result/report files. Generate them with:

```bash
rff audit report
rff audit gate
rff audit present --presentation chat
```

Only a successful gate may promote `audit.gate` from `PENDING` to `PASSED`. `PASSED` is not trusted as a stored field by itself: it is accepted only when `.complete.json` is a valid seal that binds the exact `audit-result.json` and `result-manifest.json` digests to the same audit identity and producer. A missing seal with `PENDING` is a valid unfinished run; any other gate/seal disagreement is an integrity failure.

Gate finalization uses a **seal-last multi-file commit protocol**. The result and manifest are prepared before replacement, and the seal is installed last as the logical commit record. Ordinary readers fail closed on a partial commit; `rff audit gate` alone may resume a partial finalization after re-deriving the `PASSED` result from the plan and append-only ledgers. This is transaction-like recovery, not a claim of distributed two-phase commit or multi-file atomicity.

On POSIX, RFF fsyncs staged files, uses same-directory atomic replacement, then performs a best-effort directory fsync. On other platforms, replacement/crash-durability guarantees depend on the operating system and filesystem; RFF still detects incomplete seal-last states and fails closed.

Sealed runs from released v2.9.0-v2.9.2 use the historical completion-marker protocol. The intermediate digest-bound 2.9.2 hardening candidate uses a pre-v2.9.3 seal shape, native v2.9.3 uses a digest-bound seal without manifest-bound descriptive metadata, and v2.9.4 uses the manifest-bound seal protocol. These historical forms are **migration inputs**, not current v2.9.5 seals: ordinary readers return a migration-required integrity state, while `rff audit gate` may fully revalidate the audit and upgrade it.

v2.9.5 retains manifest-bound canonical `seal_metadata`: `result-manifest.json` carries the metadata and `.complete.json` mirrors it while hashing the manifest. `completed_at_utc`, `sealed_at_utc`, migration source, completion-time provenance, and next-run policy therefore remain integrity-bound. Seal timestamps must be RFC3339 UTC values. Native v2.9.5 completion uses `v2.9.5-gate`; migrated timestamps retain explicit assurance labels appropriate to their historical source.

## Workspace lifecycle and authority

The controller enforces the lifecycle, rather than relying on agent discipline. Stateful mutation commands require an **active and unsealed** audit workspace. Once a valid seal exists, the run is immutable history: attempts, hypotheses, reports, and other mutating CONTROL operations are rejected. A single audit-wide control lock serializes each stateful read-check-write operation, so semantic validation and mutation occur under the same workspace transaction boundary.

At initialization, RFF writes `audit-genesis.json`. The genesis document binds the original tracked-source baseline digest and the snapshot format used for that run. Current manifests bind both genesis and baseline digests. This makes the baseline a local root-of-authority for source-integrity checks instead of a freely replaceable cache. Native v2.9.5 Git snapshots bind tracked content plus Git-significant worktree type/executable state. Historical audits migrated without a genesis record are explicitly marked with lower-assurance `legacy-baseline-unbound-at-migration` provenance. The genesis is an internal integrity anchor, not external authenticity against an adversary able to rewrite the entire workspace.

Canonical readers use the same validation boundary. `present`, `policy`, `export`, and `compare` reject invalid/unsealed/tampered inputs; comparison history is validated before it can influence NEW/RESOLVED/REGRESSED classification. Writers refuse to extend a corrupt ledger.

Recovery is recognition-before-commit: partial finalization or migration may be recognized by `gate`, but recovery helpers do not install or repair the authoritative seal before the complete final gate succeeds. The successful gate performs the seal-last commit. On POSIX, RFF fsyncs staged files, replaces genesis/result/manifest, fsyncs the audit directory as a pre-seal durability barrier, installs the seal last, then fsyncs the directory again.

## Integrity

`result-manifest.json` binds the canonical result to the plan/inventory and current attempt/hypothesis chain heads. RFF uses a float-free RFC 8785/JCS profile for canonical JSON hashing. Floating-point fields are intentionally excluded from the v1 result contract.

## Result semantics

Audit validity and product health are separate. A structurally successful audit can legitimately have many `FALSIFIED` features. The gate validates evidence/integrity/completeness; product acceptance policy is separate.

## Final-response rule

Use `rff audit present` as the factual basis of the final user response. An agent may explain the result but must not alter gate status, coverage counts, verdict counts, finding IDs, or finding classifications.
