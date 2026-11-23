#!/bin/bash
# PreToolUse guard for Bash: blocks destructive git commands and commits on main.
# Adapted from mattpocock/skills git-guardrails-claude-code (abeeaf9).
# A pushes manually; agents never push, rewrite history or commit to main.
#
# Only real git invocations are checked: heredoc bodies and quoted strings
# (commit messages, echo text, python -c code) are stripped first, and a
# pattern must start at a command position (line start, ; & | ( or `env`).

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$COMMAND" ] && exit 0

# 1) drop heredoc bodies, 2) drop '...' and "..." strings.
STRIPPED=$(printf '%s\n' "$COMMAND" | awk '
  skip { if ($0 == tag || $0 ~ "^[ \t]*" tag "$") { skip = 0 }; next }
  {
    line = $0
    if (match(line, /<<-?[ \t]*["'"'"']?[A-Za-z_][A-Za-z0-9_]*/)) {
      t = substr(line, RSTART, RLENGTH)
      gsub(/<<-?[ \t]*["'"'"']?/, "", t)
      tag = t; skip = 1
    }
    print line
  }' | sed -E "s/'[^']*'//g; s/\"([^\"\\\\]|\\\\.)*\"//g")

block() {
  echo "BLOCKED by git-guardrails: $1. A has not allowed this; ask A." >&2
  exit 2
}

START='(^|[;&|(]|&&|\|\||then|do)[[:space:]]*'
GIT="${START}git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+"

PUSH="${GIT}push([[:space:]]|$)"

DANGEROUS_PATTERNS=(
  # Pushing feature branches is allowed (A, 2026-11-23). Still blocked:
  # force pushes, deleting/mirroring remote refs, and any push to main/master.
  "${PUSH}(.*[[:space:]])?(-f|--force|--force-with-lease|--force-if-includes|--mirror|--all|--delete|-d)([[:space:]=]|$)"
  "${PUSH}(.*[[:space:]])?\+[^[:space:]]+"
  "${PUSH}(.*[[:space:]])?([^[:space:]]*:)?(refs/heads/)?(main|master)([[:space:]]|$)"
  # `gh pr merge` is allowed (A, 2026-11-23) but never with --admin, which
  # bypasses required checks; rule: merge only after all CI checks pass and
  # A has confirmed Gate 2 for what the PR contains.
  "${START}gh[[:space:]]+pr[[:space:]]+merge(.*[[:space:]])?--admin"
  "${GIT}reset[[:space:]]+(.*[[:space:]])?--hard"
  "${GIT}clean[[:space:]]+(.*[[:space:]])?-[a-zA-Z]*f"
  "${GIT}branch[[:space:]]+(.*[[:space:]])?(-D|--delete[[:space:]]+--force)"
  "${GIT}(checkout|restore)[[:space:]]+(--[[:space:]]+)?\.([[:space:]]|$|;|&)"
  "${GIT}rebase"
  "${GIT}filter-(branch|repo)"
  "${GIT}commit[[:space:]]+(.*[[:space:]])?--amend"
  "${GIT}update-ref[[:space:]]+-d"
)
for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if printf '%s' "$STRIPPED" | grep -qE "$pattern"; then
    block "destructive git command"
  fi
done

# No commits, merges or pushes while on main (a bare `git push` there pushes main).
if printf '%s' "$STRIPPED" | grep -qE "${GIT}(commit|merge|cherry-pick|push)"; then
  BRANCH=$(git -C "${CLAUDE_PROJECT_DIR:-.}" symbolic-ref --short HEAD 2>/dev/null)
  if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
    block "commits to $BRANCH need A's approval; use a feature branch"
  fi
fi

exit 0
