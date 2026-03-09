# p2workflowy Phase 5 Refinement & Cloudflare Sync

## 1. 目的 (Objective)
- インポート後の階層構造の適正化（英語 H3 / 日本語 H2 / セパレーター追加）。
- Workflowy へのインポート時に日本語セクションが英語ノード下にネストされる問題の解消。
- Cloudflare Pages の配信設定（`web` をルートとして配信）との環境同期。

## 2. 完了定義 (Definition of Done - DoD)
- [x] `p2workflowy` と `ronbunnihongo` のエクスポートロジックが完全に分離されている。
- [x] Markdown 出力において、英語セクションは H3、日本語セクションは H2 レベルとなっている。
- [x] Markdown と Workflowy 双方に「日本語本文」セパレーターが存在する。
- [x] Workflowy 出力において、日本語各セクション（Abstract等）が Level 1 ノード（インデントなし）になっている。
- [x] ローカルおよび本番 (Cloudflare) で CSS/JS が正しく読み込まれる。
- [x] 全ての成果物が GitHub にプッシュされ、本番環境で動作確認済みである。

## 3. 作業項目 (Tasks)
### Phase 1: Logic & Level Refinement
- [x] `phase5_export.py` の条件分岐 (`export_mode`) 実装
- [x] 見出しレベル調整 (English: H3, Japanese: H2)
- [x] 理想的な構造リフレッシュ

### Phase 2: Structural Adjustments
- [x] 「日本語本文」セパレーターの導入
- [x] Workflowy 出力のインデント調整 (`base_depth=0` for Japanese tree)
- [x] `ideal_mdstructure.md` / `ideal_wfstructure.txt` の更新

### Phase 3: Cloudflare Sync
- [x] HTML 内の資産パス (`/web/style.css` -> `/style.css`) 修正
- [x] `server.py` の静的ファイルマウントをルート (`/`) に変更
- [x] FastAPI のルート定義順序（マウントを最後に）を修正
- [x] 本番環境（https://p2workflowy.pages.dev/）での動作確認
