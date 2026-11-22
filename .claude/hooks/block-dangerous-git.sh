#!/bin/bash
# PreToolUse guard for Bash: blocks destructive git commands and commits on main.
# Adapted from mattpocock/skills git-guardrails-claude-code (abeeaf9).
# A pushes manually; agents never push, rewrite history or commit to main.

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$COMMAND" ] && exit 0

block() {
  echo "BLOCKED by git-guardrails: '$COMMAND' ($1). A has not allowed this; ask A." >&2
  exit 2
}

DANGEROUS_PATTERNS=(
  'git[[:space:]]+push'
  'push[[:space:]].*--force'
  'reset[[:space:]]+--hard'
  'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*f'
  'git[[:space:]]+branch[[:space:]]+(-D|--delete[[:space:]]+--force)'
  'git[[:space:]]+(checkout|restore)[[:space:]]+(--[[:space:]]+)?\.([[:space:]]|$|;|&)'
  'git[[:space:]]+rebase'
  'git[[:space:]]+filter-(branch|repo)'
  'git[[:space:]]+commit[[:space:]].*--amend'
  'update-ref[[:space:]]+-d'
)
for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if printf '%s' "$COMMAND" | grep -qE "$pattern"; then
    block "matches '$pattern'"
  fi
done

# No commits or merges while on main.
if printf '%s' "$COMMAND" | grep -qE 'git[[:space:]]+(commit|merge|cherry-pick)'; then
  BRANCH=$(git -C "${CLAUDE_PROJECT_DIR:-.}" symbolic-ref --short HEAD 2>/dev/null)
  if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
    block "commits to $BRANCH need A's approval; use a feature branch"
  fi
fi

exit 0
