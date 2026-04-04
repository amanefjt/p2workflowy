# p2workflowy コアパイプライン 静的監査レポート

**監査日**: 2026-04-04  
**対象**: core/ 配下の全 Phase ファイル  
**手法**: systematic-debugging Phase 1 + Phase 2  
**修正済み除外**: DNA エラーハンドリング、translate_batch の6点修正は完了済み

---

## 重要度凡例
- 🔴 実害が出る・条件次第で確実にクラッシュ
- 🟠 特定条件で問題発生（潜在バグ・データ損失リスク）
- 🟡 非効率・保守性の問題・軽微な品質劣化

---

## config.py（全体共通）

### 🟠 C-1: print_log() が毎回ファイルを open/close する
- `config.py:L41-44`
- Phase 4 並列翻訳時（100〜200 回/セッション）に I/O 過多
- 修正: logging モジュールへ移行

### 🟡 C-2: MAX_STATE_SESSIONS = 300（事実上クリーンアップ無効）
- `config.py:L49` / スキルドキュメントは 10 と記述
- HF Spaces のディスク肥大化リスク

### 🟠 C-3: load_glossary_csv() がヘッダー行を用語集に取り込む
- `config.py:L128-133`
- csv.reader の全行を辞書化。"English,Japanese" ヘッダーが翻訳用語集に混入
- 修正: next(reader) でスキップ

---

## Phase 2: phase2_meta.py

### 🟠 P2-1: extract_keywords() のフォールバック regex に re.DOTALL 欠落
- `phase2_meta.py:L141`
- re.search(r'\[.*\]', response) → 複数行 JSON 配列に未対応
- 修正: re.DOTALL フラグ追加

### �� P2-2: generate_section_resume() のフォールバックプロンプトが旧仕様
- `llm_client.py:L509-522`
- SECTION_SUMMARY_PROMPT 欠落時に ## 形式で返し Unlabeled Section が発生

---

## Phase 3: toc_manager.py

### 🔴 P3-1: コードフェンス除去 regex に re.DOTALL 欠落
- `toc_manager.py:L97, L120`
- re.sub(r"```(?:json)?\s*|\s*```", "") が複数行フェンスに未対応
- json.loads() 失敗 → TOC が空になりフォールバック

### 🟠 P3-2: extract_headings_from_resume() のヒューリスティックが脆弱
- `toc_manager.py:L32`
- 「大文字始まり・80文字未満・./:; で終わらない」をすべて見出しとみなす
- レジュメ本文段落も同条件を満たし Unlabeled Section / 過剰分割の原因に

### 🟡 P3-3: state=None で get_toc() が AttributeError
- `toc_manager.py:L40`
- state.session_dir アクセス時にクラッシュ（テスト・単体実行時）

---

## Phase 4: parallel_translator.py

### 🔴 P4-1: セクション間翻訳コンテキストが失われる
- `phase4_translate.py:L120` asyncio.gather で全セクション並列実行
- previous_translation がセクション内バッチ間のみ機能。章をまたぐ文脈の一貫性低下

### 🟠 P4-2: was_downgraded フラグが毎バッチで apply_tier_settings() を再呼び出し
- `parallel_translator.py:L69-71`
- was_downgraded は set_tier() まで True のまま。不必要な再初期化ループ

### 🟡 P4-3: remaining_chunks.pop(0) が O(N)
- `parallel_translator.py:L85`
- 修正: collections.deque を使用して O(1) に

### 🟡 P4-4: rate_limiter + 無条件 sleep(0.5-1.5s) の二重スロットリング
- `parallel_translator.py:L93`
- FREE ティアでさらに遅くなる。rate_limiter に委ねて削除推奨

---

## Phase 5: phase5_export.py

### 🔴 P5-1: if True: ブロック — 条件が抜け落ちている
- `phase5_export.py:L87`
- Book/Paper 分岐の条件式が欠落したまま if True: が仮置き
- Book Mode で呼ばれても Paper Mode テンプレートで出力される

### 🟠 P5-2: _reposition_notes() の list.remove() が誤削除リスク
- `phase5_export.py:L57`
- text が同じ複数 note ノードがあると削除対象でないノードが消える
- 修正: nodes[:] = [n for n in nodes if n not in to_remove_set]

### 🟡 P5-3: export_mode == "p2workflowy" チェックが二重ネスト
- `phase5_export.py:L89, L126`
- 内側の if は常に True。ロジック錯乱

---

## models.py

### 🟠 M-1: TreeNode.from_dict() が元の辞書を破壊的変更
- `models.py:L78`
- data.pop("children", []) が元 dict から "children" を削除
- 同じ dict を再利用するコードがあると children が消える
- 修正: data.get() + {k:v ... if k != "children"} で回避

### 🟠 M-2: RawChunk.from_dict() が未知キーで TypeError
- `models.py:L33`
- フィールド追加後に古い phase1_clean.json を --resume で読んだ場合にクラッシュ

---

## 横断的な問題

### 🟠 X-1: load_coreprompts() にキャッシュなし（毎回 JSON 読み込み）
- `config.py:L27-33`（複数ファイルから直接呼び出し）
- 修正: @functools.lru_cache(maxsize=1) を追加

### �� X-2: エラー表記の不統一
- translate_batch: 「【翻訳失敗】」（全角）
- parallel_translator: 「[翻訳失敗]」（半角）

---

## 優先順位マトリクス

| ID | 説明 | 重要度 | コスト | 推奨順位 |
|---|---|---|---|---|
| P5-1 | if True: 条件欠落 | 🔴 | 小 | 1 |
| P3-1 | TOC パース re.DOTALL 欠落 | 🔴 | 小 | 2 |
| M-1 | from_dict 破壊的変更 | 🟠 | 小 | 3 |
| C-3 | Glossary ヘッダー混入 | 🟠 | 小 | 4 |
| P2-1 | Keywords regex re.DOTALL 欠落 | 🟠 | 小 | 5 |
| M-2 | RawChunk.from_dict TypeError | 🟠 | 小 | 6 |
| P3-2 | 見出し抽出ヒューリスティック脆弱 | 🟠 | 中 | 7 |
| P5-2 | list.remove() 誤削除リスク | 🟠 | 小 | 8 |
| X-1 | load_coreprompts() キャッシュなし | 🟠 | 小 | 9 |
| P4-3 | pop(0) O(N) | 🟡 | 小 | 10 |
| P4-4 | 二重スロットリング | 🟡 | 小 | 11 |
| X-2 | エラー表記の不統一 | 🟡 | 小 | 12 |
| C-1 | print_log I/O 効率 | 🟡 | 中 | 13 |
| C-2 | MAX_STATE_SESSIONS=300 | 🟡 | 小 | 14 |

---
*静的解析による監査。各 🔴/🟠 は修正前に最小再現スクリプトでの確認を推奨。*
