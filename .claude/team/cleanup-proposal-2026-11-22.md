# Cleanup proposal (22 Nov 2026), awaiting A's OK

Nothing is deleted until A approves. Each item was checked with a search of `backend/`, `frontend/src/`, `scripts/`, `tests/`, CI and Docker for anything that uses it. About **5,200 lines** would go in total.

## A. Unused code: safe to delete

| # | Remove | Lines | Evidence it's unused |
|---|---|---|---|
| A1 | `backend/app/data/agent.py` | 886 | The old Ollama + VPN download agent. Nothing imports it, and it has 0% test coverage. Price downloads now go through `data/loader.py` and `quant/data/parquet_manager.py`. |
| A2 | `tests/test_agent.py` | 1,115 | Imports nothing from the project, so its 52 tests only test their own copies of the code. |
| A3 | `backend/app/analytics/llm_reporter.py` and `tests/test_llm_reporter.py` | 354 | Plain-English report via local Ollama. Used only by its own test, with no API endpoint or UI. |
| A4 | `backend/app/analytics/console.py` and `tests/test_console.py` | 137 | Terminal table formatting for CLI scripts that no longer exist. Used only by its own test. |
| A5 | `config/trading_config.json` | 13 | Nothing reads it, and it names a strategy (`supertrend_rsi`) that doesn't exist. |
| A6 | `.claude/agents/code_reviewer.md` | 97 | Replaced by the new `reviewer` agent, which carries the same quant checklist. |
| A7 | `.hermes_cache/` (untracked) | 61 | An old copy of the Hermes Telegram sender with your chat id hard-coded. The live one is in `~/.hermes/scripts/`. |

## B. Stale docs: delete

| # | Remove | Lines | Why |
|---|---|---|---|
| B1 | `docs/agent/` (8 files: BLOCKERS, CURRENT_PLAN, DECISIONS, HANDOFF, PROGRESS, CODE_REVIEW_SPRINT_7, QE_SPRINT_7, REPOSITORY_REPORT) | ~990 | Hand-off notes from the earlier AI dev loop. They're out of date (HANDOFF says Sprint 9 is blocked, but it was merged), and `.claude/team/` replaces them. |
| B2 | `docs/SPRINT_6_AUDIT.md`, `docs/SPRINT_8_AUDIT.md` | 753 | One-off sprint audits, and their findings are already fixed. Git history keeps them. |
| B3 | `docs/code_review.md` | 296 | A one-time "multi-persona review" from an earlier sprint. The team's reviewer and retro replace it. |
| B4 | `SPRINT_CHANGELOG.md` | 360 | A sprint-by-sprint diary that duplicates git history and the README Status section. |

Links to removed docs in `README.md` and `docs/architecture.md` get updated in the same commit.

## C. Shrink or fix: edit, no deletion

| # | Change |
|---|---|
| C1 | **AGENTS.md:** rewrite every `src/...` path to `backend/app/...` and drop the sections for removed modules (llm_reporter, console, the CLI-script logging section). Aim for about 250 lines instead of 369. |
| C2 | **pytest.ini:** remove the 9 markers used only by `test_agent.py` (vpn, ollama, checkpoint…). Remove the ignored `[coverage]` section, which points at a `src` folder that no longer exists. Either remove `timeout = 30` or add `pytest-timeout` so it actually works (I'd **add** it, so a hung test can't hang CI). |
| C3 | **README.md:** trim the long "Status" history (about 90 lines, sprint by sprint) to a 5-line summary. Remove links to deleted docs. |
| C4 | **Untracked `scripts/day_setup_scan.py`:** commit it next to the swing scan so both Hermes scans are versioned. Otherwise delete it. **I'd commit it.** |

## D. Keep (flagged, needs your call)

| Item | Why I'd keep it |
|---|---|
| `docs/trading_log_template.md` | It looks like your own printable trade journal ("EDGE JOURNAL"). Nothing references it, but it's personal, so delete it only if you say so. |
| `scripts/generate_demo_videos.py` (203 lines) | It records the README demo videos with Playwright. Nothing references it. Keep it if you'll refresh the screenshots and videos, otherwise delete. |
| `matplotlib` dependency | Used only by `save_equity_curve_chart` in `quant/metrics.py`. It's a heavy install for one function. That's a separate decision, not part of this pass. |

## How it would run
As a `/team lite` cycle (it's mechanical):
1. The tester confirms the full suite is green before the change.
2. The developer deletes in one commit per group (A, B, C).
3. After each commit, I'll run `pytest`, `black`/`isort`/`flake8` and `npm run build`, and check that nothing imports a removed file.
4. The reviewer checks the diff.
5. Gate 2 summary for you.
