# Archived Debug and Test Scripts

このディレクトリには、ルートディレクトリに散在していた過去の開発・デバッグ用のスクリプトを移動・整理して格納しています。

## スクリプト一覧

- `test_p6_margin.py`: ページ余白（マージン）と柱（Running Header）の判定ロジックを検証するためのスクリプト。
- `debug_p6.py`, `debug_repeating.py`: 繰り返し要素（ヘッダー・フッター）の検出デバッグ用。
- `test_phase1_regex.py`, `test_regex.py`: Phase 1 の正規表現クリーニング処理の検証用。
- `test_blocks.py`, `test_extract.py`: `fitz` (PyMuPDF) によるテキストブロック抽出の挙動確認用。
- `check_vlm_content.py`: VLM の出力内容を確認するための補助スクリプト。
- `split_pdf.py`: 検証用に PDF を分割するためのユーティリティ。

## 利用上の注意
これらのスクリプトは特定の課題解決やバグ修正の過程で作成された使い捨て（ad-hoc）のものです。現在のプロジェクト構造や最新の API 仕様と完全には一致しない可能性がありますが、過去の設計判断の参照用として保存しています。
