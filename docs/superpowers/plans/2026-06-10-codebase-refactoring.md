# p2workflowy 全体リファクタリング実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 長年蓄積したデッドコード・重複実装・アーキテクチャ乖離を 6 フェーズで段階的に除去し、CLAUDE.md の設計原則（ファサード＝オーケストレーション専任、アルゴリズム＝エンジン層）と実装を一致させる。

**Architecture:** 挙動を一切変えない「behavior-preserving refactoring」。各フェーズ末に全テスト（ベースライン: 184 passed）を実行し、フェーズごとにコミットする。削除 → 重複統合 → Phase 3 ファサード解体 → Phase 1 配置整理 → web 統合 → ドキュメント同期の順。リスクの低い順に進めるため、途中でいつでも中断できる。

**Tech Stack:** Python 3.12 / pytest / Git。新規依存は追加しない。

---

## 不変条件（全フェーズ共通）

- 各タスク完了時に `python3 -m pytest tests/unit/ -q` が **184 passed**（テスト削除・改名したタスク以降は更新後の件数）であること。
- パイプラインの出力（`_p2.md` / `_p2.txt`）のバイト列を変えるコード変更は行わない。
- 1 タスク = 1 コミット。コミットメッセージは日本語。
- 削除はすべて `git rm` / 通常の編集で行い、git 履歴から復元可能な状態を保つ。

## 事前調査で確定した事実（2026-06-10 検証済み）

| 事実 | 検証コマンド |
|---|---|
| `engine/p3_structure/tree_constructor.py`・`heading_detector.py`・`toc_manager.py` は本番コードから参照ゼロ（heading_detector は tree_constructor のみが参照、tree_constructor と toc_manager は参照元なし） | `grep -rn "tree_constructor\|toc_manager" --include="*.py" core/ main.py server.py` |
| `pdf_ingester.py` の `run_pdf_ingestion_async`（旧ルート、17〜100行付近）と同期版 `run_pdf_ingestion`（219〜222行）は呼び出し元なし。本番は `run_pdf_ingestion_v3` のみ | `grep -rn "run_pdf_ingestion\b\|run_pdf_ingestion_async" --include="*.py" core/ main.py server.py tests/ scripts/ \| grep -v v3` |
| `LayoutEngine` の利用箇所は旧ルート内（`pdf_ingester.py:31`）のみ | `grep -rn "LayoutEngine" --include="*.py" core/ main.py server.py tests/` |
| `text_utils.py` の `normalize_heading`・`roman_to_int`・`is_roman_numeral`・`extract_headings_from_resume`・`STOP_SECTIONS` は本番から import されていない（本番が import するのは `_SENTENCE_END_RE` と `_TRAILING_WORDS` のみ） | `grep -rn "from .text_utils import\|from core.text_utils import" --include="*.py" core/ main.py server.py tests/` |
| `normalize_heading` / `extract_headings_from_resume` の**本番実体**は `phase3_structure.py:686` / `:1316`。text_utils 版とは挙動が異なる（phase3 版は章番号・ローマ数字を剥離する） | 両ファイルの Read 比較 |
| spread_splitter は 2 系統: `core/spread_splitter.py`（PDF レベル、book_manager が使用）と `core/engine/p1_ingest/spread_splitter.py`（PIL 画像レベル、pdf_ingester が使用）。ノド検出ロジック（中央40-60%帯・輝度積分・window=20 平滑化）がコピーされ、信頼性判定だけ異なる（core 版が新しく高精度） | 両ファイルの Read 比較 |
| `engine/p3_structure/chapter_parser.py:37` がファサード `core.phase3_structure` を逆 import している（依存逆転） | `grep -n "from core.phase3_structure" core/engine/p3_structure/chapter_parser.py` |
| `tests/unit/test_phase3_structure.py` は `is_excluded_heading`, `normalize_heading`, `match_heading`, `structure_nodes_by_headings` を `core.phase3_structure` から import | 同ファイル 12〜17 行 |
| ベースライン: `python3 -m pytest tests/unit/ -q` → **184 passed**, 13.5s | 実行済み |

---

# フェーズ A: デッドコード削除（挙動不変・最低リスク）

## Task A1: engine/p3_structure の孤児モジュール 3 件を削除

**Files:**
- Delete: `core/engine/p3_structure/tree_constructor.py`
- Delete: `core/engine/p3_structure/heading_detector.py`
- Delete: `core/engine/p3_structure/toc_manager.py`
- Delete: `tests/unit/test_heading_detector.py`

これらは Phase 3 エンジン層抽出の放棄された試みであり、本番アルゴリズムは `phase3_structure.py` 内の同名関数が担っている。テストは孤児コードをテストしているため一緒に削除する（フェーズ C で本番実体を移設したあと、本番実体に対するテストとして復活させる）。

- [ ] **Step 1: 参照ゼロを再確認する**

```bash
grep -rn "tree_constructor\|TreeConstructor\|heading_detector\|HeadingDetector\|toc_manager\|TOCManager" \
  --include="*.py" core/ main.py server.py scripts/ \
  | grep -v "core/engine/p3_structure/tree_constructor.py:" \
  | grep -v "core/engine/p3_structure/heading_detector.py:" \
  | grep -v "core/engine/p3_structure/toc_manager.py:"
```

Expected: 出力なし（tests/ 内の test_heading_detector.py のみが残るが、それも削除対象）。もし本番参照が見つかったらこのタスクを中止して報告する。

- [ ] **Step 2: 4 ファイルを削除する**

```bash
git rm core/engine/p3_structure/tree_constructor.py \
       core/engine/p3_structure/heading_detector.py \
       core/engine/p3_structure/toc_manager.py \
       tests/unit/test_heading_detector.py
```

- [ ] **Step 3: テストを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: PASS（test_heading_detector.py の 4 テストが減るため **180 passed**）

- [ ] **Step 4: コミット**

```bash
git commit -m "refactor: 未参照の p3_structure 孤児モジュール3件とそのテストを削除"
```

## Task A2: pdf_ingester.py の旧ルートと LayoutEngine を削除

**Files:**
- Modify: `core/pdf_ingester.py`（`run_pdf_ingestion_async` 関数全体、`run_pdf_ingestion` 関数全体、`LayoutEngine` の import 行を削除）
- Delete: `core/engine/p1_ingest/layout_engine.py`

- [ ] **Step 1: 旧ルートの呼び出し元がないことを再確認する**

```bash
grep -rn "run_pdf_ingestion_async\|run_pdf_ingestion\b" --include="*.py" \
  core/ main.py server.py tests/ scripts/ | grep -v "_v3" | grep -v "core/pdf_ingester.py:"
```

Expected: 出力なし。

- [ ] **Step 2: `core/pdf_ingester.py` から以下を削除する**

1. `async def run_pdf_ingestion_async(...)` 関数全体（17 行目から `run_pdf_ingestion_v3_async` の直前まで）
2. `def run_pdf_ingestion(pdf_path: str, **kwargs) -> str:` 関数全体（219〜222 行付近の 4 行）
3. import 行 `from .engine.p1_ingest.layout_engine import LayoutEngine`
4. モジュール docstring 内の「LayoutEngine」への言及（3 行目付近）を OCRManager / PhysicalIngester のみに修正

- [ ] **Step 3: LayoutEngine が孤児化したことを確認して削除する**

```bash
grep -rn "LayoutEngine\|layout_engine" --include="*.py" core/ main.py server.py tests/ scripts/
```

Expected: `core/engine/p1_ingest/layout_engine.py:` 自身の行のみ。確認後:

```bash
git rm core/engine/p1_ingest/layout_engine.py
```

※ もし `physical_ingester.py` 等から実参照が見つかった場合は layout_engine.py を残し、旧ルート削除のみコミットする。

- [ ] **Step 4: テストを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: **180 passed**

- [ ] **Step 5: コミット**

```bash
git add -A && git commit -m "refactor: pdf_ingester の旧ハイブリッドルートと孤児化した LayoutEngine を削除"
```

## Task A3: text_utils.py の未使用関数を削除

**Files:**
- Modify: `core/text_utils.py`

本番が import しているのは `_SENTENCE_END_RE` と `_TRAILING_WORDS` のみ。`normalize_heading` 等の本番実体は phase3_structure.py 側にあり、text_utils 版は古い別実装（混乱の元）。

- [ ] **Step 1: text_utils の関数が本当に未参照か確認する**

```bash
grep -rn "text_utils" --include="*.py" core/ main.py server.py tests/ scripts/
```

Expected: `core/phase1_preprocessor.py:14` と `core/phase3_structure.py:20`（いずれも定数 2 つの import）と text_utils.py 自身のみ。

- [ ] **Step 2: `core/text_utils.py` を以下の内容に置き換える（定数 2 つだけ残す）**

```python
import re

# 文末判定用正規表現（引用ブラケット対応）
# Phase 1, Phase 3 で共有
_SENTENCE_END_RE = re.compile(r"""[.!?;:\"'](?:\[[\d,\s-]+\])?\s*$""")

# Trailing words リスト（前置詞・冠詞等、行末にある場合に結合を促す単語群）
# Phase 1, Phase 3 で共有
_TRAILING_WORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "by", "from", "as", "is",
    "was", "were", "are", "has", "had", "have", "that",
    "which", "who", "whom", "this", "these", "those",
])
```

- [ ] **Step 3: テストを実行する**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: **180 passed**

- [ ] **Step 4: コミット**

```bash
git add core/text_utils.py && git commit -m "refactor: text_utils の未使用関数（normalize_heading 旧版・ローマ数字変換等）を削除"
```

## Task A4: test_rtt_v34.py を実態に合わせて改名

**Files:**
- Rename: `tests/unit/test_rtt_v34.py` → `tests/unit/test_translate_batch.py`

ファイル冒頭の docstring 自身が「translate_batch のユニットテスト（旧 test_rtt_v34.py）」と宣言している。

- [ ] **Step 1: リネームする**

```bash
git mv tests/unit/test_rtt_v34.py tests/unit/test_translate_batch.py
```

- [ ] **Step 2: docstring 1 行目を更新する**

`tests/unit/test_translate_batch.py` の 2 行目を:

```
translate_batch のユニットテスト（旧 test_rtt_v34.py）
```

から

```
translate_batch のユニットテスト
```

に変更。

- [ ] **Step 3: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/test_translate_batch.py -q   # Expected: 全件 PASS
python3 -m pytest tests/unit/ -q                          # Expected: 180 passed
git add -A && git commit -m "refactor: test_rtt_v34 を test_translate_batch に改名"
```

## Task A5（任意・ユーザー確認後に実施）: 一回性スクリプトのアーカイブ

**Files:**
- Move: `scripts/ab_test_vlm_layouts.py` → `archive/scripts/`（gitignore 対象 = リポジトリから除去）
- Move: `scripts/compare_ab_results.py` → `archive/scripts/`
- Keep: `scripts/benchmark_concurrent.py`（docs/model_optimization.md の並列数ベンチの再現手段として保持）

A/B テストスクリプト 2 件は docs からの参照がなく、検証完了済みの一回性スクリプト。**削除はユーザー確認が必要**（グローバル CLAUDE.md「破壊的操作は確認」）。実行時にユーザーへ確認し、否なら skip して次へ。

- [ ] **Step 1: ユーザーに確認**（確認が取れなければこのタスクを skip）
- [ ] **Step 2: 移動してコミット**

```bash
mkdir -p archive/scripts
git mv scripts/ab_test_vlm_layouts.py scripts/compare_ab_results.py archive/scripts/ 2>/dev/null \
  || { mv scripts/ab_test_vlm_layouts.py scripts/compare_ab_results.py archive/scripts/; git rm --cached scripts/ab_test_vlm_layouts.py scripts/compare_ab_results.py 2>/dev/null; git add -A; }
git commit -m "chore: 検証完了済みの A/B テストスクリプトを archive へ退避"
```

---

# フェーズ B: spread_splitter 二系統の統合

## Task B1: ノド検出コアを 1 箇所に統合する

**Files:**
- Modify: `core/engine/p1_ingest/spread_splitter.py`（PDF レベル関数を統合した正本にする）
- Delete: `core/spread_splitter.py`
- Modify: `core/book_manager.py:144`（import 先変更）
- Modify: `tests/unit/test_spread_splitter.py`（import 先変更）

方針: **`core/spread_splitter.py` のアルゴリズム（新しい・信頼性判定が高精度）を正本とし、engine 側へ移設**。PIL 画像レベルの API（pdf_ingester の VLM ルートが使用）は、グレースケール ndarray を共有コア関数に渡す薄いラッパーとして書き直す。

- [ ] **Step 1: 統合版 `core/engine/p1_ingest/spread_splitter.py` を書く**

既存の engine 版（73 行）を以下の構成で全面置換する:

```python
"""
見開きスキャンページの検出・分割ユーティリティ。

PDF レベル API（書籍モード前処理: book_manager が使用）と
PIL 画像レベル API（VLM ルート内の保険: pdf_ingester が使用）の両方を提供する。
ノド（綴じ目）検出コアは _find_gutter_in_gray() に一本化。
"""

import fitz
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image

from core.config import print_log

SPREAD_ASPECT_THRESHOLD = 1.1


# ─── 共有コア ───

def _find_gutter_in_gray(gray: np.ndarray) -> Tuple[int, bool]:
    """グレースケール 2 次元配列からノドのピクセル X 座標と信頼性を返す。

    中央±20% の帯域で輝度の縦積分 + window=20 移動平均により
    最も白い列を検出する。信頼性判定は現行 core/spread_splitter.py の
    レンジベース基準（旧 engine 版の max>avg*1.1 基準より高精度）を採用。
    """
    height, width = gray.shape
    x_min = int(width * 0.40)
    x_max = int(width * 0.60)
    if x_max <= x_min:
        return width // 2, False

    col_sums = gray[:, x_min:x_max].sum(axis=0).astype(float)

    window = 20
    if len(col_sums) > window:
        smoothed = np.convolve(col_sums, np.ones(window) / window, mode='valid')
        rel_idx = int(np.argmax(smoothed))
        max_val = float(smoothed.max())
        pixel_x = x_min + rel_idx + window // 2
    else:
        rel_idx = int(np.argmax(col_sums))
        max_val = float(col_sums.max())
        pixel_x = x_min + rel_idx

    avg_val = float(col_sums.mean())
    min_val = float(col_sums.min())
    luminance_range = max_val - min_val
    is_reliable = (
        luminance_range > height * 10
        and (max_val - avg_val) / (luminance_range + 1) > 0.25
        and avg_val < 250.0 * height
    )
    return pixel_x, is_reliable
```

続けて、現行 `core/spread_splitter.py` から以下を**そのまま移設**する（`from .config import print_log` を `from core.config import print_log` に変更する以外は無改変）:

- `is_spread_pdf()`（20〜31 行）
- `split_spread_pdf()`（34〜94 行）
- `_page_aspect()`（101〜103 行）
- `_find_gutter_x(page, dpi=72)`（106〜151 行）— ただし関数本体のうち pixmap→gray 変換（112〜116 行）の後は `pixel_x, is_reliable = _find_gutter_in_gray(gray)` を呼ぶ形に差し替え、末尾の pt 座標変換（150〜151 行）だけ残す
- `_insert_cropped()`（154〜170 行）

最後に PIL 画像レベル API（旧 engine 版 `SpreadSplitter` クラスの互換）を追加する:

```python
# ─── PIL 画像レベル API（pdf_ingester の VLM ルートが使用） ───

class SpreadSplitter:
    """PIL 画像に対する見開き判定・分割（共有コアへの薄いラッパー）。"""

    @staticmethod
    def is_spread(img: Image.Image) -> bool:
        width, height = img.size
        return (width / height) > SPREAD_ASPECT_THRESHOLD

    @staticmethod
    def split_spread_ltr(img: Image.Image) -> List[Image.Image]:
        width, height = img.size
        gray = np.array(img.convert("L")).astype(float)
        split_x, is_reliable = _find_gutter_in_gray(gray)
        if not is_reliable:
            split_x = width // 2
        return [img.crop((0, 0, split_x, height)), img.crop((split_x, 0, width, height))]
```

- [ ] **Step 2: 呼び出し元の import を更新する**

`core/book_manager.py:144` を:

```python
        from .spread_splitter import is_spread_pdf, split_spread_pdf
```

から

```python
        from .engine.p1_ingest.spread_splitter import is_spread_pdf, split_spread_pdf
```

に変更。`core/pdf_ingester.py:115` の `from .engine.p1_ingest.spread_splitter import SpreadSplitter` は変更不要。

- [ ] **Step 3: テストの import とパッチパスを更新する**

`tests/unit/test_spread_splitter.py` 内の `core.spread_splitter` を一括で `core.engine.p1_ingest.spread_splitter` に置換する（import 文 15 行目、`patch("core.spread_splitter._find_gutter_x", ...)` の 173・192 行目）。

- [ ] **Step 4: 旧ファイルを削除してテストを実行する**

```bash
git rm core/spread_splitter.py
python3 -m pytest tests/unit/test_spread_splitter.py -v   # Expected: 全件 PASS
python3 -m pytest tests/unit/ -q                          # Expected: 180 passed
```

- [ ] **Step 5: コミット**

```bash
git add -A && git commit -m "refactor: spread_splitter 二系統をノド検出コア共有の単一モジュールに統合"
```

---

# フェーズ C: Phase 3 ファサード解体（最大の作業・アーキテクチャ整合）

`core/phase3_structure.py`（1360 行）を CLAUDE.md の責務境界どおり「`run_phase3` オーケストレーションのみ」に縮小し、アルゴリズムを `engine/p3_structure/` に移す。**各タスクは関数の verbatim 移設 + import 差し替えのみ**で、ロジックは 1 文字も変えない。

移設マップ（行番号は 2026-06-10 時点。実行時に `grep -n "^def " core/phase3_structure.py` で再確認すること）:

| 現関数（行） | 移設先 |
|---|---|
| `normalize_heading`(686), `is_excluded_heading`(705), `match_heading`(715), `extract_headings_from_resume`(1316) | `engine/p3_structure/heading_matcher.py`（新規） |
| `structure_nodes_by_headings`(767), `build_tree`(964), `structure_nodes_by_markdown`(1050) | `engine/p3_structure/tree_builder.py`（新規） |
| `_should_join_lines`(32), `_matches_toc_entry`(61), `extract_toc_via_llm`(90), `extract_toc_from_chunks`(163), `apply_toc_titles`(228) | `engine/p3_structure/toc_extractor.py`（新規） |
| `detect_chapter_font_sizes`(302), `extract_book_chapters`(400), 定数 `STOP_SECTIONS`(84) | `engine/p3_structure/chapter_extractor.py`（新規） |
| `run_phase3`(1173) | `phase3_structure.py` に残す（ファサード） |

## Task C1: heading_matcher.py の切り出し

**Files:**
- Create: `core/engine/p3_structure/heading_matcher.py`
- Modify: `core/phase3_structure.py`（4 関数を削除し import に置換）
- Modify: `tests/unit/test_phase3_structure.py`（import 先変更）

- [ ] **Step 1: 各関数が参照するモジュールレベル名を洗い出す**

```bash
sed -n '686,760p;1316,1360p' core/phase3_structure.py | grep -oE '\b(re|List|Optional|tuple)\b' | sort -u
```

Expected: `re`, `List`, `Optional`, `tuple` 程度（外部依存なしの純関数群）。想定外の名前（プロンプトや LLM 呼び出し）が出たら移設前に依存も移設マップに追加する。

- [ ] **Step 2: `core/engine/p3_structure/heading_matcher.py` を作成する**

ファイル先頭:

```python
"""
見出しの正規化・除外判定・決定論的マッチング。
Phase 3 のツリー構築とレジュメ逆引きの基盤となる純関数群。
"""

import re
from typing import List, Optional
```

続けて `phase3_structure.py` から `normalize_heading`・`is_excluded_heading`・`match_heading`・`extract_headings_from_resume` の 4 関数を**本体無改変で**カット＆ペーストする。

- [ ] **Step 3: `phase3_structure.py` 側を import に置き換える**

削除した 4 関数の位置に何も残さず、ファイル冒頭の import 群に追加:

```python
from .engine.p3_structure.heading_matcher import (
    normalize_heading,
    is_excluded_heading,
    match_heading,
    extract_headings_from_resume,
)
```

（ファサード内の他関数がこれらを呼んでいるため、import で同名を束縛すれば呼び出し側は無変更で動く。）

- [ ] **Step 4: テストの import を新モジュールに変更する**

`tests/unit/test_phase3_structure.py` の 12〜17 行を:

```python
from core.engine.p3_structure.heading_matcher import (
    is_excluded_heading,
    normalize_heading,
    match_heading,
)
from core.phase3_structure import structure_nodes_by_headings
```

に変更。

- [ ] **Step 5: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 180 passed
git add -A && git commit -m "refactor: 見出しマッチング4関数を engine/p3_structure/heading_matcher へ移設"
```

## Task C2: tree_builder.py の切り出し

**Files:**
- Create: `core/engine/p3_structure/tree_builder.py`
- Modify: `core/phase3_structure.py`
- Modify: `tests/unit/test_phase3_structure.py`

- [ ] **Step 1: 3 関数の依存を洗い出す**

```bash
sed -n '767,1172p' core/phase3_structure.py | grep -oE '^\s*(from|import)|normalize_heading|match_heading|is_excluded_heading|TreeNode|RawChunk|print_log' | sort -u
```

Expected: `TreeNode`／`print_log`／heading_matcher の関数群。これ以外（LLM 呼び出し等）が出たら移設マップを修正する。

- [ ] **Step 2: `core/engine/p3_structure/tree_builder.py` を作成する**

ファイル先頭:

```python
"""
フラットなノード列を見出し情報に基づいて階層 TreeNode ツリーへ再構築するエンジン。
"""

import re
from typing import Dict, List, Optional, Tuple

from core.config import print_log
from core.models import TreeNode
from .heading_matcher import normalize_heading, is_excluded_heading, match_heading
```

続けて `structure_nodes_by_headings`（内部関数 `_collect_p` を含む）・`build_tree`・`structure_nodes_by_markdown`（内部関数 `is_valid_chapter` を含む）を**本体無改変で**移設する。Step 1 で判明した import を過不足なく合わせる。

- [ ] **Step 3: `phase3_structure.py` の import に追加し、移設済み関数を削除する**

```python
from .engine.p3_structure.tree_builder import (
    structure_nodes_by_headings,
    build_tree,
    structure_nodes_by_markdown,
)
```

- [ ] **Step 4: テストの import を更新する**

`tests/unit/test_phase3_structure.py` の `from core.phase3_structure import structure_nodes_by_headings` を `from core.engine.p3_structure.tree_builder import structure_nodes_by_headings` に変更。

- [ ] **Step 5: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 180 passed
git add -A && git commit -m "refactor: ツリー構築3関数を engine/p3_structure/tree_builder へ移設"
```

## Task C3: toc_extractor.py の切り出し

**Files:**
- Create: `core/engine/p3_structure/toc_extractor.py`
- Modify: `core/phase3_structure.py`

- [ ] **Step 1: 5 関数（32〜301 行）の依存を洗い出す**

```bash
sed -n '1,30p' core/phase3_structure.py   # 現行 import 一覧を確認
sed -n '32,301p' core/phase3_structure.py | grep -nE 'call_gemini|_get_prompts|fitz|json|print_log|RawChunk|normalize_heading|_SENTENCE_END_RE|_TRAILING_WORDS'
```

`extract_toc_via_llm` は LLM 呼び出し（`call_gemini`）と TOC プロンプトを使う。検出された依存をすべて新ファイルの import に含める。

- [ ] **Step 2: `core/engine/p3_structure/toc_extractor.py` を作成する**

ファイル先頭（Step 1 の結果で過不足を調整）:

```python
"""
書籍 PDF の目次（TOC）抽出とタイトル適用。
LLM による目次ページ解析と、チャンク列からの決定論的 TOC 復元の両方を提供する。
"""

import json
import re
from pathlib import Path
from typing import Any, List, Optional

import fitz

from core.config import print_log
from core.llm_client import call_gemini
from core.models import RawChunk
from .heading_matcher import normalize_heading
```

続けて `_should_join_lines`・`_matches_toc_entry`・`extract_toc_via_llm`・`extract_toc_from_chunks`・`apply_toc_titles` を**本体無改変で**移設する。

- [ ] **Step 3: `phase3_structure.py` の import に追加する**

```python
from .engine.p3_structure.toc_extractor import (
    extract_toc_via_llm,
    extract_toc_from_chunks,
    apply_toc_titles,
)
```

（`_should_join_lines`・`_matches_toc_entry` はプライベートヘルパーなのでファサードに再 export しない。ファサード内の他関数が直接呼んでいないことを `grep -n "_should_join_lines\|_matches_toc_entry" core/phase3_structure.py` で確認する。）

- [ ] **Step 4: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 180 passed
git add -A && git commit -m "refactor: TOC 抽出系5関数を engine/p3_structure/toc_extractor へ移設"
```

## Task C4: chapter_extractor.py の切り出しと chapter_parser の依存逆転解消

**Files:**
- Create: `core/engine/p3_structure/chapter_extractor.py`
- Modify: `core/phase3_structure.py`
- Modify: `core/engine/p3_structure/chapter_parser.py:37`

- [ ] **Step 1: `detect_chapter_font_sizes`・`extract_book_chapters`（302〜685 行）と定数 `STOP_SECTIONS`（84 行）の依存を洗い出す**

```bash
sed -n '302,685p' core/phase3_structure.py | grep -nE 'fitz|json|print_log|call_gemini|normalize_heading|STOP_SECTIONS|RawChunk|ChapterBoundary|_SENTENCE_END_RE|_TRAILING_WORDS' | head -30
```

- [ ] **Step 2: `core/engine/p3_structure/chapter_extractor.py` を作成する**

ファイル先頭（Step 1 の結果で調整。`_SENTENCE_END_RE`・`_TRAILING_WORDS` を使う場合は `core.text_utils` から import）:

```python
"""
書籍 PDF の章境界抽出。フォントサイズ統計と TOC 照合により
章タイトル・開始ページを決定する。
"""

import re
from typing import Any, Dict, List, Optional

import fitz

from core.config import print_log
from core.models import RawChunk
from core.text_utils import _SENTENCE_END_RE, _TRAILING_WORDS
from .heading_matcher import normalize_heading

STOP_SECTIONS = {
    # phase3_structure.py:84 の定義を verbatim 移設
}
```

続けて `detect_chapter_font_sizes`・`extract_book_chapters` を**本体無改変で**移設する。

- [ ] **Step 3: `phase3_structure.py` の import に追加する**

```python
from .engine.p3_structure.chapter_extractor import (
    detect_chapter_font_sizes,
    extract_book_chapters,
)
```

- [ ] **Step 4: chapter_parser.py の逆 import を解消する**

`core/engine/p3_structure/chapter_parser.py:37` を:

```python
        from core.phase3_structure import extract_book_chapters, apply_toc_titles
```

から

```python
        from .chapter_extractor import extract_book_chapters
        from .toc_extractor import apply_toc_titles
```

に変更（エンジン層→ファサードの依存逆転を解消）。

- [ ] **Step 5: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 180 passed
git add -A && git commit -m "refactor: 章境界抽出を engine/p3_structure/chapter_extractor へ移設し chapter_parser の依存逆転を解消"
```

## Task C5: ファサードの最終整理と移設後テストの復活

**Files:**
- Modify: `core/phase3_structure.py`（`run_phase3` と import のみの状態に整理）
- Create: `tests/unit/test_tree_builder.py`（Task A1 で削除した test_heading_detector の検証観点を本番実体で復活）

- [ ] **Step 1: ファサードの残存内容を確認する**

```bash
grep -n "^def \|^class \|^STOP_SECTIONS" core/phase3_structure.py
wc -l core/phase3_structure.py
```

Expected: `def run_phase3` のみ・200 行以下。それ以外の関数が残っていたら C1〜C4 の漏れなので該当タスクの方式で移設する。

- [ ] **Step 2: 復活テストを書く**

`tests/unit/test_tree_builder.py` を作成。旧 test_heading_detector.py がカバーしていた「正規化」「番号剥離」観点を、本番実体である `heading_matcher.normalize_heading` に対して書く:

```python
"""heading_matcher / tree_builder の移設後検証。"""
from core.engine.p3_structure.heading_matcher import normalize_heading


def test_normalize_strips_chapter_numbering():
    assert normalize_heading("1. Introduction") == "introduction"
    assert normalize_heading("Chapter 3: Methods") == "methods"
    assert normalize_heading("III. Comparisons") == "comparisons"


def test_normalize_keeps_numeric_only_title():
    # 数字だけのタイトルはフォールバックで残す
    assert normalize_heading("3.1") != ""


def test_normalize_case_and_symbols():
    assert normalize_heading("RESULTS & Discussion!") == "results discussion"
```

- [ ] **Step 3: テストを実行する**

```bash
python3 -m pytest tests/unit/test_tree_builder.py -v   # Expected: 3 passed
python3 -m pytest tests/unit/ -q                       # Expected: 183 passed
```

※ アサーション値が現実装と食い違って FAIL した場合は、**実装を変えず**、実際の戻り値を確認してテスト側を実挙動に合わせる（このフェーズは挙動固定が目的）。

- [ ] **Step 4: ゴールデン検証（E2E 回帰確認）**

```bash
python3 main.py data/input/paperplain/NST/*.txt --lite
```

実行後、`state/<session_id>/phase3_structure.json` のセクション分割が `data/input/paperplain/NST/` の理想出力と整合するか目視確認する（`.cursor/skills/golden-verification` の手順に従う）。API コストが発生するため、実行可否はユーザーに確認する。

- [ ] **Step 5: コミット**

```bash
git add -A && git commit -m "refactor: phase3_structure をオーケストレーション専任ファサードに縮小し移設後テストを追加"
```

---

# フェーズ D: Phase 1 配置整理と v3 命名の刷新

## Task D1: pdf_ingester / pdf_splitter を engine/p1_ingest へ移動

**Files:**
- Move: `core/pdf_ingester.py` → `core/engine/p1_ingest/pdf_ingester.py`
- Move: `core/pdf_splitter.py` → `core/engine/p1_ingest/pdf_splitter.py`
- Modify: `core/phase1_preprocessor.py:17`, `core/pipeline.py:74`, `core/book_manager.py:10,100`
- Modify: `tests/unit/test_pdf_splitter.py:17,21`, `tests/unit/test_book_manager.py:22`

- [ ] **Step 1: 移動する**

```bash
git mv core/pdf_ingester.py core/engine/p1_ingest/pdf_ingester.py
git mv core/pdf_splitter.py core/engine/p1_ingest/pdf_splitter.py
```

- [ ] **Step 2: 移動したファイル内の相対 import を修正する**

両ファイル内の `from .config import ...` → `from core.config import ...`、`from .llm_client import ...` → `from core.llm_client import ...`、`from .models import ...` → `from core.models import ...`、`from .engine.p1_ingest.X import ...` → `from .X import ...` をすべて置換する。漏れ検出:

```bash
grep -n "^from \.\|^    from \.\|        from \." core/engine/p1_ingest/pdf_ingester.py core/engine/p1_ingest/pdf_splitter.py
```

各行を目視し、`from .ocr_manager` 等の同階層参照だけが残っている状態にする。

- [ ] **Step 3: 呼び出し元の import を更新する**

- `core/phase1_preprocessor.py:17`: `from .pdf_ingester import run_pdf_ingestion_v3` → `from .engine.p1_ingest.pdf_ingester import run_pdf_ingestion_v3`
- `core/pipeline.py:74`: `from .pdf_ingester import diagnose_pdf_quality` → `from .engine.p1_ingest.pdf_ingester import diagnose_pdf_quality`
- `core/book_manager.py:10`: `from .pdf_splitter import PDFSplitter` → `from .engine.p1_ingest.pdf_splitter import PDFSplitter`
- `core/book_manager.py:100`: `from .pdf_ingester import diagnose_pdf_quality` → `from .engine.p1_ingest.pdf_ingester import diagnose_pdf_quality`
- `tests/unit/test_pdf_splitter.py`: `core.pdf_splitter` → `core.engine.p1_ingest.pdf_splitter`（import と patch パス）
- `tests/unit/test_book_manager.py:22`: `patch("core.book_manager.PDFSplitter")` は book_manager 名前空間へのパッチなので**変更不要**（確認のみ）

- [ ] **Step 4: 取り残し参照がないか全体を確認する**

```bash
grep -rn "core\.pdf_ingester\|core\.pdf_splitter\|from \.pdf_ingester\|from \.pdf_splitter" \
  --include="*.py" core/ main.py server.py tests/ scripts/
```

Expected: 出力なし。

- [ ] **Step 5: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 183 passed
git add -A && git commit -m "refactor: pdf_ingester / pdf_splitter を engine/p1_ingest 配下へ移動"
```

## Task D2: v3 命名の除去

**Files:**
- Modify: `core/engine/p1_ingest/pdf_ingester.py`（関数名 2 件）
- Modify: `core/engine/p1_ingest/ocr_manager.py`（`process_page_vlm_v3` → `process_page_vlm`）
- Modify: `core/phase1_preprocessor.py`（呼び出し名）

旧 v2 ルートは Task A2 で削除済みのため、`_v3` サフィックスは情報を持たない。

- [ ] **Step 1: リネーム対象の全参照を列挙する**

```bash
grep -rn "run_pdf_ingestion_v3\|process_page_vlm_v3\|V3\b" --include="*.py" core/ main.py server.py tests/ scripts/
```

- [ ] **Step 2: 一括リネームする**

- `run_pdf_ingestion_v3_async` → `run_pdf_ingestion_async`
- `run_pdf_ingestion_v3` → `run_pdf_ingestion`
- `process_page_vlm_v3` → `process_page_vlm`（`ocr_manager.py` の定義と `pdf_ingester.py` の呼び出し）
- docstring・ログ文字列内の「V3」表記（`pdf_ingester.py:112,126` 等）も除去

Step 1 で列挙した全行を漏れなく更新し、再度同じ grep で残存ゼロを確認する。

- [ ] **Step 3: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 183 passed
git add -A && git commit -m "refactor: 旧ルート削除に伴い v3 サフィックスを除去"
```

## Task D3: core/base 解消と meta_analyzer の配置正規化

**Files:**
- Move: `core/base/exceptions.py` → `core/exceptions.py`（`core/base/` ディレクトリ削除）
- Move: `core/engine/meta_analyzer.py` → `core/engine/p2_meta/meta_analyzer.py`（`__init__.py` 新規）
- Modify: `core/engine/p2_meta/meta_analyzer.py:11`, `core/engine/p1_ingest/formatter.py:9`, `core/phase2_meta.py:13`, `tests/unit/test_meta_analyzer.py`

- [ ] **Step 1: core/base の利用箇所を確認する**

```bash
grep -rn "core\.base\|from \.\.base\|from \.base" --include="*.py" core/ main.py server.py tests/
```

Expected: `core/engine/meta_analyzer.py:11` と `core/engine/p1_ingest/formatter.py:9` の 2 件のみ。他にあれば Step 3 で同様に更新する。

- [ ] **Step 2: 移動する**

```bash
git mv core/base/exceptions.py core/exceptions.py
rmdir core/base 2>/dev/null || git rm core/base/__init__.py && rmdir core/base
mkdir -p core/engine/p2_meta && touch core/engine/p2_meta/__init__.py
git mv core/engine/meta_analyzer.py core/engine/p2_meta/meta_analyzer.py
git add core/engine/p2_meta/__init__.py
```

- [ ] **Step 3: import を更新する**

- `core/engine/p2_meta/meta_analyzer.py:11`: `from ..base.exceptions import MetaExtractionError` → `from core.exceptions import MetaExtractionError`
- `core/engine/p1_ingest/formatter.py:9`: `from core.base.exceptions import PreprocessorError` → `from core.exceptions import PreprocessorError`
- `core/phase2_meta.py:13`: `from .engine.meta_analyzer import MetaAnalyzer` → `from .engine.p2_meta.meta_analyzer import MetaAnalyzer`
- `tests/unit/test_meta_analyzer.py`: `core.engine.meta_analyzer` → `core.engine.p2_meta.meta_analyzer`（import と patch パスの 3 箇所）

- [ ] **Step 4: テストを実行してコミット**

```bash
python3 -m pytest tests/unit/ -q   # Expected: 183 passed
git add -A && git commit -m "refactor: core/base を解消し meta_analyzer を engine/p2_meta へ配置"
```

---

# フェーズ E: web フロントエンドの重複統合

## Task E1: app.js / app_ronbun.js の共通関数を common.js へ抽出

**Files:**
- Create: `web/common.js`
- Modify: `web/app.js`, `web/app_ronbun.js`, `web/index.html`, `web/ronbun.html`

事前調査: 両ファイルの関数名集合の差分は `closeApiModal` / `handleModalEsc` / `handleOverlayClick` / `openApiModal` / `switchInputTab`（app.js のみ）のみで、残りは同名。

- [ ] **Step 1: 同名関数が同一実装かを関数ごとに diff で確認する**

```bash
diff web/app.js web/app_ronbun.js | head -80
```

**完全一致する関数のみ**を抽出対象とする。一致しない同名関数（エンドポイント URL やモード名が違う等）は各ファイルに残す。抽出対象リストをこのステップの出力としてメモする。

- [ ] **Step 2: `web/common.js` を作成し、抽出対象関数を verbatim 移動する**

両ファイルから対象関数を削除し、`web/common.js` に 1 部ずつ置く。ファイル先頭にコメント:

```javascript
// app.js / app_ronbun.js 共通のポーリング・ダウンロード・UI ヘルパー。
// ページ固有の差分（エンドポイント・モーダル等）は各 app ファイルに残す。
```

- [ ] **Step 3: HTML 2 ファイルで common.js を先に読み込む**

`web/index.html` の `<script src="app.js">` の直前、`web/ronbun.html` の `<script src="app_ronbun.js">` の直前に:

```html
<script src="common.js"></script>
```

- [ ] **Step 4: 動作確認（手動）**

```bash
python3 server.py
```

ブラウザで `http://localhost:7860/`（または server.py 設定のポート）の index と ronbun 両ページを開き、(1) ファイル選択 UI が反応する、(2) ブラウザコンソールに ReferenceError が出ない、ことを確認する。API キーなしでも UI 層の確認は可能。

- [ ] **Step 5: コミット**

```bash
git add web/ && git commit -m "refactor: web フロントの共通関数を common.js へ抽出"
```

---

# フェーズ F: ドキュメント・ルール同期と最終検証

## Task F1: CLAUDE.md のアーキテクチャ記述を実態に同期

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: エンジン層の表を更新する**

「エンジン層（`core/engine/`）」セクションの表を以下に置き換える:

```markdown
| サブパッケージ | 主要モジュール |
|---|---|
| `p1_ingest/` | `docling_ingester.py`（Docling ルート）, `pdf_ingester.py`, `pdf_splitter.py`, `spread_splitter.py`, `physical_ingester.py`, `ocr_manager.py`, `text_structure_extractor.py`, `formatter.py` |
| `p2_meta/` | `meta_analyzer.py` |
| `p3_structure/` | `heading_matcher.py`, `tree_builder.py`, `toc_extractor.py`, `chapter_extractor.py`, `chapter_parser.py`, `state_integrator.py` |
| `p4_translate/` | `parallel_translator.py`, `prompt_builder.py`, `tree_reconstructor.py` |
| `p5_export/` | `workflowy_engine.py`, `markdown_engine.py`, `text_book_integrator.py` |
```

- [ ] **Step 2: 本文中の旧名参照を更新する**

`grep -n "run_pdf_ingestion_v3\|tree_constructor\|heading_detector\|toc_manager\|layout_engine" CLAUDE.md` で残存参照を確認し、新名（`run_pdf_ingestion` 等）に置換、削除済みモジュールへの言及は除去する。`.cursor/rules/` 配下も同じ grep で確認して同期する（ルール正本は `.cursor/rules/`）。

- [ ] **Step 3: コミット**

```bash
git add CLAUDE.md .cursor/rules/ && git commit -m "docs: リファクタリング後のエンジン層構成に CLAUDE.md とルールを同期"
```

## Task F2: 変更管理ログへの追記と最終検証

**Files:**
- Modify: `docs/management/requirements_log.md`

- [ ] **Step 1: requirements_log.md に追記する**

```markdown
## 2026-06-XX 全体リファクタリング（挙動不変）

- デッドコード削除: p3_structure 孤児3モジュール / pdf_ingester 旧ルート / LayoutEngine / text_utils 旧関数
- spread_splitter 二系統をノド検出コア共有で統合（信頼性判定はレンジベース基準に一本化）
- phase3_structure(1360行) を run_phase3 専任ファサードに縮小、アルゴリズムは engine/p3_structure/ の heading_matcher / tree_builder / toc_extractor / chapter_extractor へ移設
- chapter_parser のファサード逆 import を解消
- pdf_ingester / pdf_splitter を engine/p1_ingest へ移動、v3 命名除去、core/base 解消、meta_analyzer を p2_meta へ
- web フロント共通関数を common.js へ抽出
- 根拠: CLAUDE.md 責務境界（ファサード=オーケストレーション専任）との乖離解消
```

（XX は実施日に置換。）

- [ ] **Step 2: 最終全テスト**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: **183 passed**（ベースライン 184 − 孤児テスト 4 + 復活テスト 3）

- [ ] **Step 3: E2E ゴールデン検証（ユーザー確認のうえ実施）**

```bash
python3 main.py data/input/paperplain/NST/*.txt --lite   # テキストルート
python3 main.py data/input/paperpdf/AL/*.pdf --lite      # PDF ルート
```

出力 `_p2.md` を各ディレクトリの理想出力と比較し、セクション構造（見出し階層・[Unlabeled Section]・References 除外・Appendix 保持）が変わっていないことを確認する。API コストが発生するため実行前にユーザーへ確認する。

- [ ] **Step 4: コミット**

```bash
git add docs/management/requirements_log.md && git commit -m "docs: 全体リファクタリングの変更記録を追記"
```

---

## スコープ外（このプランでは扱わない）

- `llm_client.py`（614 行）の分割: 多責務だが安定稼働中で、TierManager のシングルトン挙動はテストが依存している。挙動不変の確証が持てないため別プランとする。
- `server.py` / `main.py` のエントリーポイント共通化: 重複は引数の組み立て程度で、統合の利益が小さい（YAGNI）。
- プロンプト内容・モデルルーティングの変更: リファクタリングの範囲外（挙動が変わる）。
- 多言語ソース対応: `docs/superpowers/specs/2026-05-28-` の別構想（延期中）。

## リスクと中断ポイント

- 各フェーズは独立しており、どのフェーズ末でも中断可能。フェーズ内のタスクも 1 コミット単位で revert 可能。
- 最リスクはフェーズ C（1000 行超の移設）。verbatim 移設 + import 束縛方式により diff レビューで機械的に検証できる。`git diff --color-moved=dimmed-zebra` を使うと移動と変更を区別しやすい。
- フェーズ C で行番号がこのプランとずれていた場合（プラン作成後にコードが変わった場合）、`grep -n "^def " core/phase3_structure.py` の現在値を正とする。
