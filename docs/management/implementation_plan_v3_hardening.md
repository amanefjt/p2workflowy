# Implementation Plan - P2Workflowy V3 Hardening & Deployment Prep

## 1. 現状の課題と修正ポイント
前回のセッションへの監査の結果、以下の修正が必要であることが判明しました。

### A. `StateIntegrator` のパス解決バグ
- `StateIntegrator.integrate_to_book` において、`sess["state_path"]` （セッションディレクトリ）の `.parent` を取得しているため、`state/` ディレクトリ自体を名前として取得してしまい、誤ったセッションディレクトリを参照する可能性がある。
- `Phase 5` が `is_book=True` の場合に作成する `[Title]_export/` サブディレクトリを `StateIntegrator` が考慮していないため、章ごとの出力ファイルを見つけられない。

### B. `BookManager` の整合性
- `ch_state.session_dir` を `state_path` として記録しているが、これを `StateIntegrator` で正しく解釈するように修正する。

## 2. 実施ステップ

### STEP 1: `StateIntegrator` の修正
- [ ] `core/engine/p3_structure/state_integrator.py` の `integrate_to_book` メソッドを修正。
  - `ch_path = Path(sess["state_path"])` ( `.parent` を除去)
  - `md_file` と `wf_file` のパス探索に `[safe_ch_title]_export/` を含める、または `glob` で柔軟に探す。

### STEP 2: `BookManager` の最終確認
- [ ] `core/book_manager.py` の引数伝播（`pop()` 部分）が `run_pipeline` への全引数を網羅しているか再確認。

### STEP 3: Git コミット
- [ ] これまでの修正内容（RTT v3.4, TierManager, BookManager/StateIntegrator fixes）を `main` ブランチにコミット。

### STEP 4: スモークテスト（Verification）
- [ ] 小規模な PDF (1-2ページ) を用い、`--book` モードで最初から最後まで完走することを確認。
- [ ] 最終成果物（`state/book_sessions/.../_p2.md`）が正しく全章を統合しているか目視確認。

## 3. 完了定義 (DoD)
- [ ] `StateIntegrator` がエラーなく全章を統合できる。
- [ ] 統合後の Markdown/Workflowy ファイルに全章の内容が含まれている。
- [ ] すべての修正が Git に反映されている。
