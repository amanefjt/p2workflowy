# Cursor Governance Mapping

本書は旧資産から Cursor 正本への対応表です。

## Mission

- `.agent/mission.md` -> `.cursor/rules/00-mission.mdc`
- `.github/agents/p2workflowy.agent.md` -> `.cursor/rules/00-mission.mdc`

## Rules

- `.agent/rules/01_pipeline_and_hierarchy.md` -> `.cursor/rules/10-pipeline-and-hierarchy.mdc`
- `.agent/rules/02_vlm_determinism.md` + `.agent/rules/02_vlm_geometric_v_s_logic.md` -> `.cursor/rules/20-vlm-determinism.mdc`
- `.agent/rules/05_export_standards.md` -> `.cursor/rules/30-export-standards.mdc`
- `.agent/rules/06_llm_model_standards.md` -> `.cursor/rules/40-model-tier-policy.mdc`
- `.agent/rules/04_verification_maintenance.md` -> `.cursor/rules/90-verification-and-maintenance.mdc`

## Skills

- `.agent/skills/p2workflowy-context/SKILL.md` + `.github/skills/p2workflowy-context/SKILL.md` -> `.cursor/skills/p2workflowy-context/SKILL.md`
- `.agent/skills/p2workflowy-debug/SKILL.md` -> `.cursor/skills/p2workflowy-debug/SKILL.md`
- `.agent/skills/golden_verification/SKILL.md` + `.github/skills/golden-verification/SKILL.md` -> `.cursor/skills/golden-verification/SKILL.md`
- `.agent/skills/systematic-debugging/SKILL.md` + `.github/skills/systematic-debugging/SKILL.md` -> `.cursor/skills/systematic-debugging/SKILL.md`

## Operational Notes

- 旧資産は削除せず凍結運用し、段階的に参照停止する。
- 新規運用は `.cursor/*` だけを更新先にする。
