---
description: Have the skill-upgrader propose up to 3 evidence-based improvements to agents, CLAUDE.md or project skills, for A to approve one by one.
---

Run the **skill-upgrader** subagent. It reads `.claude/team/learnings.md`, `metrics.md`,
`decisions.md`, `changelog.md`, `.claude/agents/`, `.claude/skills/` and the Team Protocol
section of `CLAUDE.md`. It proposes **at most 3** changes, each a unified diff tied to
specific dated lessons.

Rules it must follow:
- A lesson seen 3 or more times becomes a permanent rule in the relevant agent file or in
  CLAUDE.md.
- A workflow repeated 3 or more times gets a draft project skill at
  `.claude/skills/<name>/SKILL.md`, made with skill-creator and including a test prompt.
- A skill marked "hurt" twice in `metrics.md` gets proposed for removal.
- Agent prompts stay under about 60 lines and the Team Protocol under 40 lines. Merge or
  prune to make room.

Show A the proposals and **stop**. Apply only the ones A approves, exactly as shown, and log
each in `.claude/team/changelog.md`
(`- YYYY-MM-DD file — change — lesson ref — approved by A`). Then commit on the current
feature branch, never on `main`.
