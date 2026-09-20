### Code Review: Sprint 7 — Deploy Workflow GHCR Fix

**Date:** 2026-09-20
**Reviewer:** Claude Opus 4.6 (independent review agent)
**PR:** #15
**Branch:** sprint-7/deploy-workflow-fix

## Finding 1: Correctness — APPROVED

Using `github.repository_owner | lower` / `github.event.repository.name | lower` is the correct fix for GHCR casing.

`github.repository` preserves case (e.g., `stock-automator/SwingTrader-26`).
GHCR stores images under the lowercase organization/user name, so the old
expression would produce image paths with uppercase characters that don't
match the actual GHCR namespace.

The new expression correctly produces lowercase paths.

## Finding 2: Scope — APPROVED

The change is minimal and targeted: 2 lines in `env:`.

No other workflows, scripts, or code are affected.

## Finding 3: Backward Compatibility — APPROVED

No user-facing API or behavior changes.

Existing deployed images are unaffected (this only affects future pushes).

## Finding 4: Testing — NOTE

The deploy workflow does not have unit tests (it's a GitHub Actions workflow).

Validation performed:
- YAML syntax validated via Python yaml parser ✓
- Local pytest suite passes (856 tests) ✓
- lint/black/isort pass ✓

The fix will be verified when the deploy workflow runs successfully on
merge to main (previous runs failed due to this exact issue).

## Finding 5: Documentation — APPROVED

Commit message clearly explains the problem and fix.

No additional documentation required for a 2-line configuration fix.

## Verdict: APPROVED

The change is correct, minimal, and well-documented.

One consideration for the future: if the project adds actual deployment
targets (Kubernetes, cloud run, etc.), the lowercase image names will be
required for those integrations to work correctly with GHCR.

---
