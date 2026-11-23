# Team changelog

Every approved change to agents, skills or CLAUDE.md:
`- YYYY-MM-DD file — change — lesson ref — approved by A`

- 2026-11-22 .claude/agents/*.md — initial 10-role team created from A's spec — n/a — requested by A
- 2026-11-22 .claude/commands/team.md, CLAUDE.md — add /security-review to Verify for orders/alerts/config/secrets diffs — expert suggestion — agreed by A
- 2026-11-23 .claude/commands/team.md, CLAUDE.md — risk tiers (lite/standard/full), one critique round, lighter Verify, per-phase updates — facilitator lesson (cycle 01 cost) — approved by A
- 2026-11-23 .claude/agents/{domain-expert,tester,system-architect,product-owner,reviewer}.md — verify with data (domain-expert gains read-only Bash), test every data step, list API consumers + removal search, skills only for vague asks, reviewer receives /code-review findings — cycle 01 lessons — approved by A
- 2026-11-23 .claude/skills/ticker-lifecycle-check/SKILL.md (new), CLAUDE.md — data-first rename/merger/buyout classification — repeated workflow (3+ in cycle 01) — approved by A
- 2026-11-23 backend/app/quant/engine.py — every backtest caps orders to available cash (existing results change) — F2 reviewer WARNING 1 — approved by A
- 2026-11-23 .claude/hooks/block-dangerous-git.sh, CLAUDE.md — agents may push feature branches and open PRs; force push, push/commit/merge on main and gh pr merge stay blocked — A's request in the thread — approved by A
