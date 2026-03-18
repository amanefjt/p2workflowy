# [NEW] VLM キャッシュ（APIコスト節約・レジューム）機能

## 目的
プロセスが中断された際、すでに Gemini VLM で解析済みのページの抽出結果を再利用することで、API 料金の無駄を防ぎ、再実行時間を短縮する。

## Proposed Changes

### [core]

#### [MODIFY] [config.py](file:///Users/shufujita/Antigravity/p2workflowy/core/config.py)
- `SessionState` クラスに `vlm_cache` プロパティを追加。
  - パス: `state/{session_id}/vlm_cache.json`

#### [MODIFY] [pdf_ingester.py](file:///Users/shufujita/Antigravity/p2workflowy/core/pdf_ingester.py)
- `run_pdf_ingestion_async` 内で `vlm_cache.json` をロード。
- すでに結果が存在するページについては、VLM 呼び出しをスキップしてキャッシュから結果を取得。
- 新たに VLM で解析した結果は、逐次（ページ完了ごと）にキャッシュファイルに書き込む（atomic write）。

### [Component Name]# TOC目次の欠落修正計画

TOCページで章タイトルが「繰り返し要素（柱）」と誤認されて削除されるバグを修正します。原因は `pdf_ingester.py` (Pass 1) および `phase1_preprocess.py` (Phase 1) の両方にありました。

## Proposed Changes

### PDF Ingester

#### [MODIFY] [pdf_ingester.py](file:///Users/shufujita/Antigravity/p2workflowy/core/pdf_ingester.py)
- `extract_text_fast` の Step 4 に位置制約を追加。
- ページ上部 15% または下部 15% の範囲内にあるブロックのみを `ignored_patterns` でフィルタリングするように制限。

### Phase 1 Preprocessor

#### [MODIFY] [phase1_preprocess.py](file:///Users/shufujita/Antigravity/p2workflowy/core/phase1_preprocess.py)
- `_RUNNING_HEADER_RE` にネガティブ・ルックアヘッド `(?!...)` を追加。
- `Chapter`, `Part`, `Contents`, `Preface`, `Notes`, `Bibliography`, `Index` などの目次・重要セクションキーワードで始まる行が「タイトル 123」形式に合致しても削除されないように保護する。# Remove Inline Running Headers - Evaluation & Plan

**Goal:** 外部から提供された正規表現や構造のフィードバックを精査し、その中で真に有効な「空行起因のインデックスバグ修正」と「テストの強化」のみを適用しつつ、現在の堅牢な副題保護ロジック（小文字判定）を維持する。

## Proposed Changes

### Task 1: Fix Empty Line Preservation Bug in `pdf_ingester.py`
- Modify: [pdf_ingester.py](file:///Users/shufujita/Antigravity/p2workflowy/core/pdf_ingester.py)
- Logic: `header_removed` フラグを導入し、独立行ヘッダー検出時に `append` をスキップする。これにより、元からある空行を維持しつつ除去跡の重複空行を防ぐ。

### Task 2: Strengthen Assumptions & Verification
- Modify: [test_debug_header2.py](file:///Users/shufujita/Antigravity/p2workflowy/tests/test_debug_header2.py)
- Assertions: 章タイトルの保護（大文字開始）を厳密にテスト。

### Task 3: Investigate & Fix "Unlabeled Section" Issue
- Modify: [phase3_structure.py](file:///Users/shufujita/Antigravity/p2workflowy/core/phase3_structure.py)
- Enhancement: `normalize_heading` の正規表現に `Part`, `Section` 等のキーワードを追加。
- Enhancement: `match_heading` にデバッグログを追加し、マッチング失敗時に `norm_first` と `norm_head` を記録するようにする。
- ページ中腹にある柱キーワード行が削除されない（スルーされる）ことを確認するテストケースを追加します。

## Verification Plan

### Automated Tests
- `tests/test_vlm_cache.py` (新規作成)
  - 初回実行時にキャッシュが作成されること。
  - 2回目実行時に VLM (Gemini) が呼ばれず、キャッシュからデータが返ること。

### Manual Verification
1. 現在実行中のプロセスを停止。
2. キャッシュ機能付きのコードを適用。
3. 再実行し、ログで「Cache Hit」が発生して解析済みページがスキップされることを確認。
