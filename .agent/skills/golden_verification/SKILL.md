# Golden Rewrite 抽出構造検証 (SKILL.md)

P2Workflowy 抽出パイプラインの実行後、その論理的な階層構造が「黄金の非対称ルール」および「幾何学的ルール」に適合しているかを自律的に検証するためのスキル。

## 1. 目的
抽出された PDF データの「構造的欠損」および「階層の誤り」を、VLM 出力と構造化 JSON を照合することで発見・修正する。

## 2. 検証プロトコル (Verification Protocol)

### ステップ 1: 並列スライディング OCR のスループット確認
- `state/[timestamp]/` ディレクトリ内のログを確認し、全ページの VLM リクエストが 40 秒程度（19ページ基準）で完了しているかを確認する。

### ステップ 2: 幾何学的見出し (Vertical Moat) の抽出確認
- `phase3_structure.json` を開き、以下の項目が `role="h1"` (または `h2` rank) として抽出されているかを GFM 検索などで確認する。
    - ローマ数字や番号のない見出し（例: `Other Assemblies`, `Identity`）
    - イタリック体で記述された独立行
- 見落としがある場合は、`ocr_manager.py` の `VLM_BASE_RULES` と照らし合わせ、不適切な「堀（空白）」の過小評価がないかを確認する。

### ステップ 3: 無題セクション (Unlabeled Section) のマージ確認
- `TreeConstructor.py` の Greedy Merge ロジックが正常に作動し、`[Unlabeled Section]` が既存の章の続きとして正しく結合されているか（あるいは適切な独立章になっているか）をツリー構造で確認する。

### ステップ 4: 階層の非対称性の視視
- ブラウザ拡張機能（Antigravity Browser Extension）を用いて、最終出力の Markdown/txt をプレビューする。
    - **日本語**: 前の見出しに対して並列（同じインデントレベル）
    - **英語**: 前の見出しに対してネスト（1つ深いインデントレベル）
- この構造が崩れている場合は、Phase 5 の Export エンジンを確認する。

## 3. 推奨ツール
- `view_file` (JSON の階層確認)
- `list_dir` (ステートディレクトリのタイムスタンプ確認)
- `Antigravity Browser Extension` (出力の視覚的確認)
