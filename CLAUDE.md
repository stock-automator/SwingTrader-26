# CLAUDE.md

Engineering contracts (strategy schema, RiskManager, engines, analytics) are in
`AGENTS.md`. Build and test: `ALLOW_DOWNLOADS=false pytest tests/ -q -n auto`,
`black`/`isort`/`flake8` on `backend tests`, and `npm run build` in `frontend/`.

## Team Protocol

All work on this repo goes through the agent team in `.claude/agents/`:
product-owner, domain-expert, customer, system-architect, tech-expert, developer,
tester, reviewer, retrospective-learner and skill-upgrader.

- **Read `.claude/team/learnings.md` first**, every role, every time.
- `/team [lite|standard|full] <task>`: Discover → Design → 🛑 Gate 1 → Test first →
  Build → Verify → Accept → 🛑 Gate 2 → Retro, scaled by risk (`.claude/commands/team.md`).
  **lite** = docs/config/small fixes (product-owner → tester → developer → reviewer).
  **standard** = most features (`/code-review` + reviewer; tech-expert only for a new
  library or API shape). **full** = orders, money, data integrity, strategy math (every
  role, `/simplify`, customer acceptance). At most 3 rework loops, then escalate.
- Discover has one critique round; the domain-expert checks facts against real data.
- `/retro` records lessons and metrics. `/upgrade` proposes at most 3 diffs to agents,
  skills or this file.
- Diffs touching orders, execution, alerts, config or secrets also get `/security-review`.
- **Human gates:** A approves the stories and design (Gate 1), the result (Gate 2),
  every `/upgrade` change, any skill install and any deletion.
- **Installed skills** (`.claude/skills/`, from addyosmani/agent-skills): interview-me,
  spec-driven-development, planning-and-task-breakdown, incremental-implementation and
  test-driven-development. Plus built-in `/code-review` and `/simplify`, and
  skill-creator at user level. Project skill: `ticker-lifecycle-check`.
- **git-guardrails** (`.claude/hooks/`) blocks force pushes, any push/commit/merge on
  `main`, `gh pr merge` and history rewrites. Agents may push feature branches and open
  PRs; A merges to `main`.
- Agents never edit their own prompts. Only skill-upgrader proposes changes, and only A
  approves them. A lesson seen 3 or more times becomes a rule.
- Keep it lean: no speculative code or docs. Prefer deleting or reusing. Agent prompts
  stay under about 60 lines.
- No secrets, tokens or account numbers in any `.claude/` file or log.
- Team memory: `.claude/team/` (learnings, decisions, backlog, metrics, changelog).
