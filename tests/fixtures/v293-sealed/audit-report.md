# Runtime Feature Falsification Report

## Scope

- Audit ID: `v293-sealed`
- Project: demo
- Environment: local
- Scope: feature
- Audit mode: FEATURE

## Executive result

- Features audited/planned: 1
- FALSIFIED: 0
- NOT_FALSIFIED: 1
- BLOCKED: 0
- INCONCLUSIVE: 0
- INCOMPLETE: 0

> `NOT_FALSIFIED` means no counterexample was found in the completed declared matrix. It must not be rewritten as a verdict such as "verified", "proven", "alive", or "fully working"; it is not proof of correctness, completeness, or production readiness.

## Runtime preflight

- Declared startup path: demo --serve
- Expected runtime identity: demo service
- Collision surfaces considered: isolated-db

## Feature results

### feature

- Claim: feature has real effect
- Verdict: **NOT_FALSIFIED**
- Terminal attempts: 6
- Patterns: NONE_OBSERVED

## Persistent investigation

- Hypotheses opened: 0
- Active hypotheses: 0

## Attempt-log integrity

- Canonical event log: `attempts.jsonl`
- Events: 12
- Attempts: 6
- Open attempts: none
