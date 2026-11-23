# Cycle metrics

One row per /team cycle, appended by retrospective-learner.

| date | task | test pass rate | review rejections | rework loops | A's corrections | skills used → helped / hurt / none |
|---|---|---|---|---|---|---|
| 2026-11-22 | C-1 cleanup (/team lite; 60d7d76, 74ca213, 4cc8d3b, 2b6570a), 5,309 lines removed | 778 passed (baseline 850; −72 belonged to deleted modules) | 0 (APPROVE first time, 4 NOTEs) | 0 | 2 (scope, before start; 0 after) | built-ins only → none |
| 2026-11-22 | Cycle 01: C-2/C-3/C-4 delisted tickers (/team full; 9ad5c0b..8234c04), ~170 prod lines, ~20 subagent runs | 875 passed (84 new tests, 81 red before build; build green first try) | 1 (BLOCK: WBD junk bar not trimmed, frontend types.ts field missing) | 1 | 0 (2 Gate-1 answers) | test-driven-development → helped; planning-and-task-breakdown → helped; interview-me / spec-driven-development → none (not invoked); /code-review → helped (sanitize/dedupe gap); /simplify → helped (63→36 lines); /security-review → n/a |
| 2026-11-23 | Batch 02 F1 guarded orders (full) | 56 new, green | 1 (BLOCK: alpaca attrs; EXIT bypass) | 1 | 0 (+1 own regression from /simplify) | TDD → helped; /security-review → helped; /code-review → helped; /simplify → hurt |
| 2026-11-23 | Batch 02 F6 UK costs + live £ (standard) | 46 new, green | 2 (no GBP cache / full download; untested offline branch, no negative cache) | 2 | 1 design question (why not live?), improved design | TDD → helped |
| 2026-11-23 | Batch 02 F2 fair backtests (full) | 63 new, green | 2 | 2 | 1 sign-off (old results changed) | TDD → helped; /code-review → helped (10 issues) |
| 2026-11-23 | Batch 02 F3a daily brief (full) | 71 new, green | 2 (20-min scan, token leak; ignored 2.5R gate) | 2 | 0 | TDD → helped; /security-review → helped |
| 2026-11-23 | Batch 02 F3b-1 scheduling (full) | green (count not given) | 1 (concurrent rebuilds, brief ordering) | 1 | 0 | TDD → helped |
| 2026-11-23 | Batch 02 F4 universe (full), 8 files migrated | 43 new, green | 1 (untested status file, silent drops) | 1 | 0 | TDD → helped |
| 2026-11-23 | Batch 02 F5 journal + weekly (full) | 49 new, green after clock/ISO8601 fix | 0 recorded | 0 | 0 | TDD → helped |

Batch 02 overall: A's corrections were mostly "keep going/confirm", plus one explicit "too slow" (13:21).

## Repeated workflows (project-skill candidates, for skill-upgrader + A)
- **Removal safety check** (3+ times: cleanup proposal A1–A7, the module-retire decision, cycle 01's paper-bot holdings check, lesson on `~/.hermes`): search backend, frontend, scripts, tests, CI, Docker, `~/.hermes`, cron and the hand-off docs before deleting or blocking anything.
- **Pre-reviewer gate** (7 of 7 features in batch 02): tests first, then /code-review and /security-review, then the reviewer, with BLOCKs recurring on untested branches and uncached or unbounded calls. Candidate for a project skill (a checklist: cache, timeout, negative cache, concurrency, secrets in errors, a test per new file or branch, pinned-library check).
- **Ticker lifecycle check** (3+ times in cycle 01: EQR→VMRK, BK→BNY, MMC→MRSH, plus 7 other classifications): classify as rename, stock merger, cash buyout or broken data by comparing the successor's closes and row counts with the cache.
