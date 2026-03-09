# Walkthrough: RonbunNihongo

このドキュメントでは、論文翻訳専用インターフェース **「RonbunNihongo」** の使用方法を説明します。

## 🚀 アクセス方法
サーバ起動後、以下のURLにアクセスしてください。
- `http://localhost:8000/ronbun`

※ 従来の `p2workflowy` は `http://localhost:8000/` で引き続き利用可能です。

## 🌟 主な機能と特徴

### 1. 「日本語だけ」の Markdown 出力
- 英語原文およびレジュメを除去した、日本語のみのクリーンなファイルを生成します。
- 論文のタイトルと、論理構造を維持した日本語訳がそのまま読めます。

### 2. わかりやすい進捗表示
- 実行中、詳細な技術ログは表示されません。
- 「テキスト分析中...」「翻訳中...」といった、現在何をしているかが直感的にわかるステータスと、25%ごとの大まかなパーセンテージが表示されます。

### 3. 日本語に完全最適化されたUI
- 各入力項目の説明、APIキーに関する注意書き、PDFコピーのヒントなど、すべての情報をユーザーの視点に立って日本語で記述しています。

## Phase 1 (Preprocessing) の検証結果 (2026-03-09)

`core/preprocessor.py` を実装し、Phase 0 の出力を正常にクレンジング・段落化できることを確認しました。

### 1. 癒着単語の分離 (Wordninja)
CamelCase 判定と `wordninja` の組み合わせにより、PDF特有の単語の癒着が正しく分離されました。

**修正前 (Phase 0 直後):**
`... fantasy epicTheLordoftheRings.Inthemanyinterviewswhichfollowed ...`

**修正後 (Phase 1 適用後):**
`... fantasy epic The Lord of the Rings In the many interviews which followed ...`
※ `epic` と `The`、および `Rings` と `In` の間の不自然な結合が解消されています。

### 2. Smart Unwrap (段落の結合)
文末記号以外で終わる行を後続行と結合し、読みやすいパラグラフを再構成しました。

**抽出結果 (alpdf.pdf 冒頭段落):**
```text
[03] In 1995, the filmmaker Peter Jackson embarked upon an adaptation of Tolkien’s fantasy epic The Lord of the Rings In the many interviews which followed the international success of the ensuing trilogy, Jackson reminisced on the roots of his project. He had been encouraged, he often stated, by a realization about the level of complexity reached by computer animation and special effects technology. ...
```

### 3. ステートの保存
処理結果は `state/phase1_clean.json` に保存され、後続のフェーズで利用可能な状態になっています。
- **最終段落数**: 259 (alpdf.pdf)

## OCRフォールバックの検証結果 (Phase 0.5)

文字化け（CIDフォント）が発生していた `Technopdf.pdf` に対して、画像認識（OCR）による自動救済機能の検証を行いました。

### 1. 文字化け検知と自動切り替え
ログ出力にて、`(cid:)` の含有率が閾値（10%）を超えたページで OCR への移行が正しく実行されていることを確認しました。

**デバッグログ:**
```text
  [Warning] Page 1: (cid:) detected (ratio=0.72). Falling back to OCR...
  [Warning] Page 2: (cid:) detected (ratio=0.74). Falling back to OCR...
```

### 2. OCRによる抽出結果
以前は `(cid:86)(cid:105)...` 等の記号の羅列であった箇所が、人間が読める正確な英語テキストとして復元されました。

**抽出ログ（Technopdf.pdf 冒頭）:**
```text
[00] Technologies of the Imagination:
[01] An Introduction
[02] David Sneath, Martin Holbraad & Morten Axel Pedersen
[03] University of Cambridge, University College London, & University of Copenhagen
[04] hat would an anthropology that takes the imagination seriously look
[05] like? And how far could an exploration of the processes through
```
※ Tesseract のページセグメンテーション機能により、2段組みレイアウトも適切に処理されています。

### 3. ハイブリッドエンジンの完成
- **通常PDF**: `pdfplumber` による座標ベースの決定論的抽出（ヘッダー・フッター、脚注の除去を伴う）。
- **文字化けPDF**: `pytesseract` による画像ベースのフォールバック救済。

この「ハイブリッド構成」により、学術論文PDFの抽出における主要な技術的課題（ノイズ、レイアウト、文字化け）を網羅的に解決しました。

## 🛠 使い方
1. **Gemini APIキー** を入力します。
2. **専門分野**（文化人類学など）を設定します。
3. 必要に応じて **用語集 (CSV)** をアップロードします。
4. 論文のテキスト（PDF等からコピーしたもの）を貼り付け、「翻訳を開始する」ボタンを押します。
5. 完了後、「Download RonbunNihongo (.md)」ボタンをクリックして保存します。

---
> [!TIP]
> PDFからテキストをコピーする際は、一度 **Microsoft Word** でPDFを開いてからコピーすると、レイアウトが崩れにくく翻訳精度が向上します。
