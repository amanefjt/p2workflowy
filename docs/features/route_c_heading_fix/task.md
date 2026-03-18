# Task: psdpdf.pdf Translation and Verification (Route C Heading Fix)

## 完了定義 (Definition of Done)
- [x] Route C（Full VLM Mode）において、Chapter 1 等の主要な章が `h3` に誤降格されない。
- [x] VLM が `# ` とした見出しが、目次（TOC）に存在しない場合のみ `h3` に降格される。
- [x] 手動のプレフィックス判定（"Chapter" 等）に依存せず、正確な目次照合が行われる。
- [x] レジュメモード（Paper Mode/Hybrid）の正規化ロジック改善により、見出しの紐付け制度が向上する。
- [x] `psdpdf.pdf` の全域（Chapter 1〜8）で構造が正しいことを確認。

## 完了済みタスク
- [x] Route C（Full VLM Mode）の目次バリデーション実装
- [x] VLMチャンクからの目次抽出機能の追加 (`extract_toc_from_chunks`)
- [x] Route Cでの目次バリデーションロジックの統合 (`structure_nodes_by_markdown`)
- [x] 見出し判定の正規化ロジックの改善 (TOCマッチング精度向上)
- [x] `psdpdf.pdf` での動作確認（Phase 3 および Phase 4/5 完遂）
- [x] レジュメモードでの整合性確認
- [x] 退行テスト（`tests/test_normalize_heading.py`）の更新とパス確認
