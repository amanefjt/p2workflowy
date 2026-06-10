# Cursor Governance Source Of Truth

このリポジトリのAI運用ルールは、以下を正本とします。

- Rules: `.cursor/rules/*.mdc`
- Skills: `.cursor/skills/*/SKILL.md`

## Migration Policy

- `.agent/*` と `.github/*` の既存定義は当面「凍結された互換レイヤー」として残します。
- 新規追加や更新は `.cursor/*` にのみ実施します。
- 凍結レイヤーを更新する場合は、同時に `.cursor/*` を先に更新して整合させます。
