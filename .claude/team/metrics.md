# Cycle metrics

One row per /team cycle, appended by retrospective-learner.

| date | task | test pass rate | review rejections | rework loops | A's corrections | skills used → helped / hurt / none |
|---|---|---|---|---|---|---|
| 2026-11-22 | C-1 cleanup (/team lite; 60d7d76, 74ca213, 4cc8d3b, 2b6570a), 5,309 lines removed | 778 passed (baseline 850; −72 belonged to deleted modules) | 0 (APPROVE first time, 4 NOTEs) | 0 | 2 (scope, before start; 0 after) | built-ins only → none |
| 2026-11-22 | Cycle 01: C-2/C-3/C-4 delisted tickers (/team full; 9ad5c0b..8234c04), ~170 prod lines, ~20 subagent runs | 875 passed (84 new tests, 81 red before build; build green first try) | 1 (BLOCK: WBD junk bar not trimmed, frontend types.ts field missing) | 1 | 0 (2 Gate-1 answers) | test-driven-development → helped; planning-and-task-breakdown → helped; interview-me / spec-driven-development → none (not invoked); /code-review → helped (sanitize/dedupe gap); /simplify → helped (63→36 lines); /security-review → n/a |

## Repeated workflows (project-skill candidates, for skill-upgrader + A)
- **Removal safety check** (3+ times: cleanup proposal A1–A7, the module-retire decision, cycle 01's paper-bot holdings check, lesson on `~/.hermes`): search backend, frontend, scripts, tests, CI, Docker, `~/.hermes`, cron and the hand-off docs before deleting or blocking anything.
- **Ticker lifecycle check** (3+ times in cycle 01: EQR→VMRK, BK→BNY, MMC→MRSH, plus 7 other classifications): classify as rename, stock merger, cash buyout or broken data by comparing the successor's closes and row counts with the cache.
