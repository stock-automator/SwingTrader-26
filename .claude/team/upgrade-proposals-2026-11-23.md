# /upgrade proposals (23 Nov 2026), awaiting A's approval, one by one

These were drafted by the skill-upgrader from `learnings.md` and `metrics.md`. Nothing is applied yet.

## 1. Risk tiers, one critique round, lighter Verify, one update per phase
**Files:** `.claude/commands/team.md` and the Team Protocol section of `CLAUDE.md` (which goes from 30 to 32 lines).
- `/team [lite|standard|full] <task>`. If no tier is given, the facilitator picks one by risk, says why, and goes one tier up when unsure. A can change it at Gate 1.
  - **lite:** docs, config and small fixes. product-owner → tester → developer → reviewer (plus `/code-review` if input handling changes) → Gate 2 → retro.
  - **standard (default):** the full set of steps. The tech-expert only joins for a new library or a new API shape. `/simplify` and the customer only run if the change is user-visible.
  - **full:** orders and execution, money and sizing, data integrity (cache, universe, deleting or blocking), strategy and benchmark math. Every step and every role.
- **Discover:** one parallel critique round. The domain-expert checks facts against repo data. The customer joins only if something user-visible changes. There is no customer pass before Gate 1.
- **Verify:** `/code-review`, then the reviewer gets those findings. `/security-review` runs on sensitive diffs as before. `/simplify` runs only for full or user-visible work. The 3-loop limit is unchanged.
- One update to A per phase (Discover+Design, Test+Build, Verify+Accept). Gates are unchanged.
- **Expected effect:** about 8–12 agent runs per standard cycle instead of about 20.
- **Trade-off:** `/simplify` helped last cycle (63→36 lines). If bloat gets past review in the next two standard cycles, it comes back.

## 2. Turn the new lessons into agent rules (5 agent files, all stay under 60 lines)
- **domain-expert:** gets **read-only Bash**, so it can read the parquet files. Rule: settle facts with data (row counts, dates, closes compared with the successor's history), cite the URLs, and write fetched data only to scratch. **This is a permission change.**
- **tester:** every data-file step in a design gets an assertion on the committed file. State the expected test-count change before modules are deleted.
- **system-architect:** before deleting or blocking anything, search backend, frontend, scripts, tests, CI, Docker, `~/.hermes`, cron and the hand-off docs. When an API response changes, list every consumer as a task: schemas, `types.ts`, `docs/api.md`, and docs examples pasted from real output.
- **product-owner:** use `interview-me` and `spec-driven-development` only for vague or multi-feature asks.
- **reviewer:** the facilitator runs `/code-review` and passes the findings on. This ends a double run.

## 3. New project skill: `ticker-lifecycle-check`
- **What it does:** a step-by-step data check for a ticker that has vanished. It inventories the cache, finds the corporate event, compares the successor's closes, and classifies the ticker as rename, stock_merger, cash_buyout or broken_data. It works out the `since` date and lists the actions, and every action needs a test and A's approval.
- **Wiring:** the domain-expert uses it for any delisted, renamed or missing ticker.
- **Test prompts:**
  - EQR/AVB must come out as a rename and a merger into VMRK, with nothing written under `data/`.
  - "Add a P/E column" must not trigger it.
- **Expected effect:** one domain run instead of three on the next universe change, which is useful right away for F4 (FI→FISV and the 54 watchlist differences).
