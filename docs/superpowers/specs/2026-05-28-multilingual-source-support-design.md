# p2workflowy 多言語ソース対応：将来構想設計メモ

**ステータス**: 構想（実施延期 / Deferred）
**起案日**: 2026-05-28
**起案者**: shufujita（brainstorming via Claude）
**位置づけ**: 即実施はしない。将来着手時の出発点として保存する設計メモ。

---

## Context

p2workflowy は現状、英語学術論文・専門書籍を入力として日本語訳の Workflowy 階層 Markdown を生成するツール。コードベース全体に **英語→日本語が前提として深く埋め込まれている**（プロンプト本文・glossary スキーマ `{en, ja}`・`english_tree`/`japanese_tree` 命名・Workflowy 非対称階層の「英語ラッパー」など）。

スペイン語・中国語・韓国語の論文も同じ品質で日本語化したいという要望があるが、現状のコード前提を解きほぐす作業は重く、**今すぐ着手するには工数が大きすぎる**と判断したため、将来の参考用に方針を確定して保留する。

基本的な使用シーンは引き続き **英語論文** であり、他言語対応は「あればうれしい拡張」という位置づけ。

---

## スコープ決定

ブレインストーミングで以下を確定：

| 軸 | 決定 |
|---|---|
| **対応の方向** | ソース言語のみ拡張。訳出先＝日本語は固定 |
| **入力種別** | デジタル PDF / プレーンテキスト**のみ**。非英語のスキャン PDF / VLM OCR 対応は対象外（OCR プロンプト・スプレッド分割・物理証拠ロジックの多言語化を回避） |
| **言語指定方法** | 明示フラグ `--lang {en,es,zh,ko}`（既定 `en`）。自動検出はやらない。Web 版はセレクタで同等 |
| **対象言語** | Spanish / Chinese (Simplified) / Korean。英語は既存挙動を維持 |
| **採用アプローチ** | **C案：テンプレ化プロンプト + 言語パック** |

---

## 不採用案の記録

検討した 3 案：

### A. プロンプト言語別フルセット
`TRANSLATION_PROMPT_EN/ES/ZH/KO` を全部書く。
- ○ 各言語へ素直に最適化可能
- ✕ プロンプト 4 倍、整合維持が破綻する

### B. テンプレ化（言語パックなし）
単一プロンプトに `{source_language}` を差すだけで言語別チューニング無し。
- ○ コード変更最小
- ✕ CJK の見出し慣例（第N章 / 제N장）や Spanish の長文展開などの言語固有チューニング余地ゼロ

### C. テンプレ + 言語パック（採用）
プロンプト本体は単一テンプレ。言語ごとに小さな pack（`name_ja`, `heading_patterns`, `translation_examples`, `glossary_label`）を持たせる。
- ○ 拡張性と品質のバランス。新言語は pack 1 ファイルで追加
- ○ EN は既存プロンプトの文言を `en.py` pack に移すだけで後方互換可能
- △ 初回リファクタが少し重い（既存プロンプト分解）

---

## C案 設計骨子

### 1. 言語抽象

```
core/languages/
  __init__.py          # get_language_profile(code) ファクトリ
  base.py              # LanguageProfile dataclass
  en.py                # 英語 pack（既存プロンプト文言の移植先）
  es.py                # スペイン語 pack
  zh.py                # 中国語簡体 pack
  ko.py                # 韓国語 pack
```

`LanguageProfile` の最小フィールド：

| フィールド | 用途 |
|---|---|
| `code: str` | `"en"` / `"es"` / `"zh"` / `"ko"` |
| `name_ja: str` | プロンプト内表記用（「英語」「スペイン語」「中国語」「韓国語」） |
| `name_en: str` | コメント・ログ用 |
| `heading_patterns: list[str]` | Phase 3 chapter_parser 用の正規表現（例: `r"^第[一二三四五六七八九十百\d]+[章節]"`） |
| `heading_keywords: list[str]` | Phase 1 LLM 抽出時のヒント語彙（例: `Abstract / Resumen / 摘要 / 초록`） |
| `translation_examples: list[dict]` | hedge/booster 等の言語ペア例（Phase 4 プロンプト注入） |
| `glossary_term_label: str` | 用語集での原語ラベル（例 `"原語"`） |

### 2. パイプライン引数化

- `main.py`: `--lang {en,es,zh,ko}` 追加（既定 `en`）
- `server.py`: API リクエスト / フロントエンドにセレクタ追加
- `core/pipeline.py::run_pipeline(..., source_lang: str = "en")` を末端まで貫通
- `state/<session_id>/` の resume 用 JSON に `language` キーを保存・読み戻し

### 3. フェーズ別変更点

| フェーズ | 変更 |
|---|---|
| Phase 1 (text) | `TEXT_STRUCTURE_EXTRACTION_PROMPT` をテンプレ化、`{heading_keywords}` 注入 |
| Phase 1 (PDF) | Docling パスのみ非英語対応。VLM/物理抽出パスは英語専用のまま据え置き |
| Phase 2 | `SUMMARY_PROMPT*` / `SECTION_SUMMARY_PROMPT` / `KEYWORD_EXTRACTION_PROMPT` の「英語」表記をテンプレ化。DNA 抽出（`intro_pre_heading`）は言語非依存 |
| Phase 3 | `core/engine/p3_structure/chapter_parser.py` の見出し検出を `heading_patterns` 経由に。`[Original English Heading]` 文言を `[Original {source_language_name} Heading]` 化 |
| Phase 4 | `TRANSLATION_PROMPT` テンプレ化、`prompt_builder.py` で `translation_examples` 注入。`english_tree` → `source_tree` リネーム（`tree_reconstructor.py`） |
| Phase 5 | Workflowy 非対称階層の「英語ラッパー親 / 日本語並列」を「source ラッパー / 日本語並列」と一般化 |

### 4. 用語集スキーマ移行

```
# 旧
{"en": "...", "ja": "...", "definition": "..."}

# 新
{"src": "...", "ja": "...", "lang": "es", "definition": "..."}
```

**互換性**: reader 側で `en` キーを `src`（`lang="en"`）にマッピングする後方互換レイヤーを `core/phase2_meta.py` に置く。既存 EN セッションの resume は壊さない。

### 5. テスト資産

- `data/input/paperplain/{ES,ZH,KO}/` に各 1 サンプル（オープンアクセス論文の抜粋で十分）
- 既存 EN 回帰テスト（165 件）は全数維持
- `tests/unit/` に `test_language_profile.py` 追加：pack ロード・heading_patterns マッチ・glossary 互換 reader

### 6. Web UI

`server.py` のフロントエンドに `<select name="lang">` を追加。デフォルト `en`。料金表示・モデル選択 UI 隣に配置。

---

## 工数見積り

| ブロック | 工数目安 | 内容 |
|---|---|---|
| 抽象＋ EN リファクタ | **2〜3日** | `LanguageProfile` 導入・EN pack 化・既存テスト全通過維持 |
| Spanish 追加 | **+1〜2日** | 既存英語パターンの近接言語。pack 1 つ書く |
| Chinese + Korean 追加 | **+3〜4日** | CJK 見出し慣例・glossary・テスト fixture 整備 |
| 翻訳品質チューニング | **+2〜3日** | hedge/booster や論理展開例文の言語別調整（人文系教訓の踏襲） |
| Web UI 言語セレクタ | **+0.5日** | |
| **合計（3 言語まとめて）** | **~9〜12日** | |
| **合計（Spanish 単独パイロット）** | **~4〜5日** | |

**最大の不確定要素**は翻訳品質チューニング。`project_humanities_translation_enhancement.md` の教訓どおり、サンプル文献を集めて例文ベースで調整する反復が工数の主因になる。

---

## 将来着手時のチェックリスト

着手前に下記を確認する：

1. `coreprompts.json` のプロンプト構造が当時から大きく変わっていないか（人文系強化の続編などで構造が変わっている可能性）
2. `core/engine/p4_translate/prompt_builder.py` のシグネチャ確認（pack 注入ポイント）
3. Docling の非英語デジタル PDF 抽出品質を当時の最新版で再確認
4. Gemini モデルの非英語性能（特に韓国語）の最新状況を `docs/gemini_models.md` で確認
5. Spanish パイロットを先に走らせて学習を取ってから CJK に進む（推奨）
6. 用語集スキーマ移行は破壊的変更なので、既存セッション resume 互換テストを最初に書く

---

## 関連メモ

- 翻訳品質の知見：`memory/project_humanities_translation_enhancement.md`
- Phase 4 並列数の決定：`docs/model_optimization.md` Section 3
- アーキテクチャ判断：`memory/project_architecture_decisions.md`
