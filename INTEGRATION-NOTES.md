# Runtime Feature Falsifier v2.7.0 — integration notes

## v2.6.0 OpenCode integration

OpenCode v2 is now a first-class integration. `rff init --integration opencode` installs the skill to OpenCode's native project path `.opencode/skills/runtime-feature-falsifier/` and installs a dedicated read-only auditor at `.opencode/agents/runtime-feature-auditor.md`. OpenCode can discover `.agents/skills`, but the native path is used deliberately for documented OpenCode precedence and predictable project-local discovery.

## v2.5.1 hardening

Canonical JSONL readers now acquire the same cross-platform lock as writers, closing the partial-last-line race for summary/report/gate reads. Structural validation now uses a vendored `fastjsonschema` Draft 7 engine rather than RFF's former hand-written JSON Schema subset. The validator is bundled under the installable skill, so runtime audits remain self-contained and do not require pip/network setup.


> **Python command:** examples use `python3` on POSIX. On default Windows Python installations, use `py -3` instead.

## Simple interactive setup

For normal local use, run:

```bash
./install.sh
```

The bootstrap installs/updates the persistent `rff` CLI and immediately opens a terminal wizard. The wizard asks for the target project, detects Codex, Google Antigravity 2.0, Google Antigravity CLI, Gemini CLI, OpenCode, and Claude Code, then lets you install all detected integrations or choose individual clients.

Explicit flags remain available for automation, for example:

```bash
./install.sh --codex --project /path/to/project --force
```


## Spec-Kit-style persistent CLI

v2.4 ships an installable CLI named `rff`. The intended lifecycle now mirrors GitHub Spec Kit's tool-first workflow: install the CLI once, then initialize/update integrations in each project.

From the downloadable one-shot bundle:

```bash
./install.sh

# then, from a project
rff integration list
rff init --here --integration codex
```

The release bundle contains a prebuilt universal wheel, so `./install.sh` can install the CLI with `uv tool install` without fetching Python build dependencies. The old one-command syntax remains valid and bootstraps the CLI before initializing the project:

```bash
./install.sh --codex --project /path/to/project --force
```

Direct CLI usage:

```bash
rff --version
rff integration list
rff detect --here
rff doctor --here
rff init --here                         # interactive client picker
rff init --here --integration codex
rff init --here --integration antigravity2
rff init --here --integration antigravity-cli
rff init --here --integration gemini
rff init --here --integration opencode
rff init --here --integration claude
```

Multiple integrations may be selected by repeating `--integration`, or with `--integration all`. For automation, add `--non-interactive --yes`; existing managed destinations additionally require `--force`.

Once this project is published to a Git repository/PyPI, the package is structured for the same installation form used by Spec Kit:

```bash
uv tool install runtime-feature-falsifier-cli \
  --from git+https://github.com/<owner>/<repo>.git@v2.7.0

rff init --here --integration antigravity
```

The CLI also supports explicit-source upgrades:

```bash
rff self upgrade --from /path/to/new/runtime-feature-falsifier-release
rff self upgrade --from git+https://github.com/<owner>/<repo>.git@v2.7.0
```

Automatic `latest` release discovery is intentionally not claimed until there is a canonical published repository/release channel.

## Distribution layout

v2.4 separates the **installable runtime skill** from **development/evaluation fixtures**.

```text
runtime-feature-falsifier-v2.7.0/
├── pyproject.toml                  # installable runtime-feature-falsifier-cli package
├── setup.py
├── src/runtime_feature_falsifier_cli/
│   ├── cli.py                     # `rff` command
│   └── payload/                   # wheel-bundled installation payload
├── dist/
│   └── runtime_feature_falsifier_cli-2.7.0-py3-none-any.whl
├── runtime-feature-falsifier/      # canonical portable Agent Skill source
│   ├── SKILL.md
│   ├── LICENSE
│   ├── agents/openai.yaml
│   ├── assets/
│   ├── references/
│   └── scripts/auditctl.py
├── claude-code/                    # Claude-only companion agent + hooks
│   └── .claude/
├── google-antigravity/             # Antigravity custom agent + optional hooks
├── tests/                          # distribution self-test/evals; NOT installed
├── install.sh                      # uv-tool bootstrap + legacy one-command proxy
├── installer.py                    # source-mode fallback/compatibility launcher
├── self-test.sh
├── VERSION
└── SHA256SUMS.txt
```

This keeps cross-harness skill discovery lean while preserving the teaching corpus and eval datasets in the downloadable one-shot bundle.

## Project-local installation paths

For **Claude Code**:

```text
<project>/.claude/skills/runtime-feature-falsifier/
<project>/.claude/agents/runtime-feature-auditor.md
<project>/.claude/runtime-feature-falsifier-hooks/
```

For **Codex**:

```text
<project>/.agents/skills/runtime-feature-falsifier/
```

The portable skill has no Claude-specific hook scripts. `agents/openai.yaml` remains inside the portable skill for OpenAI/Codex-facing metadata.

## Interactive installer (default)

v2.4 uses a dependency-free interactive installer when `install.sh` is run from a real terminal without an integration flag. The flow intentionally follows the same usability pattern as modern multi-agent bootstrap CLIs such as GitHub Spec Kit: detect available coding-agent clients, show the target project and existing integration state, let the user choose one or more integrations, preview the exact install/update destinations, then confirm before writing.

Typical use:

```bash
./install.sh --project /path/to/project
```

The picker offers Codex, Google Antigravity 2.0, Google Antigravity CLI, Gemini CLI, OpenCode, Claude Code, all detected clients, or all supported clients. Antigravity CLI detection uses `agy`; Antigravity 2.0 desktop detection is best-effort from standard application markers and remains explicitly selectable when shell detection is unavailable. Both Google choices share `.agents/skills/` and `.agents/agents/`, so selecting both installs one deduplicated workspace payload.

If an existing Runtime Feature Falsifier installation is found, the preview shows `UPDATE` and interactive mode asks before replacing managed files. The installer never silently defaults to an integration. In a non-interactive/CI session, an explicit mode is required.

Scripted examples remain supported:

```bash
./install.sh --codex --project /path/to/project
./install.sh --antigravity --project /path/to/project
./install.sh --gemini --project /path/to/project
./install.sh --all --project /path/to/project --force
```

Use `--detect --project /path/to/project` for a read-only client/install-state report. Use `--force` for deterministic non-interactive upgrades. The replacement path is staged/rollback-capable and replaces the prior managed tree rather than merging files, so stale material from older releases does not linger.

The installer does **not** remove or rewrite:

```text
<project>/.runtime-feature-audit/
```

If you want a clean audit run after upgrading schemas/workflow, archive that directory yourself before starting the next audit.

## Self-test behavior

`self-test.sh` validates four layers before normal installation:

1. distribution structure and Agent Skill links/metadata,
2. Python syntax for runtime, tests, Claude companion hooks, and Google companion hooks,
3. the fake/TODO/hardcoded/mock-only/BTTLP teaching corpus plus SYSTEM inventory regression,
4. real install-layout and `--force` upgrade behavior in a temporary project.

The teaching corpus remains dependency-free and uses only the Python standard library.

## Runtime controller path rule

Agent Skills clients resolve bundled `scripts/...` paths from the skill directory. Audit state and source-integrity checks must target the application repository explicitly:

```bash
python3 scripts/auditctl.py init \
  --audit-dir "<project-root>/.runtime-feature-audit" \
  --target-root "<project-root>" \
  --project-name "<project>" \
  --environment "local" \
  --scope "<requested scope>" \
  --mode <feature|system>
```

v2.4 retains the corrected single `--target-root` that appeared in the v2.1 SKILL.md example.

## Persistent investigation / stubborn-auditor mode

v2.4 adds a portable bounded persistence protocol shared by Codex, Claude Code, Antigravity, and Gemini CLI. Suspicious, ambiguous, or intermittent runtime behavior can open a hash-chained `hypothesis-ledger.jsonl`. The agent must change an information-bearing variable on repeated probes, or explicitly state why an unchanged retry is useful for nondeterminism/reproduction.

The controller exposes:

```bash
python3 scripts/auditctl.py hypothesis-open ...
python3 scripts/auditctl.py hypothesis-update ...
python3 scripts/auditctl.py investigation-status ...
```

Hypotheses are bounded by a declared attempt budget and must terminate as `REFUTED`, `CONFIRMED`, `BLOCKED`, or `BUDGET_EXHAUSTED`. `OPEN`/`SUPPORTED` hypotheses block final completion. A `CONFIRMED` hypothesis must be tied to an actual runtime `FALSIFIED` attempt; the ledger cannot manufacture a failure by prose alone.

Repeated probes are rejected unless the attempt supplies `--changed-variable` or `--retry-reason`. This makes persistence information-seeking instead of repetitive. `BUDGET_EXHAUSTED` prevents a surviving feature from being reported `NOT_FALSIFIED`; absent another independent falsification it becomes `INCONCLUSIVE`.

Reports now carry a `.report-state.json` fingerprint over the plan, attempt log, hypothesis ledger, and SYSTEM inventory. `gate --require-report` rejects stale reports generated before the latest canonical state change.

## Whole-project / SYSTEM mode

Use `SYSTEM` mode when the user asks for all/every features or the whole project/system. It requires `feature-inventory.json` in addition to `audit-plan.json` and rejects representative sampling when discovered `IN_SCOPE` capabilities remain unmapped.

For Codex, use `/plan` first for whole-project audits. During Plan Mode, perform read-only discovery, system-boundary definition, inventory design, dependency/blocker discovery, and execution-batch planning. Run runtime falsification probes only after Plan Mode ends.

Suggested planning prompt:

```text
Use $runtime-feature-falsifier to plan an exhaustive SYSTEM audit of this project.
Discover every claimed user-facing/public runtime capability, public function where
applicable, and meaningful cross-feature workflow. Do not sample. Define the
feature universe, runtime entry points, dependencies, and execution batches.
Do not repair implementation.
```

## Claude enforcement split

The Claude companion agent preloads the portable skill. Its deterministic hooks are installed separately at:

```text
<project>/.claude/runtime-feature-falsifier-hooks/
```

- `PreToolUse` blocks direct source/test writes and obvious source-mutating shell actions.
- `Stop` blocks completion until `auditctl gate --require-report` passes.
- `auditctl.py` remains the portable deterministic controller shared by Claude and Codex.

Hooks are defense-in-depth, not an OS sandbox. The tracked-source baseline/final gate remains a second integrity layer.

## Google Antigravity 2.0 / Antigravity CLI / Gemini CLI

The shared portable skill installs at `<project>/.agents/skills/runtime-feature-falsifier/`, which is the preferred Antigravity workspace skill path and a current Gemini CLI workspace alias. Antigravity additionally gets a custom agent at `<project>/.agents/agents/runtime-feature-auditor/agent.md`.

Install Antigravity integration:

```bash
./install.sh --antigravity --project /path/to/project
```

Install skill-only Gemini CLI compatibility:

```bash
./install.sh --gemini --project /path/to/project
```

Because Codex, Antigravity, and current Gemini CLI can all consume `.agents/skills/`, they share one portable skill copy rather than duplicating it.

Strict Antigravity hooks are opt-in because workspace hooks affect ordinary non-audit sessions too. To install them:

```bash
./install.sh --antigravity --strict-google-hooks --project /path/to/project --force
```

The strict hooks preserve existing `.agents/hooks.json` entries and add only namespaced Runtime Feature Falsifier keys. They are dormant unless `.runtime-feature-audit/.active.json` exists.

For long exhaustive Antigravity SYSTEM runs, first use `/plan` (or `agy --mode=plan`) for read-only discovery. After approval, `/goal Execute the approved runtime-feature-falsifier SYSTEM audit to completion without repairing source or tests; stop only after the deterministic audit gate passes.` can be used as an optional autonomy wrapper. GUI probes can use `/browser`; keep STARTED/FINISHED logging in the controlling audit trajectory around browser delegation.

## Google Antigravity 2.0 and Antigravity CLI

RFF treats the two current Google surfaces as separate installer integrations while reusing their shared workspace customization layout.

```bash
rff init --here --integration antigravity2
rff init --here --integration antigravity-cli
```

Selecting both installs only one `.agents/skills/runtime-feature-falsifier/` copy and one `.agents/agents/runtime-feature-auditor/` definition. The legacy `--antigravity` / `--integration antigravity` selector installs support for both.

Antigravity CLI users can verify discovery with `/skills` and `/agents`. Antigravity 2.0 users should reopen/start the Project and select `runtime-feature-auditor` from custom agents if discovery was already cached. Strict `.agents/hooks.json` enforcement remains opt-in because hooks apply workspace-wide.
