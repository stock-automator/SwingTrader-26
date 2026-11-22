# Decisions (lightweight ADR log)

Format: `## YYYY-MM-DD — Decision` then Why / Alternatives considered.

## 2026-11-22 — Keep the Mac awake rather than switch the bot to a catch-up scheduler
- **Why:** an existing `ai.hermes.keepawake` launchd agent (`caffeinate -ims`) already prevents sleep; no sleep events since it started. Fewest moving parts.
- **Alternatives:** launchd `StartCalendarInterval` jobs that run missed slots on wake (needs a market-open guard so a late run can't trade after hours); `pmset repeat` scheduled wake (needs sudo, A's call).

## 2026-09-20 — Infra facts carried over from the old `docs/agent/` notes
- Outbound Telegram from the Hermes side works with the `hermes send` CLI and needs no gateway. The app's own alerts subsystem has Telegram support but no bot token configured.
- Hermes routes models through OmniRoute. Groq was failing with an invalid API key, and OmniRoute routing for Claude Code is not configured (not needed).
- Short-position support exists in the backtest engine: `run_backtest(..., direction=-1)`, from commit bfc6ea3.
- Sprint 7's deploy "fix" (`| lower`) was itself broken. It was really fixed on 2026-11-22 (00b3ace).

## 2026-11-22 — Retire data/agent.py, analytics/console.py and analytics/llm_reporter.py (approved by A)
- **Why:** nothing in the repo, CI, Docker, `~/.hermes` or cron imports or runs them. The Sprint 6 audit kept them as "standalone tools", but none has been used since the FastAPI refactor. Downloads now go through `parquet_manager` and `/api/v1/data/sync`, which refreshed 505 tickers in 104s with no VPN rotation. Recoverable from git history (last commit before removal) if ever needed.
- **Alternatives:** keep them and fix their doc paths (the Sprint 6 recommendation).

## 2026-11-22 — Work on local branch only until A pushes
- **Why:** no GitHub credentials on this Mac; A will push at the end.
- **Alternatives:** `gh auth login` now.

## 2026-11-22 — Commit the refreshed `data/raw` snapshot
- **Why:** the files are tracked; A asked to commit everything we do.
- **Alternatives:** leave the refresh uncommitted (dirty tree forever) or untrack `data/raw/`.

## 2026-11-22 — Primary workflow: addyosmani/agent-skills (approved by A)
- **Why:** it works with any stack, each skill is opt-in, and it adds no session hook. Its interview, spec, plan, incremental build and TDD skills map directly onto /team's Discover, Design, Test-first and Build steps. Installed by copying 5 skills (pinned at 8c99fa3) into `.claude/skills/`.
- **Alternatives:** obra/superpowers as primary. Rejected: its SessionStart hook forces skill invocation in every session and would fight the /team facilitator, and `executing-plans` relies on helper scripts and git worktrees. It may be used later for specific sub-skills (requesting/receiving-code-review) if a retro shows the need.

## 2026-11-22 — Use built-in /simplify instead of the code-simplifier plugin (approved by A)
- **Why:** it does the same job and is already present. The plugin's prompt is tuned for TypeScript/React and pins `model: opus`.
- **Alternatives:** `/plugin install code-simplifier@claude-plugins-official`.

## 2026-11-22 — git-guardrails as a project PreToolUse hook, extended
- **Why:** it enforces A's rules (no push, no history rewrite, no commits to main) mechanically, not by prompt.
- **Changes from upstream:**
  - Added a main-branch check.
  - Fixed the `checkout \.` false positive, which also blocked paths like `.github/...`.
  - Strip heredocs and quoted strings and match only at command positions. Otherwise commit messages and scripts that merely *mention* a blocked command got blocked.
- Test cases are in `.claude/hooks/test-block-dangerous-git.sh`.
- **Alternatives:** the upstream script unchanged.
