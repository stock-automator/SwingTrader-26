---
description: Run the retrospective-learner on the last /team cycle (default) or on the whole session.
argument-hint: "[session]"
---

Run the **retrospective-learner** subagent on: ${ARGUMENTS:-the last /team cycle}.

Give it:
- the cycle's stories, design note, test report, reviewer findings and customer verdict,
- the commits made (`git log --oneline main..HEAD`),
- every correction A made during the cycle, quoted.

It appends lessons to `.claude/team/learnings.md`, adds one row to `.claude/team/metrics.md`,
and marks repeated lessons (3 or more occurrences) as `⚑ RULE CANDIDATE`. It writes only inside
`.claude/team/`. Show A its 3-line summary and what it appended.
