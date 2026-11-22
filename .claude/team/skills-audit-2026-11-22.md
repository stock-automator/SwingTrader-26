# Skills audit (22 Nov 2026), awaiting A's OK

Checked against this repo: Python 3.12 / FastAPI / pandas backend, React 19 + TypeScript + Vite frontend, local DuckDB (no SQL server, no migrations). I read each candidate's README install section, the SKILL.md files and any bundled scripts or hooks, from shallow clones pinned at these commits: addyosmani 8c99fa3, superpowers 22f0b46, mattpocock abeeaf9, anthropics/skills e4bb5d9, vercel-labs 5a37a63, planetscale 999045c.

## Recommendation: install 2 things now

| Skill | Fits? | Why | Scope / install | Risks found |
|---|---|---|---|---|
| **addyosmani/agent-skills**: `interview-me`, `spec-driven-development`, `planning-and-task-breakdown`, `incremental-implementation`, `test-driven-development` | ✅ **Primary workflow** | Works with any stack. Each skill is opt-in, and the five map one-to-one onto Discover, Design, Build and Test. | Project. Copy the 5 folders into `.claude/skills/`. That's what the README's `npx skills add … --skill X` does, without running npx from the internet. | No scripts in these 5 skills. The plugin's session-start hook is not used in Claude Code, and we're not installing the plugin anyway. The TDD examples are JavaScript-flavoured, but the method is the same for pytest. |
| **mattpocock git-guardrails-claude-code** | ✅ | Enforces your rules: no push, force push, `reset --hard`, `clean -f` or `branch -D`. | Project. `.claude/hooks/block-dangerous-git.sh` plus a `PreToolUse` hook in `.claude/settings.json`. | It runs a small shell script before **every** Bash command; it reads the command with `jq`, which is installed. It blocks all `git push`, which is fine because you push. I'll add one rule (block `git commit` while on `main`) and fix one false positive: `git checkout \.` also matches `git checkout .github/…`. It's a persistent settings change, so it needs your OK. |
| **skill-creator** (Anthropic) | ✅ | Needed by skill-upgrader. | **Already installed** at user level, so nothing to do. | Its eval scripts start `claude -p` sub-runs, which cost usage. They only run when someone deliberately evaluates a skill. |
| **code-simplifier** (official marketplace) | ⚠️ Overlaps | It overlaps the built-in `/simplify` you already have. Its prompt is tuned for TypeScript/React (ES modules, Props types), while most changed code here is Python. | It would be a project-scope plugin: `/plugin install code-simplifier@claude-plugins-official`. | No scripts, but it forces `model: opus`. **My suggestion: skip it and have the reviewer use `/simplify` on changed files.** Say if you'd rather have the plugin. |

## Deferred until a retro shows a need (lean start)

| Skill | Fits? | Why deferred | Risks |
|---|---|---|---|
| obra/superpowers (planning, TDD, requesting/receiving-code-review) | ⚠️ Not as primary | The plugin adds a SessionStart hook that injects "if there's a 1% chance a skill applies you MUST invoke it" into every session. That would fight the `/team` facilitator and agent-skills. `executing-plans` runs bash helper scripts (`task-start`/`task-done`) that write a ledger and expect git worktrees. Its review skills overlap the built-in `/code-review`. | Hook on every session. Shell scripts. |
| anthropics/skills `frontend-design` | ✅ (has a UI) | Not needed until a UI story arrives. | None (SKILL.md only). |
| vercel-labs `react-best-practices`, `composition-patterns`, `web-design-guidelines` | ✅ (React) | Many rules are Next.js-specific, while this is a Vite single-page app. They're large rule sets, so add them for the first big UI change. | `web-design-guidelines` has an optional `curl` step to refresh its rules. |

## Not installing

| Skill | Why |
|---|---|
| mattpocock `write-a-prd`, `request-refactor-plan` | **They no longer exist.** Its changelog says they were replaced by `to-spec` and `improve-codebase-architecture`. Both are manual-only, and `to-spec` publishes to an issue tracker. The product-owner uses agent-skills `spec-driven-development` + `interview-me` instead. |
| mattpocock `tdd` | It duplicates agent-skills TDD. Two TDD skills would conflict. |
| planetscale/skills | No fit: there's no MySQL/Postgres and no schema migrations. The app uses a local DuckDB file. |

## Gap for skill-creator later
There's no existing skill for quant work. A project skill such as `backtest-honesty-check` (lookahead, costs, benchmark against SPY/buy-and-hold, NaN warm-up, survivorship) is worth drafting once retros show the same review findings repeating.

## Role mapping once approved
- product-owner: `interview-me`, `spec-driven-development`
- system-architect: `planning-and-task-breakdown`
- developer: `incremental-implementation`, `test-driven-development`, plus git-guardrails (always on)
- tester: `test-driven-development`
- reviewer: `/code-review`, `/simplify` (or code-simplifier), on changed files only
- skill-upgrader: `skill-creator`
- domain-expert, customer, tech-expert: none for now
