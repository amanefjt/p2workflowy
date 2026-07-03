#!/bin/bash
# git commit 前のリマインドフック(非ブロッキング)。
# core/ 配下の変更をコミットするのに requirements_log.md / troubleshooting_log.md の
# どちらも更新していない場合にだけ、参考程度の注意喚起を表示する。
# CLAUDE.md「変更管理」節 / .cursor/rules/90-verification-and-maintenance.mdc 参照。

input=$(cat)
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)

case "$command" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

staged=$(git diff --cached --name-only 2>/dev/null)
[ -z "$staged" ] && exit 0

core_changed=$(printf '%s\n' "$staged" | grep -c '^core/')
log_touched=$(printf '%s\n' "$staged" | grep -cE '^docs/management/(requirements_log|troubleshooting_log)\.md$')

if [ "$core_changed" -gt 0 ] && [ "$log_touched" -eq 0 ]; then
  cat <<'EOF'
{"systemMessage": "参考リマインド: core/ 配下の変更を含むコミットですが、docs/management/requirements_log.md / troubleshooting_log.md のどちらも更新されていません。仕様変更・判断根拠や不具合修正の記録が必要ならこのコミットの前に追記を検討してください(typo修正など記録不要な変更ならそのまま進めて問題ありません)。"}
EOF
fi

exit 0
