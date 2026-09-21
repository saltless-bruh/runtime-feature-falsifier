# Changelog

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
