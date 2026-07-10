# Spec B: Phase 1 入力ルーティングの修理と公式化（VLM 修理・Docling 正式化・書籍単位判定）

**ステータス**: 設計確定・実装未着手（実装は翻訳コンテキスト Stage 1 の後）
**起案日**: 2026-07-10
**起案者**: shufujita（brainstorming via Claude）
**位置づけ**: 上流（Phase 1 ルーティング）のスペック。`2026-07-10-translation-context-architecture-design.md`（下流・翻訳コンテキスト）とは疎結合（検証済み）。
**関連**: `troubleshooting_log.md` I-15〜I-16 / `requirements_log.md` 2026-07-07（候補改善登録）・2026-07-10（起案）

---

## Context

当初の問い（requirements_log 2026-07-07）は「書籍章処理の `pdf_mode="full_vlm"` 固定を適応ルーティング化すべきか」だった。しかし起案前調査（2026-07-10、静的解析＋実行時検証）で、**前提そのものが崩れている**ことが判明した：

1. **VLM スライディング OCR は機能停止している（I-15）**: `OCRManager.process_page_vlm` の同一クラス内二重定義（`ocr_manager.py:157` 画像版 / `:214` pdf_path 版）により後者が生存し、唯一の呼び出し元（`pdf_ingester.py:67`）は前者のシグネチャで呼ぶため**毎ページ必ず TypeError → ネイティブテキストへ静かにフォールバック**。ファイル初出コミット a4c7fa4（2026-04-03）から存在。
2. **full_vlm 指定でも Docling が優先される（I-16）**: `phase1_preprocessor.py:141` の Docling 分岐は `pdf_mode` を見ない。Booksample 3 冊はすべて `is_docling_viable=True`（実測）であり、書籍モードの実働経路は **Docling＋Phase 3 TOC/ChapterParser フォールバック**。
3. **Docling は `#` Markdown を出力しない**: `docling_ingester.py:70-81` は `role`（h1/h2/p）属性のみ付与。Phase 3 Route C（`structure_nodes_by_markdown` の Markdown 正規表現）は Docling チャンクに対して常に空振りする。

つまり「書籍モードの構造化がうまくいっている」のは偶然のフォールバック経路の産物であり、「デジタル書籍のコスト削減余地（10〜50 倍）」は VLM が動いていない以上すでに事実上享受している。よって本スペックの主題は「コスト削減」から「**壊れた経路の修理と、偶然動いている経路の設計への昇格**」に再フレームする。

### ユーザー決定（2026-07-10）

- スキャン書籍（見開き含む）は今後も処理する → **VLM 修理は必須**。
- ルーティング判定は**書籍単位**（章単位は過剰）。
- 実働経路の公式化・VLM 修理・ルーティング明示化のすべてを実施。
- CLAUDE.md の設計原則が実態と乖離しているため書き換える（暫定注記は 2026-07-10 に適用済み。本スペック実装後に正式記述へ差し替え）。

---

## ゴール / 非ゴール

**ゴール**
1. VLM スライディング OCR の修理（I-15）。
2. デジタル書籍の Docling ルート正式化：Docling の role 見出しを書籍 Phase 3 に配線し、「TOC フォールバック頼み」を設計された経路にする。
3. ルーティングの明示化：書籍単位の自動判定、ユーザー指定 `pdf_mode` の尊重（現状の pop・破棄をやめる）、実際に使ったルートの記録とログ。
4. Phase 1 / Phase 3 の前提一致：Phase 3 の分岐を「指定された pdf_mode」ではなく「Phase 1 が実際に使ったルート」参照に改める。
5. ドキュメント正常化（CLAUDE.md 設計原則・ARCHITECTURE.md §3）。

**非ゴール**
- 翻訳コンテキスト供給（別スペック `2026-07-10-translation-context-architecture-design.md`）。
- 章単位の適応ルーティング（書籍単位で不足が観測されたら再検討）。
- 見開きスキャン PDF を Docling に載せる最適化（A/B 課題として登録のみ、下記）。

---

## 採用する設計

### 1. VLM 経路の修理

- `ocr_manager.py:214` の旧シグネチャ版 `process_page_vlm(pdf_path, page_num)` を削除し、`:157` の画像版（呼び出し元 `pdf_ingester.py:67` と整合する方）を正とする。削除前に旧版のみが参照する内部依存の有無を確認する。
- 修理後、スキャン PDF サンプルで **VLM が実際に呼ばれ Markdown 見出し付きテキストが返ること**を実行確認する（これまで一度も動いていなかった経路のため、修理は「復旧」ではなく実質的な初稼働と見なして検証する）。

### 2. 書籍単位のルーティング規則

`BookManager` が処理開始時に**書籍のオリジナル PDF に対して 1 回**判定し、全章に適用する：

```
① ユーザーが pdf_mode を明示指定 → それを尊重（現状の pop・破棄を廃止。CLI 既定値のままか明示指定かを区別できる形にし、既定のままなら②以降の自動判定へ）
② is_spread_pdf() == True（見開き）→ VLM ルート（SpreadSplitter 分割後）
③ is_docling_viable() == True → Docling ルート
④ それ以外（スキャン等）→ VLM ルート
```

- ②を③より優先するのは保守的判断：見開き 2-up の PDF はテキスト層がクリーンでも（corfra/pse が該当）Docling の読み順が未検証のため。「見開き×viable を Docling に載せられるか」は A/B 課題として残す。
- 判定結果（採用ルートと判定根拠）は book session の state に記録し、ログにも出す。

### 3. 実ルートの記録と Phase 3 の分岐修正

- Phase 1 が実際に使ったルート（`docling` / `vlm` / `native_fallback`）を `phase1_preprocessor.json` に記録する。
- Phase 3 の Route C 発火条件を `pdf_mode == "full_vlm"`（指定値）から**実ルート参照**に変更：
  - 実ルート = `vlm` → Route C（Markdown 構造化）。
  - 実ルート = `docling` → **role ベース構造化**（下記 4）。
  - いずれも失敗時は既存の TOC/ChapterParser フォールバックを維持。

### 4. Docling role 見出しの書籍 Phase 3 配線

- Docling チャンクの `role="h1"/"h2"` を書籍モードの章・節構造構築に直接使う。実装の第一候補は、論文モードで実績のある `merge_role_headings` / `heading_matcher` 経路（requirements_log 2026-07-04 の I-8 対応で導入済み）の書籍への流用。
- ChapterParser / TOC 抽出は「role 見出しが乏しい場合」の従来フォールバックとして残す。

### 5. ドキュメント更新

- CLAUDE.md 設計原則の暫定注記（2026-07-10 適用）を、実装完了後に正式記述（Docling 正式経路・VLM はスキャン用・実ルート記録）へ差し替え。
- ARCHITECTURE.md §3「入力ルーティングの自動判定」「物理データ主権」を実態に合わせ更新。

---

## テスト / 検証

- **単体**: ルーティング規則（①〜④の優先順位）、実ルート記録、Phase 3 の実ルート分岐、role ベース構造化の各単体テスト。`python3 -m pytest tests/unit/ -q` 全合格維持。
- **VLM 修理の実動作確認**: スキャン PDF（見開きサンプル含む）で VLM 呼び出しが発生し「[VLM抽出失敗]」が出ないこと、Markdown 見出しが生成され Route C が機能することを実行トレースで確認。
- **ゴールデン検証**: `golden-verification` skill に従い、論文（AL/NST、ルーティング回帰なし）と Booksample 3 冊（判定ルートのログ確認＋完走＋構造品質）を確認。**relations（282p・単ページ・純デジタル）を Docling 正式化の重点検証対象**とする（見開き要因を排除できるため）。
- **コスト実測**: VLM 1 呼び出しあたりのトークン実測が未整備（`model_optimization.md` は Phase 4 中心）。修理後のスキャン書籍処理で実測し、`model_optimization.md` に追記する。

## リスク / 留意

- **VLM 修理はスキャン PDF の挙動を実質初稼働させる**: これまでネイティブテキスト（またはテキスト層なしなら「[VLM抽出失敗]」）で処理されていたスキャン文書の出力が変わり、コストも新たに発生する（Lite・thinking LOW なので単価は低い）。品質は上がる想定だが、比較読みではなく構造検証で確認する。
- **翻訳品質ベースラインの保護**: 実装は翻訳コンテキスト Stage 1 の**後**（Stage 1 の比較読み・モデル A/B が終わってから）。上流の入力が変わると比較読みの前提が動くため。
- **削除の安全性**: 二重定義の削除は「削除直前の再 grep」「削除コミット分離」の原則（Spec A 由来）に従う。
- **管理ログ**: `core/` 変更を伴うため、実装コミットで `troubleshooting_log.md`（I-15/I-16 の対応済み化）・`requirements_log.md` への追記を行う。

## スコープ外（A/B 課題として登録）

- **見開き×viable を Docling に載せる**（corfra/pse タイプ）: 読み順品質の検証が必要。書籍単位ルーティングの規則②を緩和できるかの実験。
- **章単位ルーティング**: 書籍単位で不足（例: 付録だけスキャンの合本）が観測された場合に再検討。
