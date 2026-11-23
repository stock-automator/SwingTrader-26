#!/bin/bash
H=/Users/ai-bot/SwingTrader-26/.claude/hooks/block-dangerous-git.sh
fail=0
t() { # expected_exit command
  out=$(jq -n --arg c "$2" '{tool_input:{command:$c}}' | CLAUDE_PROJECT_DIR="${3:-/Users/ai-bot/SwingTrader-26}" "$H" 2>&1); rc=$?
  if [ "$rc" != "$1" ]; then echo "FAIL want=$1 got=$rc :: $2"; fail=1; else echo "ok   $rc :: ${2:0:60}"; fi
}
# must block
t 0 "git push -u origin x"
t 0 "git push -u origin claude/project-thread-w8vytm"
t 2 "git push origin main"
t 2 "git push origin HEAD:main"
t 2 "git push origin feature:refs/heads/master"
t 2 "git push --force-with-lease origin x"
t 2 "git push -f origin x"
t 2 "git push origin +x"
t 2 "git push --delete origin x"
t 2 "git push --mirror origin"
t 0 "gh pr merge 12 --merge"
t 2 "gh pr merge 12 --merge --admin"
t 0 "gh pr create --base main --fill"
t 0 "gh run list"
t 2 "cd repo && git push --force"
t 0 "git -C /tmp/x push"
t 2 "git reset --hard HEAD~1"
t 2 "git reset -q --hard"
t 2 "git clean -fd"
t 2 "git branch -D foo"
t 2 "git checkout ."
t 2 "git checkout -- ."
t 2 "git restore ."
t 2 "git rebase main"
t 2 "git commit --amend -m x"
t 0 "ls; git push"
t 2 "true && git reset --hard"
# must allow
t 0 "git status"
t 0 "git checkout .github/workflows/ci.yml"
t 0 "git commit -m 'mention git push and reset --hard here'"
t 0 "git commit -m \"no git push, no reset --hard\""
t 0 $'python3 - <<\'EOF\'\nprint("git push --force")\nEOF\necho done'
t 0 $'cat > f.md <<EOF\ngit reset --hard\nEOF'
t 0 "echo 'git push' > notes.txt"
t 0 "git commit -F msg.txt"
t 0 "git diff main...HEAD"
t 0 "pytest tests/ -q"
t 0 "grep -n 'git push' AGENTS.md"
# main-branch rule
D=$(mktemp -d); git -C "$D" init -q -b main
t 2 "git commit -m x" "$D"
t 2 "git merge feature" "$D"
t 2 "git push" "$D"
t 2 "git push -u origin HEAD" "$D"
git -C "$D" checkout -q -b feature
t 0 "git commit -m x" "$D"
rm -rf "$D"
[ $fail = 0 ] && echo "ALL PASS" || { echo "SOME FAILED"; exit 1; }
