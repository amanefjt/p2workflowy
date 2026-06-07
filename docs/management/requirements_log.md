# Requirements Log: P2Workflowy V3 (Golden Rewrite)

## 会話履歴とユーザー要望の集約

### 2026-04-01 - 04-03: Golden Rewrite 初期フェーズ
- **JSONからの脱却**: Fragile な JSON 出力を廃止し、XMLタグベースのパースへ移行。
- **TierManagerの実装**: 有料/無料版の制限に合わせてバッチサイズを自動調整。
- **Book Modeの確立**: 大規模PDFを章ごとに分割し、並列翻訳した後に統合。

### 2026-04-04: 最終堅牢化と API仕様最適化（TTFTペーシング）
- **Phase 4 直列化の禁止**: Gemini 3 Flash Preview の長大コンテキスト（Thinking: High）発火時に発生する「約4分のサーバー側一斉塩漬け現象」を回避するため、直列化による順次待機（Context Chaining等）は絶対に採用しない（アーキテクチャ制約）。
- **並列相殺 (Global Optimum) の確立**: `max_concurrent_sections = 4` での一斉並列処理（Scatter-Gather）を標準とし、待機時間を並列で一括消化するアプローチを唯一の正解とする。

> **[2026-05-11 訂正]** 上記「約4分の塩漬け現象」は 2026-04-04 時点の一時的な外れ値（サーバー障害 × Phase 1 分割バグの複合）であり、定常現象ではなかった。実測（AL論文 17バッチ × 8回）での avg TTFT は 14〜31s。direct serialization（concurrent=1）が約50%遅いという事実は実証済みのため「直列化を避ける」という結論は変わらないが、理由は「240秒ストールの相殺」ではなく「並列化によるスループット向上」が正しい。詳細は `docs/model_optimization.md` Section 3 参照。
- **Book Mode パス解決の修正**: 統合時に出力ファイルを見失うバグ（`_export` サブディレクトリの問題）を解消。
- **ティア伝播の修正**: `--lite` フラグが Phase 0 (Global Scan) に適用されない問題を、モデル解決の遅延タイミング化によって解決。
- **コード監査**: `main.py`, `book_manager.py`, `state_integrator.py` の引数フローの整合性を確保。
- **スモークテスト**: `chap3relations.pdf` にて書籍モード完走を確認。

### 2026-05-15: ronbunnihongo モード確立・デプロイ構成修正

- **ronbunnihongo モードの仕様確定**: `export_mode="ronbunnihongo"` は、p2workflowy の通常出力から「日本語翻訳（レジュメ＋日本語本文）のみを Markdown で出力」するモード。英語ツリーおよび Workflowy テキスト（`.txt`）は生成されない。内部的には Phase 1〜4 は通常モードと同一処理を実行し、Phase 5 の出力分岐のみ異なる。出力ファイルは `_ronbun.md`。
- **URL ルーティング修正**: Cloudflare Pages で `/ronbunnihongo` にアクセスすると `index.html` が返されていた問題を修正。`web/_redirects` に `/ronbunnihongo /ronbun.html 200`（リライト）を追加。`server.py` にも `@app.get("/ronbunnihongo")` ルートを追加（HF Spaces 側）。
- **デプロイ構成**: GitHub (`origin`) と Hugging Face Spaces (`hf`) は**別リモートで独立管理**。GitHub マージ後に `git push hf main:main` を手動実行することで HF Spaces に反映。Cloudflare Pages は GitHub の `main` ブランチへのマージで自動デプロイ。
- **セキュリティ監査**: 全コードベースを対象に監査を実施。重大な脆弱性なし。`_safe_upload_path()`・`secrets.compare_digest()`・CORS 制限・UUID バリデーションの各実装は正常。

### 2026-06-07: coreprompts.json 要約・翻訳・抽出系プロンプトの構造改善

Anthropic 公式の long-context prompting tips・hallucination 低減ガイドに基づき、`core/coreprompts.json` の主要プロンプトを再構成した。

- **要約系（`GLOBAL_SUMMARY_PROMPT` / `SECTION_SUMMARY_PROMPT` / `SUMMARY_PROMPT` / `SUMMARY_PROMPT_ronbun`）**: 投入テキストは論文最大50万字・書籍最大150万字（Anthropic の言う「2万トークン超の長文書」に該当）であるため、`{text}` を `<source_document>` タグで囲んでプロンプト先頭近くへ移動し、詳細な構成・記述・フォーマットルールは本文ブロックの後（生成直前）にまとめる構成に変更（本文先頭化により応答品質が最大30%向上するという知見に基づく）。あわせて「`# [Original Heading]` / `## 英語節タイトル` を出力前に `<source_document>` の表記と一字一句照合する」という確認指示を追加（grounding 強化・幻覚抑制）。さらに「節と節の間の論理的接続（前節からの展開・次節への接続）を明示する」指示を追加した。
- **翻訳系（`TRANSLATION_PROMPT`）**: `{chunk_json}` の上限は `parallel_translator.py::DEFAULT_MAX_BATCH_CHARS = 11000`（約3,000〜7,000トークン）であり「2万トークン超」の閾値に届かないため、**本文先頭化は適用しなかった**（ルールを把握してから本文を変換するという翻訳タスクの性質上、効果も薄いと判断）。代わりに `<source_chunks>` `<resume_content>` `<glossary>` `<previous_translation>` 等の XML タグで「参考情報（背景文脈）」と「翻訳対象」を明確に分離し、境界の曖昧さを解消するに留めた。Core ルール・hedge/booster の few-shot 例・Strict Tag Protocol の内容は変更していない（過一般化リスクを避けるため）。
- **抽出系（`DNA_EXTRACTION_PROMPT` / `TEXT_STRUCTURE_EXTRACTION_PROMPT` / `TOC_EXTRACTION_PROMPT`）**: 入力規模が小さい（1ページ分のチャンク・最大120チャンク・冒頭15ページ）ため構造はほぼ維持しつつ、`<page1_chunks>` `<source_text>` `<toc_source_pages>` タグで入力データの境界を明示し、「id は `<page1_chunks>` 内に実在する値のみ使用する」「該当する情報が見つからない場合は無理に値を作らず `null` / 空配列を返す」というグラウンディング・ルールを追加した（Anthropic の hallucination 低減ガイドにある "Allow Claude to say I don't know" を JSON 抽出向けに翻案）。
- **判断基準（恒久指針）**: 長文を投入するプロンプトを改修する際は「本文を先頭へ」を機械的に適用せず、まず該当変数（`{text}` 等）の実際の投入上限文字数をコードで確認し、2万トークン規模に達するかどうかで適用可否を判断する。届かない場合は XML タグによる境界明示など、並び替えを伴わない改善に留める。
- **副産物として発見した実装バグ2件**: `meta_analyzer.py` の VLM ヒント連結順序の問題、および `TOC_EXTRACTION_PROMPT` の二重波括弧エスケープの問題。詳細は `troubleshooting_log.md` の I-6・I-7 を参照。
