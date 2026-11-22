# CLAUDE.md

Engineering contracts (strategy schema, RiskManager, engines, analytics) are in
`AGENTS.md`. Build and test: `ALLOW_DOWNLOADS=false pytest tests/ -q -n auto`,
`black`/`isort`/`flake8` on `backend tests`, and `npm run build` in `frontend/`.

## Team Protocol

All work on this repo goes through the agent team in `.claude/agents/`:
product-owner, domain-expert, customer, system-architect, tech-expert, developer,
tester, reviewer, retrospective-learner and skill-upgrader.

- **Read `.claude/team/learnings.md` first**, every role, every time.
- `/team <task>` runs the full cycle: Discover → Design → 🛑 Gate 1 → Test first →
  Build → Verify (`/code-review`, reviewer, `/simplify`, all tests; at most 3 rework
  loops, then escalate) → Accept → 🛑 Gate 2 → Retro.
- `/team lite <task>` is for small fixes: product-owner → tester → developer → reviewer
  → retro, still ending at Gate 2.
- `/retro` records lessons and metrics. `/upgrade` proposes at most 3 diffs to agents,
  skills or this file.
- Diffs touching orders, execution, alerts, config or secrets also get `/security-review`.
- **Human gates:** A approves the stories and design (Gate 1), the result (Gate 2),
  every `/upgrade` change, any skill install and any deletion.
- **Installed skills** (`.claude/skills/`, from addyosmani/agent-skills): interview-me,
  spec-driven-development, planning-and-task-breakdown, incremental-implementation and
  test-driven-development. Plus built-in `/code-review` and `/simplify`, and
  skill-creator at user level.
- **git-guardrails** (`.claude/hooks/`) blocks push, history rewrites and commits on
  `main`. Work on a feature branch; A pushes.
- Agents never edit their own prompts. Only skill-upgrader proposes changes, and only A
  approves them. A lesson seen 3 or more times becomes a rule.
- Keep it lean: no speculative code or docs. Prefer deleting or reusing. Agent prompts
  stay under about 60 lines.
- No secrets, tokens or account numbers in any `.claude/` file or log.
- Team memory: `.claude/team/` (learnings, decisions, backlog, metrics, changelog).
