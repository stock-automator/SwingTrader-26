# Progress Log

**Last updated:** 2026-09-20

## 2026-09-20

### Infrastructure Inspection (COMPLETE)
- Inspected Hermes, Telegram, Claude Code, OmniRoute, Git, project
- Telegram: connected and working (via `hermes send`)
- OmniRoute: 4 providers, 2 working (Claude, aihorde), 1 broken (Groq)
- Repository: clean, 852 tests passing, lint not yet run locally

### Documents Created
- `docs/agent/CURRENT_PLAN.md` — current plan and status
- `docs/agent/DECISIONS.md` — decisions log
- `docs/agent/BLOCKERS.md` — blockers log
- `docs/agent/REPOSITORY_REPORT.md` — comprehensive repository report (18KB)

### Iteration 1: Short-Position Engine Support (COMPLETE)
- **File changed:** `backend/app/quant/engine.py`
- **Test file changed:** `tests/test_backtester.py`
- **New parameter:** `direction` on `run_backtest()`
- **Tests added:** 4 new tests in `TestShortDirection`
- **All tests:** 856 passed (852 existing + 4 new)
- **Lint:** black ✓, isort ✓, flake8 ✓
- **Commit:** `bfc6ea3` — feat(engine): add short-position support via direction parameter
- **Pushed:** Yes (using GitHub PAT)
- **CI:** ✅ GREEN — all 4 jobs passed (lint, frontend, test 3.12, test 3.11)
- **Telegram notification:** Sent via `hermes send`

### Infrastructure Blockers Resolved
- **Telegram consent:** Resolved — `hermes send` CLI works for outbound notifications without gateway
- **GitHub credentials:** Resolved — PAT stored in `~/.git-credentials` (mode 600), push working
- **CI verification:** Resolved — CI run #39 passed (commit bfc6ea3)

### Deploy Workflow Failure (Pre-existing, Unrelated to This Change)
- Deploy run #13 failed: `invalid tag "ghcr.io/stock-automator/SwingTrader-26-backend:...": repository name must be lowercase`
- Root cause: GHCR requires lowercase repo names, workflow uses `${{ github.repository }}-backend` which expands to `stock-automator/SwingTrader-26-backend`
- This is a pre-existing workflow bug, not introduced by this change

### Remaining Blockers
- **OmniRoute Claude Code profile setup:** OmniRoute server requires API key for `setup-claude`. No OmniRoute API key configured (only a Groq test key exists). Claude Code works standalone without OmniRoute routing.
- **Claude Code skills:** Evaluated marketplace — code-review (PR-focused, not applicable), code-simplifier (JS-focused, not applicable to Python), feature-dev (interactive, not autonomous). Project's existing `code_reviewer.md` agent is more appropriate for this repo.
- **Groq provider:** Still broken (invalid API key)

### Sprint 7: Deploy Workflow GHCR Fix (COMPLETE)
- **File changed:** `.github/workflows/deploy.yml`
- **Change:** Use lowercase GHCR image names via `github.repository_owner | lower` / `github.event.repository.name | lower`
- **PR:** #15 — created, CI green (4/4), Code Review APPROVED, QE PASSED, merged
- **Commit:** `25749bd` (squash-merged)
- **CI:** ✅ GREEN — lint ✓, frontend ✓, test (3.11) ✓, test (3.12) ✓
- **Telegram notification:** Sent via `hermes send`

### Decisions Updated
- Deploy workflow GHCR fix: Implemented and merged (was previously deferred as pre-existing)
