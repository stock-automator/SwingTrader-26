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

## 2026-11-22 — Security scope: no LAN auth; secrets stay local (A)
- **Why:** only A uses the home network. The real threat is leaking secrets to the outside world. Tokens and API keys live only in the git credential manager and local `.env` files, never in the repo, logs, Telegram or `.claude/`.
- **Alternatives:** a shared-token dependency on every state-changing endpoint (architect suggestion). Not needed for now. The order-guard bypass still gets fixed (S-1).

## 2026-11-22 — /security-review added to /team Verify for sensitive diffs (A agreed)
- **Why:** orders, alerts and config are where a mistake costs money or leaks secrets.

## 2026-11-22 — Cycle 01 Gate 1 decisions (A)
- Archive KVUE/WBD history to `data/delisted/`. Delete the untracked EA/AVB/EQR stubs. Add VMRK.
- Fix the old deny list: BK→BNY and MMC→MRSH are renames, so add the new tickers. CTRA, K and WBA are real delistings.

## 2026-11-23 — Batch 02 Gate 1 approved by A ("approved all")
- Q2: the app sends Telegram itself, using a token in the local `.env`.
- Q3: the backend runs always-on as a launchd service on 127.0.0.1.
- Q4: the paper bot becomes a pure executor of the daily setups.
- Q1 answered 2026-11-23 10:42: A keeps a USD balance, so set FX conversions per round trip to 0 by default. £ figures use the live GBP/USD rate. Superseded earlier note: Q1 (broker FX) not answered. Defaulting to a configurable `FX_COST_PCT=0.0015` with 2 conversions per round trip. Set conversions to 0 if A holds USD.
- The /upgrade proposals were also approved by A, but the facilitator's tool safety check blocked writing the reduced-review `team.md`. Nothing applied; A was asked to apply them or re-confirm in the thread.

## 2026-11-23 — £ figures use a live GBP/USD quote (A)
- **Why:** A asked for £ based on live prices.
- **How:** fetch only the latest quote, cache it ~60s, and fall back to the stored daily `GBPUSD=X` close (refreshed by the data sync). Show the rate's timestamp and source (live or daily).
- **Alternatives:** stored daily close only (stale intraday); full-history download per request (slow, the reviewer blocked it).

## 2026-11-23 — Backtests cap orders to available cash (A signed off at F2 Gate 2, 12:07)
- **Why:** backtesting.py silently cancelled oversized orders, which hid trades. Orders are now sized down to the cash available and counted in `skipped_signals` ("size capped to cash", "insufficient cash", "insufficient cash at fill").
- **Impact:** every existing backtest result changes. For example, KO with $1k went from 5 to 33 trades, and final values moved by up to 20%. The cap always applies; there's no opt-out.
- **Alternatives:** keep the old silent cancel (dishonest), or make the cap opt-in (two answers to one question).

## 2026-11-23 — Scheduled jobs run on UK time (Europe/London)
- **Why:** the brief is for A in the UK, and the customer asked for it at ~08:00 UK. The jobs are anchored to A's day, not to the US session. APScheduler handles UK daylight-saving changes. The brief still uses the last completed NYSE session for its data.
- **Alternatives:** New York time (the Gate 1 design note). It would drift by an hour during the weeks when UK and US clock changes don't line up.

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

## 2026-11-23 — Cycle 03 Gate 1 approved with defaults; leaner build loop (A)
- A said "start triggering what we discussed", which accepts the cycle-03 defaults: $10k, 1% risk, 25% max per position, 6 positions, 6% heat, 40-day time stop, long+short, 2016–22 tune / 2023+ test, 3 picks per strategy section.
- A asked to conserve tokens without losing quality. For this cycle, the developer writes the failing tests first (the TDD skill) and an independent reviewer verifies. The separate tester run is skipped, and every gate is kept.
