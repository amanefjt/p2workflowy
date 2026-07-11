# 翻訳コンテキスト Stage 2：統合用語レイヤー Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽出済みの術語「定義」を翻訳プロンプトまで配線し、glossary と local_definitions を単一の構造化用語レイヤーに統合して語彙平準化を抑える。

**Architecture:** glossary パイプラインの `dict[str,str]`（en→ja）固定を解き、`TermEntry`（en/ja/definition/source）を運ぶ新モジュール `core/engine/p4_translate/term_layer.py` に用語集組み立てと整形を隔離する。定義は本文抽出（`keywords_data`）と glossary CSV（書籍モードでは定義列を持つ `global_glossary.csv`）から供給し、「訳語は CSV 優先・定義は本文優先」のフィールド別マージで統合する。翻訳プロンプトの `<glossary>` に定義付きで注入する。あわせて A/B で採用決定済みのハイブリッド構成（レジュメのみ 3.5-flash・他 lite）を coreprompts の既定にする。

**Tech Stack:** Python 3.12, pytest, Gemini API（`core/llm_client.py`）, JSON プロンプト管理（`core/coreprompts.json`, `@lru_cache`）。

## Global Constraints

- 正本設計: `docs/superpowers/specs/2026-07-11-translation-context-stage2-term-layer-design.md`。上位: `docs/superpowers/specs/2026-07-10-translation-context-architecture-design.md`（4層モデル Stage 2）。
- **依存最小化**: 外部ライブラリを追加しない。標準ライブラリ（`dataclasses`, `csv`）のみ。
- **既存挙動の後方互換**: `core/config.py::load_glossary_csv`（en→ja を返す）は変更しない。`merge_with_glossary`（Phase 2）も変更しない。
- **縮退安全性**: 抽出失敗・空の場合は用語集が空になるだけで翻訳は壊れない。
- **文脈源の変更は 1 種類**: 本 Stage で変えるのは用語レイヤーのみ。レジュメ字数指示（`SUMMARY_PROMPT_ronbun` / `CHAPTER_SUMMARY_PROMPT` の分量）は**変更しない**（論点③据え置き）。
- **プロンプト変更後はプロセス再起動**（`@lru_cache`）。
- **モデル既定値**: `DEFAULT_MODEL = gemini-3.1-flash-lite`、`DEFAULT_MODEL_RESUME = gemini-3.5-flash`。`core/coreprompts.json` と `docs/model_optimization.md` は同時更新（CLAUDE.md 整合ルール）。
- **管理ログ**: `core/` 変更を含むため `docs/management/requirements_log.md` / `troubleshooting_log.md` への追記を実装コミットに含める。
- コミットメッセージは日本語（技術用語・識別子は英語のまま）。末尾に `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- テストは `python3 -m pytest tests/unit/ -q` で全合格を維持（現状 211 件）。

### 判断保留ポイント（実装中に確定する。迷ったらこの既定値で進める）

1. **抽出件数上限**: `KEYWORD_EXTRACTION_PROMPT` の上限。本 Plan は **30 語**を既定として書く。比較読みで平準化カバレッジ不足なら 40 へ、glossary 長大化なら 20 へ調整（プロンプト文字列のみの変更）。
2. **書籍 global_glossary の定義配線方式**: **確定済み** — `load_glossary_entries`（新設・3 列対応ローダ）を追加し、書籍で既に `glossary_path` に渡っている `global_glossary.csv`（definition 列付き）から定義を読む。新たな pipeline シグネチャ変更は不要（Task 1）。
3. **format の並び順**: **定義あり先頭 → 定義なし**を既定（Task 3）。比較読みで不都合なら source 順へ。
4. **定義文の長さ**: プロンプトで「1 文・目安 60 字」（Task 6）。逸脱時の機械的切り詰めはしない（縮退時のノイズ増を避ける）。
5. **Web 無料枠のレジュメ 3.5-flash 消費**: 管理者パスコード経由でサーバ側キーを使う無料モードでレジュメが 3.5-flash 無料枠（~10 RPM / ~250 RPD）を消費する。レジュメ呼び出しは論文 1 回・書籍 1＋章数回で収まる想定のため**本 Plan では許容**し、Task 8 の管理ログに明記する。ユーザー自身のキー利用時は当人の枠なので問題なし。

---

## File Structure

- **Create** `core/engine/p4_translate/term_layer.py` — `TermEntry`（dataclass）・`build_term_layer`・`format_term_layer`。用語レイヤーの唯一の責務境界。
- **Modify** `core/config.py` — `load_glossary_entries`（3 列対応ローダ）を追加。既存 `load_glossary_csv` は不変。
- **Modify** `core/engine/p4_translate/prompt_builder.py` — `TranslationPromptBuilder.glossary` を `list[TermEntry]` 化。`format_glossary` を `format_term_layer` へ委譲。
- **Modify** `core/phase4_translate.py` — 用語集組み立てインライン（:88-98, :106）を `load_glossary_entries` + `build_term_layer` へ置換。
- **Modify** `core/coreprompts.json` — `KEYWORD_EXTRACTION_PROMPT` 改修、`DEFAULT_MODEL` / `DEFAULT_MODEL_RESUME` の 2 値変更。
- **Modify** `docs/model_optimization.md` — ハイブリッド既定化を反映。
- **Modify** `docs/management/requirements_log.md` / `troubleshooting_log.md` — Stage 2 実装追記。
- **Create/Modify tests** `tests/unit/test_term_layer.py`（新）、`tests/unit/test_glossary_entries.py`（新）、`tests/unit/test_prompt_builder.py`（追記）、`tests/unit/test_coreprompts_stage2.py`（新）。

---

### Task 1: `load_glossary_entries`（3 列対応 CSV ローダ）

**Files:**
- Modify: `core/config.py`（`load_glossary_csv` の直後、`166` 付近に追加）
- Test: `tests/unit/test_glossary_entries.py`（新規）

**Interfaces:**
- Produces: `load_glossary_entries(path: str | Path | None = None) -> list[dict]`。各要素 `{"en": str, "ja": str, "definition": str}`。ヘッダー行（1 列目が既知ヘッダーキーワード）はスキップ。2 列目以降が欠けても落ちない。definition 列（3 列目）が無ければ `""`。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_glossary_entries.py`:

```python
import csv
from core.config import load_glossary_entries


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)


def test_reads_three_columns(tmp_path):
    p = tmp_path / "g.csv"
    _write_csv(p, [["en", "ja", "definition"],
                   ["displace", "転位させる", "確立した秩序からずらす意"]])
    entries = load_glossary_entries(p)
    assert entries == [{"en": "displace", "ja": "転位させる",
                        "definition": "確立した秩序からずらす意"}]


def test_two_column_csv_yields_empty_definition(tmp_path):
    p = tmp_path / "g.csv"
    _write_csv(p, [["agency", "行為主体性"]])   # ヘッダーなし・2 列
    entries = load_glossary_entries(p)
    assert entries == [{"en": "agency", "ja": "行為主体性", "definition": ""}]


def test_missing_file_returns_empty(tmp_path):
    assert load_glossary_entries(tmp_path / "nope.csv") == []


def test_skips_header_and_blank_keys(tmp_path):
    p = tmp_path / "g.csv"
    _write_csv(p, [["term", "ja", "definition"],   # ヘッダー
                   ["", "空キー", "x"],             # 空キーは除外
                   ["ethos", "エートス", ""]])
    entries = load_glossary_entries(p)
    assert entries == [{"en": "ethos", "ja": "エートス", "definition": ""}]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_glossary_entries.py -q`
Expected: FAIL（`ImportError: cannot import name 'load_glossary_entries'`）

- [ ] **Step 3: 最小実装を書く**

`core/config.py` の `load_glossary_csv` 関数（`return glossary` の行）の直後に追加:

```python
def load_glossary_entries(path: str | Path | None = None) -> list[dict]:
    """glossary CSV を en/ja/definition の 3 列で読み込む。

    load_glossary_csv（en→ja）とは別に、definition 列を保持する。
    書籍モードの global_glossary.csv（definition 列付き）から定義を取り出すために使う。
    ヘッダー判定・空キー除外は load_glossary_csv と同一。
    """
    if path is None:
        path = PROJECT_ROOT / "data" / "glossary.csv"
    path = Path(path)
    if not path.exists():
        return []

    _HEADER_KEYWORDS = {"en", "english", "englishterm", "word", "term", "source", "original"}
    entries: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if len(row) < 2:
                continue
            if i == 0 and row[0].strip().lower() in _HEADER_KEYWORDS:
                continue
            en = row[0].strip()
            if not en:
                continue
            entries.append({
                "en": en,
                "ja": row[1].strip(),
                "definition": row[2].strip() if len(row) >= 3 else "",
            })
    return entries
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_glossary_entries.py -q`
Expected: PASS（4 件）

- [ ] **Step 5: コミット**

```bash
git add core/config.py tests/unit/test_glossary_entries.py
git commit -m "feat: definition 列対応の load_glossary_entries を追加（Stage 2 用語レイヤー）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `TermEntry` と `build_term_layer`（フィールド別マージ）

**Files:**
- Create: `core/engine/p4_translate/term_layer.py`
- Test: `tests/unit/test_term_layer.py`（新規）

**Interfaces:**
- Consumes: `keywords_data: list[dict]`（`phase2_meta.json` 由来、`{en, ja, definition?}`）、`glossary_entries: list[dict]`（Task 1 の `load_glossary_entries` 由来）。
- Produces:
  - `@dataclass TermEntry(en: str, ja: str, definition: str = "", source: str = "local")`。`source ∈ {"local", "glossary"}`（本文抽出＝local / glossary CSV＝glossary。正本の {local, glossary_csv, book} を provenance の 2 値に簡素化。定義優先は「local 優先」で実現）。
  - `build_term_layer(keywords_data: list[dict], glossary_entries: list[dict]) -> list[TermEntry]`。dedup キー=`en.lower()`。**ja は glossary CSV 優先**、**definition は local（本文抽出）優先で空なら CSV 定義で補完**。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_term_layer.py`:

```python
from core.engine.p4_translate.term_layer import TermEntry, build_term_layer


def test_local_only():
    kw = [{"en": "displace", "ja": "ずらす", "definition": "秩序からの転位"}]
    entries = build_term_layer(kw, [])
    assert entries == [TermEntry("displace", "ずらす", "秩序からの転位", "local")]


def test_csv_ja_overrides_local_ja():
    kw = [{"en": "agency", "ja": "エージェンシー", "definition": "行為の力"}]
    csv = [{"en": "agency", "ja": "行為主体性", "definition": ""}]
    entries = build_term_layer(kw, csv)
    e = {t.en: t for t in entries}["agency"]
    assert e.ja == "行為主体性"          # 訳語は CSV 優先
    assert e.definition == "行為の力"     # 定義は local を保持


def test_definition_filled_from_csv_when_local_empty():
    kw = [{"en": "ethos", "ja": "エートス", "definition": ""}]
    csv = [{"en": "ethos", "ja": "", "definition": "書籍全体での含意"}]
    entries = build_term_layer(kw, csv)
    e = {t.en: t for t in entries}["ethos"]
    assert e.ja == "エートス"             # CSV ja が空なら上書きしない
    assert e.definition == "書籍全体での含意"  # local が空なら CSV 定義で補完


def test_local_definition_wins_over_csv_definition():
    kw = [{"en": "field", "ja": "フィールド", "definition": "章での特定用法"}]
    csv = [{"en": "field", "ja": "", "definition": "書籍レベルの一般定義"}]
    entries = build_term_layer(kw, csv)
    assert {t.en: t for t in entries}["field"].definition == "章での特定用法"


def test_csv_only_entry_added():
    entries = build_term_layer([], [{"en": "habitus", "ja": "ハビトゥス", "definition": "d"}])
    assert entries == [TermEntry("habitus", "ハビトゥス", "d", "glossary")]


def test_dedup_case_insensitive():
    kw = [{"en": "Agency", "ja": "行為主体", "definition": "x"}]
    csv = [{"en": "agency", "ja": "行為主体性", "definition": ""}]
    entries = build_term_layer(kw, csv)
    assert len(entries) == 1
    assert entries[0].ja == "行為主体性"


def test_blank_and_missing_en_skipped():
    kw = [{"en": "", "ja": "x", "definition": ""}, {"ja": "y"}]
    assert build_term_layer(kw, []) == []


def test_none_inputs_safe():
    assert build_term_layer(None, None) == []
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_term_layer.py -q`
Expected: FAIL（`ModuleNotFoundError: core.engine.p4_translate.term_layer`）

- [ ] **Step 3: 最小実装を書く**

`core/engine/p4_translate/term_layer.py`:

```python
"""翻訳コンテキストの「② 術語＝統合用語レイヤー」。

glossary（訳語対応）と local_definitions（本文抽出の術語定義）を単一の
構造化レイヤーに統合する。詳細は
docs/superpowers/specs/2026-07-11-translation-context-stage2-term-layer-design.md 参照。
"""
from dataclasses import dataclass


@dataclass
class TermEntry:
    en: str
    ja: str
    definition: str = ""
    source: str = "local"   # "local"（本文抽出）| "glossary"（glossary CSV）


def build_term_layer(keywords_data, glossary_entries):
    """本文抽出（keywords_data）と glossary CSV（glossary_entries）を統合する。

    - dedup キー: en.lower()
    - 訳語 ja: glossary CSV 優先（ユーザー/書籍が権威）
    - 定義 definition: local（本文抽出）優先。local が空なら CSV 定義で補完。
    """
    merged: dict[str, TermEntry] = {}

    # 1. 本文抽出を基層に（source=local）
    for kw in keywords_data or []:
        en = (kw.get("en") or "").strip()
        if not en:
            continue
        merged[en.lower()] = TermEntry(
            en=en,
            ja=(kw.get("ja") or "").strip(),
            definition=(kw.get("definition") or "").strip(),
            source="local",
        )

    # 2. glossary CSV を重ねる（ja は CSV 優先、definition は local 優先で空なら補完）
    for g in glossary_entries or []:
        en = (g.get("en") or "").strip()
        if not en:
            continue
        key = en.lower()
        g_ja = (g.get("ja") or "").strip()
        g_def = (g.get("definition") or "").strip()
        if key in merged:
            e = merged[key]
            if g_ja:
                e.ja = g_ja
            if not e.definition and g_def:
                e.definition = g_def
        else:
            merged[key] = TermEntry(en=en, ja=g_ja, definition=g_def, source="glossary")

    return list(merged.values())
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_term_layer.py -q`
Expected: PASS（8 件）

- [ ] **Step 5: コミット**

```bash
git add core/engine/p4_translate/term_layer.py tests/unit/test_term_layer.py
git commit -m "feat: TermEntry と build_term_layer（フィールド別マージ）を追加（Stage 2）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `format_term_layer`（定義付き描画・定義あり先頭）

**Files:**
- Modify: `core/engine/p4_translate/term_layer.py`
- Test: `tests/unit/test_term_layer.py`（追記）

**Interfaces:**
- Consumes: `list[TermEntry]`（Task 2）。
- Produces: `format_term_layer(entries: list[TermEntry]) -> str`。空なら `""`。定義あり（`- en → ja：definition`）を先頭に、定義なし（`- en → ja`）を後に。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_term_layer.py` に追記:

```python
from core.engine.p4_translate.term_layer import format_term_layer


def test_format_empty_returns_empty():
    assert format_term_layer([]) == ""


def test_format_with_and_without_definition_ordering():
    entries = [
        TermEntry("plain", "ふつう", "", "local"),           # 定義なし
        TermEntry("displace", "転位", "秩序からずらす", "local"),  # 定義あり
    ]
    out = format_term_layer(entries)
    assert "# 用語集 (Glossary)" in out
    assert "- displace → 転位：秩序からずらす" in out
    assert "- plain → ふつう" in out
    # 定義あり（displace）が定義なし（plain）より前
    assert out.index("displace") < out.index("plain")
    # 定義なし行に全角コロンの定義区切りが付かない
    assert "- plain → ふつう：" not in out
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_term_layer.py -k format -q`
Expected: FAIL（`ImportError: cannot import name 'format_term_layer'`）

- [ ] **Step 3: 最小実装を書く**

`core/engine/p4_translate/term_layer.py` の末尾に追加:

```python
def format_term_layer(entries) -> str:
    """用語レイヤーを翻訳プロンプトの <glossary> 用に整形する。

    定義付きの語（＝特殊用法・高価値）を先頭に、定義なしを後に並べる。
    """
    if not entries:
        return ""
    with_def = [e for e in entries if e.definition]
    without_def = [e for e in entries if not e.definition]
    lines = [
        "# 用語集 (Glossary)",
        "指定された日本語訳を優先的に使用してください。定義が付された語は、"
        "この文献での特定の含意を示すため、訳語選択の際に踏まえてください。",
    ]
    for e in with_def:
        lines.append(f"- {e.en} → {e.ja}：{e.definition}")
    for e in without_def:
        lines.append(f"- {e.en} → {e.ja}")
    return "\n".join(lines)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_term_layer.py -q`
Expected: PASS（10 件）

- [ ] **Step 5: コミット**

```bash
git add core/engine/p4_translate/term_layer.py tests/unit/test_term_layer.py
git commit -m "feat: format_term_layer（定義付き描画・定義あり先頭）を追加（Stage 2）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `TranslationPromptBuilder` を用語レイヤーへ接続

**Files:**
- Modify: `core/engine/p4_translate/prompt_builder.py:1-24`
- Test: `tests/unit/test_prompt_builder.py`（追記）

**Interfaces:**
- Consumes: `TermEntry` / `format_term_layer`（Task 2-3）。
- Produces: `TranslationPromptBuilder(prompt_template, glossary: list[TermEntry] | None = None)`。`format_glossary() -> str` は `format_term_layer(self.glossary)` に委譲（呼び出し元 `phase4_translate.py:51` は変更不要）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_prompt_builder.py` に追記:

```python
from core.engine.p4_translate.term_layer import TermEntry


def test_format_glossary_empty_default():
    # glossary 未指定なら空文字列
    assert TranslationPromptBuilder("tpl").format_glossary() == ""


def test_format_glossary_renders_term_entries():
    entries = [TermEntry("displace", "転位", "秩序からずらす", "local")]
    b = TranslationPromptBuilder("tpl", glossary=entries)
    out = b.format_glossary()
    assert "- displace → 転位：秩序からずらす" in out
    assert "# 用語集 (Glossary)" in out
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_prompt_builder.py -k glossary -q`
Expected: FAIL（現 `format_glossary` は `dict.items()` を呼ぶため `TermEntry` リストで `AttributeError`、または旧描画で不一致）

- [ ] **Step 3: 最小実装を書く**

`core/engine/p4_translate/prompt_builder.py` の冒頭 import に追加:

```python
from core.engine.p4_translate.term_layer import TermEntry, format_term_layer
```

`__init__` と `format_glossary` を置換:

```python
    def __init__(self, prompt_template: str, glossary: Optional[List[TermEntry]] = None):
        self.prompt_template = prompt_template
        self.glossary = glossary or []

    def format_glossary(self) -> str:
        """用語レイヤーをプロンプト用に整形する（term_layer.format_term_layer へ委譲）。"""
        return format_term_layer(self.glossary)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/unit/test_prompt_builder.py -q`
Expected: PASS（既存 + 新規 2 件）

- [ ] **Step 5: コミット**

```bash
git add core/engine/p4_translate/prompt_builder.py tests/unit/test_prompt_builder.py
git commit -m "refactor: TranslationPromptBuilder を用語レイヤー（TermEntry）へ接続（Stage 2）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `phase4_translate.py` の用語集組み立てを差し替え

**Files:**
- Modify: `core/phase4_translate.py:7`（import）, `:88-98`（組み立て）, `:106`（builder 生成）

**Interfaces:**
- Consumes: `load_glossary_entries`（Task 1）, `build_term_layer`（Task 2）, `TranslationPromptBuilder`（Task 4）。
- Produces: 挙動変更のみ（新規公開 API なし）。翻訳プロンプトの `<glossary>` に定義付き用語レイヤーが届く。

> このタスクは配線の差し替え（リファクタ）。検証は「全ユニット回帰緑」＋「旧平坦化コードが消えたことの grep 確認」で行う。用語レイヤーのロジックは Task 1-4 で単体網羅済み。

- [ ] **Step 1: import を差し替え**

`core/phase4_translate.py:7` を:

```python
from .config import load_coreprompts, load_glossary_csv, print_log
```

から:

```python
from .config import load_coreprompts, load_glossary_entries, print_log
from .engine.p4_translate.term_layer import build_term_layer
```

に変更（`load_glossary_csv` は本ファイルで他に使われていないため削除。※ grep で確認: `grep -n load_glossary_csv core/phase4_translate.py` が import 行以外にヒットしないこと）。

- [ ] **Step 2: 組み立てブロックを差し替え**

`core/phase4_translate.py:88-98` の現行:

```python
    prompts = load_coreprompts()
    master_glossary = load_glossary_csv(glossary_path)
    resume_context = ""
    if Path(phase2_state_path).exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            p2_data = json.load(f)
            resume_context = p2_data.get("resume_content", "")
            # DNA キーワードを用語集に統合
            for kw in p2_data.get("keywords_data", []):
                if kw.get("en") and kw["en"] not in master_glossary:
                    master_glossary[kw["en"]] = kw.get("ja", "")
```

を:

```python
    prompts = load_coreprompts()
    glossary_entries = load_glossary_entries(glossary_path)
    keywords_data = []
    resume_context = ""
    if Path(phase2_state_path).exists():
        with open(phase2_state_path, "r", encoding="utf-8") as f:
            p2_data = json.load(f)
            resume_context = p2_data.get("resume_content", "")
            keywords_data = p2_data.get("keywords_data", [])
    # 用語レイヤー: 本文抽出（定義付き）＋ glossary CSV（書籍は定義列付き）を統合
    term_entries = build_term_layer(keywords_data, glossary_entries)
```

に置換。

- [ ] **Step 3: builder 生成を差し替え**

`core/phase4_translate.py:106` の:

```python
    prompt_builder = TranslationPromptBuilder(prompts["TRANSLATION_PROMPT"], glossary=master_glossary)
```

を:

```python
    prompt_builder = TranslationPromptBuilder(prompts["TRANSLATION_PROMPT"], glossary=term_entries)
```

に置換。

- [ ] **Step 4: 旧コードが消えたことを確認**

Run: `grep -n "master_glossary\|load_glossary_csv" core/phase4_translate.py`
Expected: 出力なし（0 件）

- [ ] **Step 5: 全ユニット回帰を確認**

Run: `python3 -m pytest tests/unit/ -q`
Expected: PASS（全件。既存 211 + 新規分）

- [ ] **Step 6: コミット**

```bash
git add core/phase4_translate.py
git commit -m "feat: Phase 4 の用語集組み立てを統合用語レイヤーへ差し替え（Stage 2 配線）

definition を捨てる master_glossary 平坦化を廃し、load_glossary_entries +
build_term_layer で定義付き用語レイヤーを <glossary> へ注入。書籍モードは
global_glossary.csv の definition 列経由で書籍定義も届く。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `KEYWORD_EXTRACTION_PROMPT` を「中庸＋特殊用法込み」へ改修

**Files:**
- Modify: `core/coreprompts.json`（`KEYWORD_EXTRACTION_PROMPT` の値）
- Test: `tests/unit/test_coreprompts_stage2.py`（新規）

**Interfaces:**
- Produces: 改修後プロンプト（プレースホルダ `{expertise}` / `{text}` は維持）。特殊用法抽出・定義付与・空許容（グラウンディング）・件数上限 30 を含む。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_coreprompts_stage2.py`（新規）:

```python
from core.config import load_coreprompts


def test_keyword_prompt_has_stage2_markers():
    p = load_coreprompts()["KEYWORD_EXTRACTION_PROMPT"]
    # プレースホルダ維持
    assert "{expertise}" in p and "{text}" in p
    # 特殊用法（平準化対策）の抽出指示
    assert "特殊" in p
    # グラウンディング（定義できない語は空）
    assert '""' in p or "空" in p
    # 件数上限
    assert "30" in p
    # 出力フォーマット維持
    assert '"definition"' in p
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_coreprompts_stage2.py::test_keyword_prompt_has_stage2_markers -q`
Expected: FAIL（現行プロンプトに「特殊」「30」等が無い）

- [ ] **Step 3: プロンプトを差し替え**

`core/coreprompts.json` の `KEYWORD_EXTRACTION_PROMPT` の値を、以下の内容に置換（JSON 文字列として、改行は `\n`、二重引用符は `\"` でエスケープすること）:

```
あなたは {expertise} を専門とする研究者です。提供された文献から、翻訳の際に訳語がブレやすい「専門用語」「固有名詞」「著者特有の概念語」を抽出し、日本語訳と、この文献での含意を示す簡潔な定義を付与してください。

【抽出対象】
1. 明示的に定義・導入されている専門用語・概念語。
2. 一般的な英単語であっても、この文献で標準的な日本語訳とはズレた特殊な含意・用法で使われている語（例: 日常語が理論的な意味を担っている場合）。これらは語彙の平準化（特殊な用法が凡庸な訳語へ均される現象）を防ぐために重要です。
3. 学術的・地域的な固有名詞。

【定義（definition）の付与ルール】
- その語がこの文献で持つ特定の含意を、1 文・目安 60 字程度で簡潔に記してください。
- 本文から特定の含意・特殊用法が読み取れない語は、無理に定義を作らず definition を空文字列("")にしてください（根拠のない定義を創作しないこと）。

【全体ルール】
1. 抽出は最大 30 語までとし、訳語がブレると読解に影響する語を優先してください。
2. ありふれた一般語（訳語が自明で特殊用法もない語）は除外してください。
3. 出力は必ず指定された JSON 配列形式のみとし、Markdown コードブロックや前置きは一切含めないでください。

【出力形式】
[{"en": "...", "ja": "...", "definition": "..."}]

【INPUT】
[Raw Text]
{text}

---
上記テキストから、上記ルールに従って専門用語・特殊用法語を抽出し、JSON 配列のみを出力してください。
```

> 注意（判断保留 ①④）: `最大 30 語` と `60 字` は既定値。比較読み（Task 8 後）で調整可。`{}` を含む出力形式例（`[{"en": ...}]`）は JSON 内では `[{\"en\": ...}]` となる。既存プロンプト同様のエスケープを踏襲すること。差し替え後、`python3 -c "import json; json.load(open('core/coreprompts.json'))"` で JSON 妥当性を確認する。

- [ ] **Step 4: JSON 妥当性とテストを確認**

Run: `python3 -c "import json; json.load(open('core/coreprompts.json')); print('ok')"`
Expected: `ok`

Run: `python3 -m pytest tests/unit/test_coreprompts_stage2.py -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add core/coreprompts.json tests/unit/test_coreprompts_stage2.py
git commit -m "feat: KEYWORD_EXTRACTION_PROMPT を中庸＋特殊用法込みに改修（Stage 2）

明示定義語に加え標準訳とズレる特殊用法語も定義付きで抽出。件数上限 30・
定義できない語は空（グラウンディング）で暴発を抑制。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: ハイブリッド構成を coreprompts の既定にする

**Files:**
- Modify: `core/coreprompts.json`（`DEFAULT_MODEL`, `DEFAULT_MODEL_RESUME`）
- Modify: `docs/model_optimization.md`
- Test: `tests/unit/test_coreprompts_stage2.py`（追記）

**Interfaces:**
- Consumes: `core.config.load_coreprompts`。
- Produces: `DEFAULT_MODEL = "gemini-3.1-flash-lite"`、`DEFAULT_MODEL_RESUME = "gemini-3.5-flash"`。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_coreprompts_stage2.py` に追記:

```python
def test_hybrid_defaults_are_baked_in():
    p = load_coreprompts()
    assert p["DEFAULT_MODEL"] == "gemini-3.1-flash-lite"        # 翻訳等は lite
    assert p["DEFAULT_MODEL_RESUME"] == "gemini-3.5-flash"      # レジュメのみ強モデル
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/unit/test_coreprompts_stage2.py::test_hybrid_defaults_are_baked_in -q`
Expected: FAIL（現状 `DEFAULT_MODEL=gemini-3.5-flash`, `DEFAULT_MODEL_RESUME=""`）

- [ ] **Step 3: coreprompts.json の 2 値を変更**

`core/coreprompts.json`:
- `"DEFAULT_MODEL": "gemini-3.5-flash"` → `"DEFAULT_MODEL": "gemini-3.1-flash-lite"`
- `"DEFAULT_MODEL_RESUME": ""` → `"DEFAULT_MODEL_RESUME": "gemini-3.5-flash"`

- [ ] **Step 4: `docs/model_optimization.md` を更新**

`docs/model_optimization.md` の該当箇所に、ハイブリッド既定化を反映する追記を行う（現行の推奨表・runtime 設定値の記述に合わせる）。最低限、以下を明記:
- 既定は「レジュメ生成（論文/書籍全体/章）のみ `gemini-3.5-flash`、翻訳含む他は `gemini-3.1-flash-lite`」のハイブリッド。
- 根拠: 2026-07-11 の Stage 1 モデル A/B（NST）で Arm B（ハイブリッド）採用決定。GA 値上げで価格差 6 倍。
- `DEFAULT_MODEL_RESUME` はティア非追従（無料モードでもレジュメは 3.5-flash。無料枠内で収まる）。

> 具体的な節・行は実装時に `docs/model_optimization.md` を開いて現行構成に合わせる。§5（トークン収支）付近か runtime 設定値の記述箇所が対象。

- [ ] **Step 5: テストと整合を確認**

Run: `python3 -c "import json; json.load(open('core/coreprompts.json')); print('ok')"`
Expected: `ok`

Run: `python3 -m pytest tests/unit/test_coreprompts_stage2.py tests/unit/test_resume_model_routing.py -q`
Expected: PASS（既存の routing テストは fake_prompts 使用のため影響なし）

- [ ] **Step 6: コミット**

```bash
git add core/coreprompts.json docs/model_optimization.md tests/unit/test_coreprompts_stage2.py
git commit -m "feat: ハイブリッド構成（レジュメのみ 3.5-flash・他 lite）を既定化（Stage 2）

A/B で採用決定済み。DEFAULT_MODEL→lite・DEFAULT_MODEL_RESUME→3.5-flash。
model_optimization.md も同時更新（CLAUDE.md 整合ルール）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 全体検証・管理ログ追記・ゴールデン確認

**Files:**
- Modify: `docs/management/requirements_log.md`, `docs/management/troubleshooting_log.md`

- [ ] **Step 1: 全ユニットテスト**

Run: `python3 -m pytest tests/unit/ -q`
Expected: PASS（全件緑。新規テスト: glossary_entries 4 + term_layer 10 + prompt_builder 2 + coreprompts_stage2 2）

- [ ] **Step 2: ゴールデン構造回帰（論文モード）**

`golden-verification` skill に従い、AL/NST で構造回帰がないことを確認する。用語レイヤーは翻訳の背景注入のみで構造に影響しないことを確認（見出し抽出・階層は不変）。

Run（例）: `python3 main.py data/input/paperplain/NST/NSTsample.txt --lite`
Expected: Phase 1-5 完走、`_p2.md` 生成、セクション構造が理想出力と一致（訳文の一致は不要）。翻訳プロンプトの `<glossary>` に定義付きエントリが載ることをログ/中間状態で確認。

- [ ] **Step 3: 管理ログ追記**

`docs/management/requirements_log.md` に「2026-07-11: 翻訳コンテキスト Stage 2（統合用語レイヤー）実装完了」節を追記（要旨: 定義配線・TermEntry/term_layer 隔離・フィールド別マージ・KEYWORD_EXTRACTION_PROMPT 中庸改修・ハイブリッド既定化・レジュメ長据え置き・判断保留の確定値）。
`docs/management/troubleshooting_log.md` に「用語集パイプラインが dict[str,str] 固定で definition が 2 箇所（load_glossary_csv / phase4:96-98）で欠落していた」根本原因と対策を追記。**判断保留 ⑤（Web 無料枠のレジュメ 3.5-flash 消費）を許容判断として明記。**

- [ ] **Step 4: 次ステップの申し送りを記録**

管理ログに「次ステップ（ユーザー実施・本 Plan スコープ外）」として明記:
- 比較読み（`docs/translation_review_checklist.md`、NST で Stage 2 前後・ハイブリッド固定、`displace` 等の平準化改善を重点確認）。
- その結果を入力に (a) レジュメ長再評価（論点③宿題）、(b) 抽出積極性の微調整（判断保留①）、(c) Stage 3（argument_tree）起案。

- [ ] **Step 5: コミット**

```bash
git add docs/management/requirements_log.md docs/management/troubleshooting_log.md
git commit -m "docs: 翻訳コンテキスト Stage 2 実装完了を管理ログに記録

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage（各 Spec 要件 → 実装タスク）:**
- 用語レイヤー構造化型 `TermEntry` → Task 2 ✅
- 新モジュール `term_layer.py` への隔離 → Task 2-3 ✅
- フィールド別マージ（訳語 CSV 優先・定義本文優先） → Task 2 ✅
- 定義を翻訳まで配線（2 箇所の欠落解消） → Task 1（load_glossary_entries）+ Task 5（phase4）✅
- 書籍 global_glossary の定義配線（判断保留②確定） → Task 1 + Task 5 ✅
- 描画（定義付き・定義あり先頭） → Task 3 ✅
- 抽出プロンプト中庸＋特殊用法込み・件数上限・空許容 → Task 6 ✅
- ハイブリッド既定化（2 値＋docs 同時更新） → Task 7 ✅
- レジュメ長据え置き → Global Constraints で明記（変更しない）✅
- テスト/ゴールデン/比較読み → Task 8 ✅
- 管理ログ・判断保留⑤明記 → Task 8 ✅

**2. Placeholder scan:** 各コードステップに実コードあり。プロンプト差し替え（Task 6）と docs 更新（Task 7 Step 4）は対象ファイルの現行構成に合わせる旨を明記済みで「TODO/後で」型の空欄なし。

**3. Type consistency:** `TermEntry(en, ja, definition="", source="local")` は Task 2 で定義、Task 3/4 で同一シグネチャ参照。`build_term_layer(keywords_data, glossary_entries)` / `format_term_layer(entries)` / `load_glossary_entries(path)` の名前・引数は全タスクで一貫。`TranslationPromptBuilder(prompt_template, glossary: list[TermEntry])` は Task 4 で定義し Task 5 が `glossary=term_entries` で呼ぶ（型一致）。

---

## Execution Handoff

（writing-plans の末尾で実行方式を選択する）
