# Changelog

## 2.9.5

- Enforced the audit workspace lifecycle at the controller boundary: mutating commands now require an active, unsealed run, while sealed audits are immutable history.
- Added one audit-wide control lock around stateful read-check-write operations so semantic decisions and ledger mutations are serialized, not merely individual JSONL appends.
- Added immutable `audit-genesis.json` authority for the tracked-source baseline and bound its digest/snapshot format into result-manifest provenance; baseline replacement no longer erases mid-audit source mutation.
- Upgraded Git tracked-source snapshots to bind executable/type state in addition to file contents, so mode/type changes are detected.
- Made attempt/hypothesis writers refuse corrupt ledgers and reject duplicate explicit attempt IDs before appending.
- Made `compare` validate and require sealed canonical inputs (including history) instead of trusting raw `audit-result.json`.
- Reworked partial finalization/migration recovery to remain read-only until the complete final gate succeeds; recovery can no longer install a strong seal on a failing gate.
- Strengthened seal-last durability ordering with a directory durability barrier after result/manifest replacement and before the final seal commit.
- Added adversarial lifecycle-atomicity regressions covering sealed mutation refusal, two-process finish races, genesis/baseline bypass attempts, validated comparison, failed-migration recovery, corrupt ledgers, duplicate IDs, Git mode changes, and seal commit ordering.
- Added migration support for native v2.9.4 manifest-bound seals, binding inherited legacy baselines as explicitly lower-assurance genesis state during upgrade.

## 2.9.4

- Bound seal-local provenance metadata into `result-manifest.json` via canonical `seal_metadata`; the seal mirrors that metadata and hashes the manifest, so one-file edits to completion/seal timestamps, migration source, or next-run policy fail closed.
- Added strict RFC3339 UTC validation for seal provenance timestamps and explicit `completion_time_provenance` assurance labels instead of treating inherited historical timestamps as equally verified.
- Added migration support for genuine native v2.9.3 seals; their historical completion time is preserved as `v2.9.3-seal-unbound`, while current 2.9.4 gates emit `v2.9.4-gate`.
- Preserved prior migration lineage when a v2.9.3 seal was itself migrated from an earlier 2.9.x format.
- Made current gate re-execution idempotent and added recovery for seal-last crashes during legacy migration after current result/manifest promotion but before legacy seal replacement.
- Added a real untouched v2.9.3 sealed-audit fixture plus adversarial regressions for seal-only/manifest-only provenance tampering, invalid timestamps, v2.9.3 migration, partial-migration recovery, and idempotent re-gating.

## 2.9.3

- Promoted final gate completion to a versioned digest-bound audit seal; `PASSED` is derived from the seal rather than trusted as a mutable result field.
- Added seal-last multi-file commit semantics with fsync'd staging, fail-closed partial-state detection, and narrowly scoped `gate` recovery after re-derivation from plan and append-only ledgers.
- Cross-bound result/manifest/seal producer metadata and made 2.9.3 the unambiguous current producer version.
- Added migration handling for released 2.9.0-2.9.2 completion markers and pre-2.9.3 digest-bound seals; ordinary readers require migration while `gate` revalidates and upgrades them.
- Preserved historical `completed_at_utc` during migration and recorded separate `sealed_at_utc` plus migration provenance.
- Added real untouched v2.9.2 sealed-audit regression fixtures and adversarial migration/recovery tests.
- Clarified crash-durability documentation: POSIX uses file fsync + atomic replacement + directory fsync; other platforms use best-effort platform semantics.

## 2.9.2

- Hardened canonical JSON ingestion to reject duplicate object names, non-standard numeric constants, floats, unsafe JCS integers, and invalid Unicode scalar values before canonical hashing or policy decisions.
- Completed the Draft 7 canonical result/manifest contracts with closed nested objects, explicit required fields, constrained enums, digest formats, and typed projections.
- Added centralized semantic invariants that re-derive verdict counts, coverage, finding relationships, hypothesis budgets, and canonical result state instead of trusting denormalized projections.
- Made `result-manifest.json` input provenance bidirectionally verifiable against the current plan, inventory, and attempt/hypothesis chain heads; final gate and policy fail closed on any mismatch.
- Refactored policy evaluation to derive verdict state from validated feature results rather than trusting stored `verdict_counts`.
- Refreshed the vendored `fastjsonschema` engine to 2.22.2 and added regressions for bool-vs-number `enum`/`const` correctness while preserving the offline single-validator runtime.
- Added adversarial v2.9.2 regressions for re-hashed semantic projection forgery, forged provenance inputs, duplicate JSON keys, unsafe canonical numbers, and cross-document identity mismatch.
- Added optional CI-only differential/conformance tooling: a `jsonschema` reference oracle plus a Bowtie hook for the official Draft 7 suite when available, without reintroducing either dependency into runtime/self-test.

## 2.9.1

- Unified canonical result validation on the already-vendored `fastjsonschema` Draft 7 engine; `report`, `gate`, release validation, and self-test now use the same bundled validator.
- Re-declared the six v2.9 canonical result contracts as JSON Schema Draft 7 after confirming they use no 2020-12-only semantics.
- Removed the mandatory external `jsonschema` development dependency, so clean self-tests no longer require pip/venv setup or network access.
- Added strictly local bundled-schema `$ref` resolution and disabled schema defaults (`use_default=False`) so validation cannot fetch network resources or mutate canonical artifacts.
- Replaced the handwritten canonical-result structural subset with full schema enforcement; retained only RFF-specific cross-artifact/domain invariants in Python.
- Added adversarial nested contract checks and gate-level fail-closed regressions for invalid enums, nested types, extra properties, and malformed digests.

## 2.9.0

- Added public `rff audit ...` CONTROL plane and made direct `auditctl.py` usage an internal/compatibility path.
- Added configurable project-local audit output roots through `.rff.toml`, safe path validation, depth override, UUIDv7 run directories, and active/latest run pointers.
- Added canonical `audit-result.json`, `findings.json`, `audit-summary.md`, and `result-manifest.json`; only a successful gate promotes the result to `gate: PASSED`.
- Added float-free RFC 8785/JCS canonical hashing, semantic finding fingerprints, explicit severity separate from pattern/confidence, deterministic `present`, `compare`, `policy`, and SARIF export commands.
- Added JSON Schema 2020-12 result contracts while retaining Draft 7 schemas for legacy plan/event artifacts.
- Formalized CONTROL vs TARGET vs ILLUSTRATION semantics and updated supported agent instructions/hooks to follow configurable audit paths.
- Added v2.9 regression coverage for structured-output integrity/tamper detection and configurable multi-run storage.

## 2.8.0

- Field-hardening from a real Antigravity audit: mandatory runtime identity and environment-collision preflight.
- Added `AUDIT_ENVIRONMENT_INTERFERENCE` classification guidance.
- Added sealed-run policy: remediation/re-audit uses a new workspace and fresh auditor instance.
- Tightened reporting language and added `auditctl terminology-check` for retrospectives.
- Corrected Antigravity custom-agent tool manifest and added compatibility regression.


## 2.7.0

- Split Google Antigravity support into first-class `antigravity2` (desktop/IDE) and `antigravity-cli` (`agy`) integrations while sharing one workspace skill/auditor payload.
- Preserve `--antigravity`, `--google`, and `--integration antigravity` as backward-compatible combined selectors for both Google surfaces.
- Add Antigravity CLI-specific host guidance covering `/skills`, `/agents`, workspace/global skill roots, planning, asynchronous subagents, and hook verification.
- Add best-effort Antigravity 2.0 desktop detection plus explicit installer/wizard choices when auto-detection is unavailable.
- Add source and packaged-CLI regressions for independent Antigravity 2.0 and Antigravity CLI installation/update paths.


## 2.6.0

- Add first-class OpenCode v2 integration using the native `.opencode/skills/runtime-feature-falsifier/` project path.
- Add a dedicated selectable OpenCode `runtime-feature-auditor` under `.opencode/agents/` with `edit` denied and the RFF skill explicitly allowed.
- Add OpenCode executable/project-marker detection, interactive installer choice, `rff init --integration opencode`, install-plan reporting, and doctor/detection visibility.
- Add OpenCode-specific runtime guidance under `references/OPENCODE.md`.
- Extend source-installer, packaged-wheel, and detection regressions to cover OpenCode installation/update behavior.


## 2.5.1

- Lock canonical JSONL reads with the same cross-platform audit lock used by writers, preventing readers from observing a partial append.
- Replace the hand-written JSON Schema subset validator with a vendored standards-compliant Draft 7 `fastjsonschema` engine.
- Keep the skill self-contained/offline by vendoring the pure-Python validator and its BSD license.
- Add reader-lock and full-schema-engine regression tests.

## 2.5.0

- Expanded **be-there-to-look-pretty (BTTLP)** at first use and made `SKILL.md` the normative authority for verdict/retry semantics.
- Added atomic two-phase `attempt-batch` logging for large SYSTEM audits.
- Added mandatory startup-path + `environment_start` planning and reproduction metadata for startup/dependency-sensitive probes.
- Added cross-platform JSONL writer locking and concurrency regression coverage.
- Added current attempt/hypothesis chain heads to every controller response for transcript-visible tamper evidence.
- Wired bundled JSON schemas into runtime validation instead of leaving them documentation-only.
- Documented Windows `py -3` fallback.
- Added batch and concurrent-writer regression fixtures.

## 2.4.0

- Added persistent falsification investigation protocol with a hash-chained hypothesis ledger.
- Added information-gain retry enforcement: repeated probes require a changed variable or explicit reproduction/nondeterminism reason.
- Added bounded hypothesis budgets, escalation stages, investigation status, and deterministic terminal states.
- Final gate now rejects unresolved hypotheses.
- Added report-state fingerprints; final gate rejects reports generated before the latest canonical audit-state change.
- Extended Claude/Antigravity canonical-state guards to protect the hypothesis ledger and report-state marker.
- Reports now include persistent-investigation state.
- Updated Claude Code and Antigravity dedicated auditors to persist on investigation without modifying target code.
- Added regression coverage for persistence, retry discipline, and hypothesis completion.

## 2.3.1

- Simplified the default installer into an interactive setup wizard. Running `./install.sh` now installs/updates the `rff` CLI and immediately launches client detection and integration selection.
- `rff init` now asks for the target project when no path is supplied, avoiding accidental installation into the extracted release directory.
- Reworked the integration picker so **All detected** is the recommended default while still allowing individual or comma-separated selections.
- The interactive wizard keeps Antigravity strict workspace hooks opt-in instead of adding another setup prompt.
- Explicit flags remain available for automation and CI.

## 2.3.0

- Added a Spec-Kit-style persistent `rff` CLI package installable with `uv tool install`.
- Added `rff init`, `rff integration list`, `rff detect`, `rff doctor`, and `rff self` commands.
- Added a prebuilt universal wheel to the one-shot bundle so `./install.sh` can bootstrap the CLI without network access after extraction.
- `./install.sh` now installs/updates the persistent CLI first, then proxies legacy v2.x flags to `rff init` for backward compatibility.
- Added an isolated uv-tool regression test proving the wheel installs and initializes Codex, Antigravity, and Gemini integrations.

- Replaced the default full-auto installer with an interactive, dependency-free client picker inspired by multi-agent bootstrap CLIs such as GitHub Spec Kit.
- Detects Codex, Antigravity CLI, Gemini CLI, and Claude Code from executables/project markers and shows existing Runtime Feature Falsifier install state before writing.
- Added install-plan preview, explicit confirmation, `UPDATE` labeling, optional Antigravity strict-hook prompt, and `--detect` read-only mode.
- Non-interactive sessions no longer silently choose integrations; explicit mode flags are required.
- Added an installed `VERSION` marker to support future upgrade detection.

- Added native Google Antigravity 2.0 / Antigravity CLI compatibility using the shared `.agents/skills/` Agent Skills path.
- Added a dedicated Antigravity custom `runtime-feature-auditor` under `.agents/agents/` with the skill preloaded.
- Added explicit Gemini CLI compatibility through the interoperable `.agents/skills/` workspace alias.
- Added Antigravity `/plan` / `agy --mode=plan` guidance for whole-system audits and a Google-specific progressive-disclosure reference.
- Added optional Antigravity `/goal` guidance for long exhaustive SYSTEM execution and `/browser` delegation guidance that preserves STARTED/FINISHED audit logging.
- Added optional strict Antigravity workspace hooks: source-mutation guard, PreInvocation context reminder, and Stop completion gate.
- Added audit lifecycle markers so workspace-wide hooks are active only during an audit; a successful final gate deactivates them.
- Protected canonical attempt-log, tracked-source baseline, and lifecycle files from direct companion-hook edits.
- Extended installer/self-tests for Antigravity, Gemini-compatible discovery layout, strict-hook merge behavior, and Google hook contracts.

## 2.2.0

- Separated distribution-only teaching fixtures and eval datasets from the installable runtime skill.
- Moved Claude-specific guard/stop hooks out of the portable skill and into the Claude companion package.
- Fixed the duplicated `--target-root` argument in the documented initialization command.
- Added install-layout regression checks proving Codex receives only runtime resources.
- Added an upgrade regression check proving `--force` replaces stale prior-version files rather than merging them.
- Added same-parent staging/rollback behavior for directory/file replacement during installation.
- Added a distribution `VERSION` marker and versioned installer output.

## 2.1.0

- Added explicit FEATURE and SYSTEM audit modes.
- Added whole-project feature inventory, anti-sampling validation, system coverage reporting, and `/plan` guidance for exhaustive Codex audits.
