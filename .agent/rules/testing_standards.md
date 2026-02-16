# 検証標準ルール (Testing Standards)

プロジェクトの品質を維持するため、以下の検証用標準データと手順を定めます。

## 1. 論文モード (Paper Mode) の標準検証データ

論文モードの機能変更、プロンプト調整、バグ修正を行った際は、必ず以下のファイルを使用して動作確認を行ってください。

- **ファイルパス**: `tests/sample_data/Arbitrary locations- in defenc.txt`
- **選定理由**: 
    - 適度な長さ（約60KB）があり、チャンク分割が発生する。
    - OCR由来のノイズや不自然な改行が含まれており、構造化ロジックの検証に適している。
    - 既に実績のあるデータであり、過去の出力結果との比較が容易である。

## 2. 検証の手順

1. **一括処理テスト**:
   ```bash
   PYTHONPATH=. python3 -m src.main "tests/sample_data/Arbitrary locations- in defenc.txt"
   ```
2. **出力の確認**:
   - `tests/sample_data/Arbitrary locations- in defenc_output.txt` (Workflowy形式)
   - `tests/sample_data/Arbitrary locations- in defenc_structured_eng.md` (構造化英語)
   - `tests/sample_data/Arbitrary locations- in defenc_resume.txt` (レジュメ)
3. **品質チェック**:
   - `_output.txt` の階層構造が正しいか。
   - `_structured_eng.md` に日本語が混入していないか。
   - 各フェーズでエラーが発生していないか。

## 3. データの更新

検証用データを更新、または追加する場合は、必ず `requirements_log.md` にその理由を記録してください。
