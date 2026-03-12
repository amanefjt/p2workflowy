# Walkthrough: p2workflowy V2 パイプライン安定化の実装と検証

## 実装の詳細

### 1. API ティアの動的管理 (TierManager)
`core/llm_client.py` にシングルトンの `TierManager` クラスを導入しました。
- 429 エラー (RESOURCE_EXHAUSTED) を検知すると、有料版 (PAID) から無料版 (FREE) 設定へ即座にダウンシフトします。
- 翻訳フェーズ (`phase4_translate.py`) ではこの設定を監視し、バッチサイズやセマフォの数を動的に調整します。

### 2. ID の型不一致問題の解消
Phase 3 (`build_tree`) と Phase 4 (`rebuild_translated_tree`) で、ノードの ID が文字列 (`str`) と整数 (`int`) で混在していた問題を修正しました。
- 辞書引きのキーには必ず `str(node_id)` を使用することで、JSONレスポンス由来の ID とモデル内部の ID を確実に突合できるようになりました。

### 3. プロンプト指示の強化
`core/coreprompts.json` に定義されている `{context_guide}` 変数が常に空文字になっていた問題を修正しました。
- Phase 2: 「論文全体の構造、各セクションの論理構成を抽出してください。」
- Phase 4: 「原文のニュアンスを維持しつつ、自然な日本語に翻訳してください。」
これらを明示的に注入するようにし、LLM の出力精度を向上させました。

### 4. 非対称的な階層構造の強制
`core/phase5_export.py` を修正し、`rules/export_structure.md` の仕様に従うようにしました。
- **英語**: 親項目の下にネスト。
- **日本語**: `日本語本文` というセパレーターの後に、H2 (フラット) な章題を羅列。
これにより、Workflowy に貼り付けた際に各章が独立した見出しとして機能します。

## 動作確認手順

### CLI 実行テスト
1. **テキストファイル**: `python main.py data/sample/old_tests/Arbitarylocations/Arbitrarysample.txt --free`
   - [x] 全 113 チャンクが日本語に翻訳されていることを確認。
   - [x] Markdown の見出しレベル (H2) が正しいことを確認。
   - [x] Workflowy 用テキスト of インデントがないことを確認。

2. **PDF ファイル**: `python main.py data/sample/PDF/ALpdf.pdf --free`
   - [ ] 18 ページの VLM 解析が正常に完了すること（現在実行中）。
   - [ ] 生成された `ALpdf_p2.md` の構造を確認。

### Render 本番環境デプロイ
1. **URL**: https://p2workflowy.onrender.com
2. **検証項目**:
   - [x] ページが正常に表示される（タイトル: `p2workflowy - Academic Text to Workflowy`）。
   - [x] JavaScript エラーがないことを確認。
   - [x] Gemini APIキー入力、専門設定、用語集、論文内容入力フォームが正しく表示されている。
3. **結果**: 正常に稼働中。

### エビデンス
- `deployment_success.png`: 本番環境のスクリーンショット。
- `state/ttft_metrics.csv`: 非同期呼び出しのパフォーマンスログ。
- `data/sample/old_tests/Arbitarylocations/Arbitrarysample_p2.md`: 最新の修正が反映された成果物。
