# Team learnings

Every agent reads this before starting. One line per lesson, newest at the bottom:
`- YYYY-MM-DD [role] [bug|misunderstanding|process|domain|skill] lesson (evidence)`
Repeat lessons get `(+1 YYYY-MM-DD)`; 3+ occurrences get `⚑ RULE CANDIDATE`.

- 2026-11-22 [reviewer] [process] A "fix" was marked done without checking the real outcome: Sprint 7 closed the GHCR deploy issue, yet `deploy.yml` kept failing on every push (`| lower` is not valid Actions syntax). Verify the actual CI/run result, not just that a PR merged. (GitHub Actions run history)
- 2026-11-22 [tester] [bug] `tests/test_data_sync.py` wrote into the tracked `data/raw/AAPL.parquet`, so every test run dirtied the repo. Tests must use `tmp_path`, never `data/raw/`. (commit 00b3ace)
- 2026-11-22 [developer] [bug] A 50-day SMA was computed from `period="2mo"` (~42 bars), so it was always None and the main scoring rule never fired. Check window length ≥ indicator period. (`scripts/swing_setup_scan.py`, 00b3ace)
- 2026-11-22 [domain-expert] [domain] Telegram rejects messages over 4096 chars; the bot's daily summary had grown to 4199 and silently failed while logging "sent". Split long messages and log real send results. (`~/.hermes/bots/paper_trader/alerts.py`)
- 2026-11-22 [domain-expert] [domain] macOS cron does not run while the Mac sleeps and does not catch up; jobs silently skipped for weeks. Check power/sleep before blaming code. (pmset log)
- 2026-11-22 [system-architect] [misunderstanding] Logic referenced in docs (`can_take_position`) lives in the separate Hermes bot (`~/.hermes/bots/paper_trader`), not this repo. Search both before claiming something is missing.
- 2026-11-22 [developer] [skill] The upstream git-guardrails hook matched text anywhere in the command, so a commit message or heredoc that merely mentioned a blocked command was blocked. I fixed the hook to strip quotes and heredocs. When a guard blocks you, read why before rewording around it. (2 blocked commits while installing)
