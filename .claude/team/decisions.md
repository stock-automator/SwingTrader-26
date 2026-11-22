# Decisions (lightweight ADR log)

Format: `## YYYY-MM-DD — Decision` then Why / Alternatives considered.

## 2026-11-22 — Keep the Mac awake rather than switch the bot to a catch-up scheduler
- **Why:** an existing `ai.hermes.keepawake` launchd agent (`caffeinate -ims`) already prevents sleep; no sleep events since it started. Fewest moving parts.
- **Alternatives:** launchd `StartCalendarInterval` jobs that run missed slots on wake (needs a market-open guard so a late run can't trade after hours); `pmset repeat` scheduled wake (needs sudo, A's call).

## 2026-11-22 — Work on local branch only until A pushes
- **Why:** no GitHub credentials on this Mac; A will push at the end.
- **Alternatives:** `gh auth login` now.

## 2026-11-22 — Commit the refreshed `data/raw` snapshot
- **Why:** the files are tracked; A asked to commit everything we do.
- **Alternatives:** leave the refresh uncommitted (dirty tree forever) or untrack `data/raw/`.

## (pending A's approval) — Primary workflow skill set
- Recorded once the skills table is approved.
