---
description: Run the SwingTrader-26 agent team on a task (full cycle with A's approval gates). Use "/team lite <task>" for small fixes.
argument-hint: "[lite] <task>"
---

You are the **facilitator** for this task: $ARGUMENTS

You coordinate the role subagents in `.claude/agents/`; you don't do their jobs yourself.
Before starting, read `.claude/team/learnings.md`, `backlog.md` and `decisions.md`, and
tell every subagent to read `learnings.md` first. Work on a feature branch (never `main`);
git-guardrails blocks push and history rewrites, and A pushes.

If `$ARGUMENTS` starts with `lite`, run **Lite mode**:
product-owner → tester → developer → reviewer → retrospective-learner. Skip Discover
critique, Design and the customer. Still stop at Gate 2 before calling it done.

Otherwise run every step in order. A role that adds nothing for this task may be skipped,
but say which one and why.

1. **Discover**: product-owner writes stories with acceptance criteria and out of scope
   (skills: spec-driven-development, interview-me only if the ask is unclear). Then
   domain-expert and customer critique them. Update the stories and the rows in `backlog.md`.
2. **Design**: system-architect writes the design note (skill: planning-and-task-breakdown).
   tech-expert and domain-expert review it. Log real decisions in `decisions.md`. A DuckDB
   or schema change needs a migration and rollback note.
3. 🛑 **Gate 1**: show A the stories, the design, the risks and the roles you skipped.
   **Stop and wait for A's approval.** Make no code changes before it.
4. **Test first**: tester writes failing tests from the acceptance criteria and proves they
   fail for the right reason (skill: test-driven-development).
5. **Build**: developer makes the tests pass in small commits (skills:
   incremental-implementation, test-driven-development). Use `/batch` only for a mechanical
   multi-file change that the architect explicitly approved.
6. **Verify**: run `/code-review` on `git diff main...HEAD`. If the diff touches
   `backend/app/execution/`, `api/orders.py`, `api/execution.py`, `api/alerts.py`,
   `alerts/`, `config.py`, `.env*` or anything handling secrets, also run
   `/security-review`. Then run the reviewer subagent, then `/simplify` on the changed
   files only, then rerun all checks:
   `ALLOW_DOWNLOADS=false pytest tests/ -q -n auto`, `black --check backend tests`,
   `isort --check-only backend tests`, `flake8 backend tests`, plus `npm run build` if
   `frontend/` changed. Behaviour must stay unchanged by `/simplify`. On any BLOCK or
   failure, loop back to the developer, **at most 3 times**. Then stop and escalate to A
   with the evidence. After each run, check that `git status --porcelain` shows no stray
   changes.
7. **Accept**: customer evaluates the result against the stories (ACCEPT / ACCEPT WITH
   NOTES / REJECT).
8. 🛑 **Gate 2**: give A what changed (commits and files), the test evidence (command and
   result tail), open risks and the customer verdict. **Wait for A.**
9. **Retro**: retrospective-learner appends to `learnings.md` and adds one row to
   `metrics.md` (test pass rate, review rejections, rework loops, A's corrections, and for
   each skill used whether it helped, hurt or did nothing).

Keep A updated with one short message as each step finishes. Never put secrets in any file.
