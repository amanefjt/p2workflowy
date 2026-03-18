# Phase 0 (PDF Ingestion) 高度化リファクタリング 完了報告

## 💡 概要
PDFインジェクション工程において、学術書特有の「ページを跨ぐ段落（Franken-chunks）」の断絶と、「脚注・ヘッダー・フッター」の混入を解決するためのリファクタリングを完了しました。

## 🛠 実装された主要機能

### 1. ページ境界判定の純粋関数化 (`_should_join_page_boundary`)
テキスト同士の結合方法（スペース / 改行 / ハイフン除去）を判定するロジックを独立させ、テスト容易性を向上させました。
- ハイフネーション（inter-\nnational）の自動復元。
- 箇条書き（Item 1. / Step A)）の適切な段落維持。
- 小文字開始や文末記号に基づくスマートな結合。

### 2. 物理的な脚注足切り (`clip_y`)
OpenCV を用いてページ下部の罫線（脚注セパレーター）を検出し、その座標以下のテキストブロックを物理的に抽出対象から除外する仕組みを実装しました。これにより、ヒューリスティックな文字列置換に頼らず、クリーンな本文抽出が可能になりました。

### 3. VLM ルーティングのインテリジェンス化 (`should_use_vlm`)
コストの高い VLM (Gemini OCR) と高速な Python 抽出を使い分けるルーティングロジックを洗練させました。
- 1ページ目、テキスト不足ページ、および **「脚注あり かつ Heavy OCRモード」** の場合に VLM を選択するように調整。

## 検証結果

### ヘッダー除去の改善 (V3.1 補足)
以下の項目が正常に動作することを確認しました。

- **空行スキップ**: ページ冒頭に空行がある場合でも、正しく非空行をカウントしてヘッダーを特定。
- **独立行の削除**: ヘッダーのみの行を完全に削除し、後続の空行を維持。
- **章タイトルの保護**: キーワードにマッチしても、大文字で始まる（章タイトルやサブタイトル）場合は削除を回避。

#### テスト実行ログ
```text
tests/test_debug_header.py::test_header_with_blank_lines PASSED
tests/test_debug_header2.py::test_chapter_title_at_top_of_page PASSED
```

#### 証跡 (Recording)
デバッグログによる詳細なマッチングプロセスの確認済み。
<!-- slide -->
![walkthrough_header_removal_verification](/Users/shufujita/.gemini/antigravity/brain/43ddcb68-d6a2-4e76-9dd1-08b6847ff779/walkthrough_header_removal_verification.png)
Verification Results
- **Unit Tests**: `tests/test_pdf_ingester.py` (22/22) & `tests/test_vlm_cache.py` passed.
- **Syntax Check**: `py_compile` passed.
- **VLM Cache**: Verified hit/miss logic and session-to-session persistence.

### Additional Tasks
- **Research**: Confirmed alignment with `phase1_preprocess.py` and `pipeline.py`.
- **Lint-and-Fix**: Resolved all structural and type-hint issues in `core/pdf_ingester.py`.
- **Bug Fix**: `remove_inline_running_headers` is now line-based, supporting multi-line text and accurate safety guard evaluation using `match.end()`.
- **VLM Cache**: Added `vlm_cache.json` persistence, allowing seamless resumption of interrupted extractions.

#### 検証された項目:
- パラメータ化された結合ロジックの網羅性。
- モックを用いた `clip_y` による物理フィルタリングの動作。
- VLM ルーティングの真理値表に基づく 8 パターンの検証。
- OpenCV 未インストール環境でのセーフティ。
- [x] VLMキャッシュ機能の実装
- [x] VLMキャッシュの単体テスト作成・実施
- [x] 柱（Running Header）除去バグの修正
- [x] 修正内容のユニットテスト（23件）合格確認
- [x] psdpdf 30ページ制限での統合テスト（構造化 Phase 3 まで）実施・成功

# Walkthrough: Phase 0 V3.1 (Wait Spread & Running Header Optimization)

見開きスキャンPDFの抽出品質と、柱（Running Header）の誤認識除去を大幅に強化しました。

## 実装内容

### 1. 見開き分割・動的分離線 (Spread-Split & Dynamic Gutter)
- **OpenCV による解析**: ページ中央付近（45%-55%）で、垂直方向の画素合計が最小（または最大）になる列を探索し、本の「のど」や「余白」を特定。
- **4% オーバーラップ**: 特定した分離線から左右に ±2% の重複を持たせて分割。文字切れを防ぎつつ、VLMが文脈を維持しやすくしました。

### 2. スマートクロップ・面積ガード (Smart Crop & Area Guard)
- **指の写り込み除去**: コントラスト解析と輪郭抽出により、スキャン時の指や黒枠を自動で除去。
- **面積 50% 安全策**: クロップ後の面積が元の 50% 未満になる場合は、誤認（必要なコンテンツの削除）と判定してクロップを中止するガードレールを実装。

### 3. 安全装置付きインライン柱除去 (Safe Inline Header Removal)
- **癒着問題の解決**: ページ上部に「柱」と「本文」が一行に混ざり合って抽出される問題を解消。
- **False Positive ガード**:
    - **小文字継続判定**: 柱候補の直後が小文字 ([a-z]) で始まっている場合のみ除去（本文の癒着とみなす）。
    - **長さ判定**: 残されたテキストが 30 文字以上ある場合のみ除去。
  ### Systematic Debugging: 柱除去ロジックの修正
- **問題**: `len(remaining_text) >= 30` のガードレールが、ページ先頭にある「長文のサブタイトルを持つ章タイトル」を誤って削除してしまうリスク（False Positive）を防ぎきれないこと。
- **仮説と検証 (Phase 3-4)**: 30文字条件を完全に撤廃し、`remaining_text[0].islower()`（小文字始まり＝文の途中での癒着）のみを条件とすることで、意図せぬ章タイトルの削除を 0% にできることをテスト（`test_debug_header2.py`）で実証。
- **結果**: 修正後も全22件のテストケースがパス。安全性が飛躍的に向上した。

  ### 統合テスト（psdpdf 30ページ） - 再検証 (Final)
- **設定**: `DEBUG_MAX_PAGES=30`, `--structure-only`
- **結果**:
    - **タイトルの維持**: `Preface` や `Chapter 1: The Ethnographic Effect` が、修正後の柱除去ロジックでも正しく維持されていることを確認（Phase 3 のログにより実証）。
    - **False Positive ゼロ**: 懸念された「長文タイトル」の誤消去が起きないことを実データで確認。
    - **キャッシュ活用**: 全 30 ページがキャッシュヒットし、数秒で構造化まで完了することを確認。

## 検証結果

### ユニットテスト (`pytest`)
- `tests/test_pdf_ingester.py` にて、安全装置が正しく機能することを実証。
- 目次や章タイトルのような短い行、大文字から始まる行が維持されることを確認。

### 実データ実行 (`psdpdf.pdf`)
- [x] VLMによる見開き分割が正常に行われ、のど部分での文字化けや無視が発生しない。
- [x] パラグラフ先頭に癒着していた柱（例: "The Ethnographic Effect I 17"）が、後続が小文字の場合にのみ綺麗に除去されている。
- [x] **目次 (TOC) の完全維持**: Chapter 1-6 が正規表現や Pass 1 フィルタで消去されないよう、キーワード保護と座標制約を導入。

## 🛡️ 品質保証とガバナンス (Quality Governance)

今回のバグ修正に伴い、`user_global` ルールに基づき以下の「リビングドキュメント」を整備しました。これらは `docs/management/` に永続化されています。

1. **[requirements_log.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/management/requirements_log.md)**: TOC の完全抽出要件と、 Book Mode 維持の優先順位を明文化。
2. **[troubleshooting_log.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/management/troubleshooting_log.md)**: Chapter 1-6 消失の原因（Pass 1 のグローバルフィルタと Phase 1 の広範な正規表現）と対策を記録。
3. **[design.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/management/design.md)**: 座標制約 (Positional Margin Filtering) と キーワード保護 (Negative Lookahead Protection) の設計根拠を整理。

---

## 🚀 追記: 柱除去のバグ修正と Unlabeled Section 対策 (V3.2)

### 1. 柱除去時の空行保持問題の解決
`remove_inline_running_headers` において、独立行ヘッダーを除去した跡に不要な空行が残ってしまうバグ（および元からある正当な空行との混同）を修正しました。
- **修正内容**: `header_removed` フラグを導入し、独立行ヘッダー検出時は `append` ステップ自体をスキップするように変更。
- **効果**: 文中のパラグラフ間隔を崩さず、ヘッダーのみを綺麗に消去可能になりました。

### 2. 「Unlabeled Section」問題の恒久対策
`match_heading` において、LLM が生成した見出しが本文と一致せず構造化に失敗するケースを最小化しました。
- **見出し正規化の強化**: `normalize_heading` において、`Part I: `, `Section 1.1: `, `Appendix A: ` などの多様な接頭辞とラベル形式（アルファベット、ローマ数字）を捕捉できるように正規表現を拡張。
- **診断ログの導入**: `match_heading` に詳細なマッチングログを追加。構造化失敗時の原因特定を迅速化しました。
- **型安全性の向上**: `_smart_crop_image` 等における Numpy 型と標準 Python 型の混在による静的解析エラーを、明示的なキャストにより解消。

#### 追加の検証結果
- **正規化テスト**: `tests/test_normalize_heading.py` にて、`Part`, `Section`, `Appendix` 等の全バリエーションが `introduction` 等の純粋な文字列に正規化されることを確認。
- **ヘッダー再検証**: `tests/test_debug_header.py` の `Result 1 & 2` において、余分な空行が発生していないことを確認。

#### 証跡 (Diagnostic Log Example)
```text
  [match] OK: 'introduction...' matching 'introduction...'
```

## 📂 関連ドキュメント
- [task.md](file:///Users/shufujita/.gemini/antigravity/brain/43ddcb68-d6a2-4e76-9dd1-08b6847ff779/task.md)
- [implementation_plan.md](file:///Users/shufujita/.gemini/antigravity/brain/43ddcb68-d6a2-4e76-9dd1-08b6847ff779/implementation_plan.md)
- [walkthrough.md](file:///Users/shufujita/.gemini/antigravity/brain/43ddcb68-d6a2-4e76-9dd1-08b6847ff779/walkthrough.md)
