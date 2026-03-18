# Walkthrough: Route C Heading Hierarchy Fix

Route C（Full VLM Mode）において、Valid な章タイトルが誤って節（h3）に降格されたり、逆に「柱（Running Header）」が章として誤認されたりする問題を、目次（TOC）ベースのバリデーションによって解決しました。

## 実施した変更のハイライト

### 1. TOCベースの見出しバリデーションの統合
`core/phase3_structure.py` の `structure_nodes_by_markdown` に、目次リストと照合して `# ` 見出し（h2候補）を自動降格するロジックを実装しました。

### 2. 見出し正規化ロジックの改善
プレフィックス（"Chapter 1" 等）を削除せず、記号除去と小文字化のみを行うように `normalize_heading` を簡素化しました。これにより、照合の堅牢性が向上しています。

## 検証結果

### Route C（Full VLM Mode）での検証
`psdpdf.pdf` の 175〜180ページ（Chapter 1 の終りと Chapter 8 の周辺）において、以下の改善を確認しました：
- **Before**: Chapter 1 終端の節が章として誤認されたり、Chapter 8 が正しく認識されなかった。
- **After**: Chapter 1 終端の節（"Chapter 1 Concluded"）は `h3` に降格され、Chapter 8 は正しく `h2` として認識された。

### レジュメモードでの検証
レジュメに基づいた構造化（`hybrid` モード）でも、改善された正規化ロジックにより、本文見出しが正しく紐付けられることを確認しました。

```json
{
  "id": 59,
  "text": "Chapter 1 Concluded",
  "level": 3,
  "matched_resume_id": 5
}
```

### 退行テストのパス
`tests/test_normalize_heading.py` を更新し、新しい正規化ロジック（プレフィックス保持型）の下で全てのテストがパスすることを確認しました。

## 成果物へのリンク
- [Task Log](task.md)
- [Design Support](design.md)
- [最終出力 (extracted_from_pdf_p2.md)](file:///Users/shufujita/Antigravity/p2workflowy/state/psdpdf/extracted_from_pdf_p2.md)
