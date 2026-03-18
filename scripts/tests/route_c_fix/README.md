# Route C Heading Fix Test Scripts

Route C（Full VLM Mode）の見出し階層適正化および正規化ロジックの検証に使用したスクリプト群です。

## スクリプト一覧

### 1. `show_toc_matches.py`
VLM チャンクから抽出された見出しが、目次（TOC）とどのようにマッチングされるかを可視化するスクリプトです。
- **用途**: `normalize_heading` の挙動確認と、特定の章タイトルがなぜ降格（または維持）されたかのデバッグ。
- **実行方法**:
  ```bash
  python3 scripts/tests/route_c_fix/show_toc_matches.py
  ```

### 2. `test_diagnostic.py`
PDF のテキスト抽出品質を診断し、Route C (VLM) か Route A/B (Python) かを判定するロジックのテスト用スクリプトです。

### 3. `subheading_list.txt`
検証中に抽出した見出し候補のリストです。照合精度の比較に使用しました。

## 注意事項
これらのスクリプトは開発・検証用であり、プロダクションのパイプラインには含まれません。修正内容の再検証や、新しい PDF での挙動確認が必要な際のリファレンスとして活用してください。
