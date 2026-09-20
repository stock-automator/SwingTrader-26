# Current Plan — Autonomous Development Supervisor

**Last updated:** 2026-09-20  
**Status:** Phase 2 — Sprint 7 COMPLETE, waiting for next PO task

## Objective

Build an autonomous development supervisor for ~/SwingTrader-26 capable of:

```
PLAN → IMPLEMENT → REVIEW → TEST → FIX → CI VERIFY → REPORT
```

Operating unattended for long periods, contacting via Telegram only when human input is genuinely required.

## Current Phase

**Phase 2: Sprint 7 COMPLETE — Waiting for next PO task**

### Done
- [x] Inspect Hermes configuration
- [x] Inspect Telegram/gateway status — **Telegram IS connected and working**
- [x] Inspect Claude Code installation — v2.1.278 installed
- [x] Inspect OmniRoute — v16.3.1 running, 4 providers (Claude, aihorde, Groq, OpenCode)
- [x] Inspect OmniRoute providers — Claude ✓, aihorde ✓, Groq ✗ (invalid key), OpenCode ✓
- [x] Inspect repository — clean main branch, 852 tests passing
- [x] Inspect GitHub CI — ci.yml + deploy.yml workflows present
- [x] Inspect project architecture — FastAPI backend + React frontend, quant engine
- [x] Create REPOSITORY_REPORT.md
- [x] Create persistent PLAN MODE docs (CURRENT_PLAN.md, DECISIONS.md, BLOCKERS.md)
- [x] Execute first iteration (PLAN → CODE → REVIEW → TEST → FIX → REPORT)
- [x] **Sprint 7: Fix GHCR image name casing in deploy workflow** — COMPLETE
  - PR #15 created, CI green (4/4), Code Review APPROVED, QE PASSED, merged

### In Progress
- [ ] Determine next approved PO task

### Not Started
- [ ] Autonomous execution loop scripts (if needed)
- [ ] OmniRoute fallback routing configuration (Claude Code works standalone)
- [ ] Claude Code skills (evaluated — not needed for current workflow)

## Iteration 1 Results

### What was implemented
**Short-position support for the backtest engine** (`backend/app/quant/engine.py`)

Added `direction` parameter to `run_backtest()`:
- `direction=None` (default): long-only, existing behavior preserved exactly
- `direction=1`: long-only (explicit)
- `direction=-1`: enables short entries — `signal=-1` opens a short when flat, `signal=1` closes an open short

### Files changed
- `backend/app/quant/engine.py` — Added `direction` parameter, direction-aware `_SignalAdapter`
- `tests/test_backtester.py` — Added `TestShortDirection` class with 4 tests

### Tests run
- All 856 tests pass (852 existing + 4 new)
- Lint: black ✓, isort ✓, flake8 ✓
- Full suite: 856 passed, 148 warnings in 28.77s

### Commit
- `bfc6ea3` — feat(engine): add short-position support via direction parameter

### CI Status
- **BLOCKED** — No GitHub push credentials configured locally. CI cannot be triggered.
- The commit is local only. CI would pass based on local validation.

## Key Decisions

### Telegram
- **Status:** Already connected and working (bot token configured, user 8970662198 authorized)
- **Action:** Use existing Telegram integration for all notifications. No new setup required.

### OmniRoute Providers
- **Claude (cc):** Primary — works, authenticated via OAuth
- **aihorde (aihorde):** Working — free tier, usable for cheaper tasks
- **Groq (groq):** **FAILED** — invalid API key, needs credential rotation
- **OpenCode (oc):** Listed but untested

### First Iteration
- **Decision:** Implement short-position support for backtest engine
- **Rationale:** Well-documented gap in AGENTS.md, bounded scope, high value, low risk
- **Result:** Successful — 4 new tests, all green, lint clean

### GitHub Push
- **Issue:** No SSH/HTTPS credentials configured for `github.com`
- **Impact:** Cannot push to trigger CI; commit is local only
- **Resolution required:** User must configure git authentication or provide credentials

## Verification Criteria

- [x] All tests pass (856)
- [x] Lint passes (black, isort, flake8)
- [ ] CI green on push — BLOCKED (no push credentials)
- [x] Code reviewed (self-review + lint)
- [x] Telegram notification sent on completion

## Next Action

Pause for human input: GitHub push credentials required to trigger CI and complete the CI VERIFY step of the cycle.

Alternatively, if CI is not required for this iteration, mark as complete and proceed to Phase 2 (autonomous execution loop scripts, Claude Code skills evaluation, OmniRoute routing).
