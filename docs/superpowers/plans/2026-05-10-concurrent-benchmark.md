# concurrent_sections ベンチマーク実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `concurrent_sections` の最適値を実測で確定し、ドキュメントの根拠を一点観測の経験則から実データに置き換える。

**Architecture:** dead semaphore を除去して並列数の制御を一本化 → `--concurrent N` フラグを main.py から ParallelTranslator まで通す → ベンチマークスクリプトで AL テキスト論文を concurrent=1/2/4/8 × 2回実行 → TTFT ログを集計してドキュメントを更新する。

**Tech Stack:** Python asyncio, aiolimiter, google-genai SDK, pytest, argparse

---

## 背景・前提知識

- Phase 4 の並列数は `ParallelTranslator(max_concurrent_sections=4)` のデフォルト値として固定されており、CLI から変更できない
- `apply_tier_settings()` が返す `Semaphore` は `ParallelTranslator` に完全に無視されている（dead code）
- 実測 avg TTFT は 29.6s（従来ドキュメントの「240s問題」は 2026-04-04 の一時的サーバー混雑 × Phase 1 分割バグの複合）
- 実験対象: `data/input/paperplain/AL/Arbitrarysample.txt`（18 ページ相当、\n 分割で 124 チャンク、9 セクション）

---

## ファイル変更マップ

| ファイル | 変更種別 | 内容 |
|---|---|---|
| `core/llm_client.py` | 修正 | `apply_tier_settings` の返り値から `Semaphore` を除去 |
| `core/engine/p4_translate/parallel_translator.py` | 修正 | アンパック文を 2 箇所更新 |
| `main.py` | 修正 | `--concurrent N` 引数を追加 |
| `core/pipeline.py` | 修正 | `max_concurrent_sections` を `run_phase4` に渡す |
| `core/phase4_translate.py` | 修正 | `_run_phase4_async` に `max_concurrent_sections` 追加、`ParallelTranslator` に渡す |
| `scripts/benchmark_concurrent.py` | 新規作成 | ベンチマーク実行スクリプト |
| `tests/unit/test_concurrent_flag.py` | 新規作成 | `--concurrent` が ParallelTranslator まで届くことを確認するテスト |
| `docs/model_optimization.md` | 修正 | 実験結果に基づき Section 3・6 を更新 |

---

## Task 1: dead semaphore を除去する

**Files:**
- Modify: `core/llm_client.py`（`apply_tier_settings` 関数）
- Modify: `core/engine/p4_translate/parallel_translator.py`（`__init__` と `translate_section_chunks`）

- [ ] **Step 1: `apply_tier_settings` の返り値から Semaphore を除去する**

`core/llm_client.py` の `apply_tier_settings` 関数（590 行付近）を以下のように変更する。

変更前:
```python
def apply_tier_settings(tier: str | GeminiTier) -> Tuple[AsyncLimiter, asyncio.Semaphore, dict]:
    ...
    with _LIMITER_LOCK:
        if tier == GeminiTier.FREE:
            settings = {"max_batch_chunks": 5, "max_batch_chars": 6000}
            semaphore_size = 1
            if tier not in _CACHED_LIMITERS:
                _CACHED_LIMITERS[tier] = AsyncLimiter(1, 4.0)  # 1 request per 4 seconds
        else:
            settings = {"max_batch_chunks": 10, "max_batch_chars": 11000}
            semaphore_size = 2
            if tier not in _CACHED_LIMITERS:
                _CACHED_LIMITERS[tier] = AsyncLimiter(100, 60.0)  # 100 requests per minute

        rate_limiter = _CACHED_LIMITERS[tier]
        # Semaphore は毎回新規作成（イベントループ依存のためキャッシュ不可）
        semaphore = asyncio.Semaphore(semaphore_size)

    return rate_limiter, semaphore, settings
```

変更後:
```python
def apply_tier_settings(tier: str | GeminiTier) -> Tuple[AsyncLimiter, dict]:
    ...
    with _LIMITER_LOCK:
        if tier == GeminiTier.FREE:
            settings = {"max_batch_chunks": 5, "max_batch_chars": 6000}
            if tier not in _CACHED_LIMITERS:
                _CACHED_LIMITERS[tier] = AsyncLimiter(1, 4.0)  # 1 request per 4 seconds
        else:
            settings = {"max_batch_chunks": 10, "max_batch_chars": 11000}
            if tier not in _CACHED_LIMITERS:
                _CACHED_LIMITERS[tier] = AsyncLimiter(100, 60.0)  # 100 requests per minute

        rate_limiter = _CACHED_LIMITERS[tier]

    return rate_limiter, settings
```

型ヒントの import 行 (`Tuple` を使用) は既存のものをそのまま使う。

- [ ] **Step 2: `parallel_translator.py` のアンパック文を 2 箇所修正する**

`core/engine/p4_translate/parallel_translator.py` で `apply_tier_settings` を呼んでいる箇所を変更する。

`__init__` メソッド（30 行付近）:
```python
# 変更前
self.rate_limiter, _, self.settings = apply_tier_settings(tier)
# 変更後
self.rate_limiter, self.settings = apply_tier_settings(tier)
```

`translate_section_chunks` メソッド内（ティア動的変更部分、68 行付近）:
```python
# 変更前
self.rate_limiter, _, self.settings = apply_tier_settings(self.tier)
# 変更後
self.rate_limiter, self.settings = apply_tier_settings(self.tier)
```

- [ ] **Step 3: 既存テストが通ることを確認する**

```bash
cd /Users/shufujita/Code/p2workflowy
source venv/bin/activate
python3 -m pytest tests/unit/ -v -x 2>&1 | tail -20
```

Expected: 全テスト PASS（既存テストが壊れていないことを確認）

- [ ] **Step 4: Commit**

```bash
git add core/llm_client.py core/engine/p4_translate/parallel_translator.py
git commit -m "refactor: remove unused Semaphore from apply_tier_settings return value"
```

---

## Task 2: `--concurrent N` フラグを CLI から ParallelTranslator まで通す

**Files:**
- Create: `tests/unit/test_concurrent_flag.py`
- Modify: `core/phase4_translate.py`
- Modify: `core/pipeline.py`
- Modify: `main.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_concurrent_flag.py` を新規作成する:

```python
"""concurrent_sections が ParallelTranslator まで届くことを確認するテスト。"""
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from core.engine.p4_translate.parallel_translator import ParallelTranslator


def test_parallel_translator_accepts_concurrent_param():
    """ParallelTranslator がカスタム concurrent_sections を受け取ること。"""
    translator = ParallelTranslator(max_concurrent_sections=2)
    assert translator.semaphore._value == 2


def test_parallel_translator_default_concurrent():
    """デフォルト値が 4 のままであること（後で実験結果に応じて変わる可能性あり）。"""
    translator = ParallelTranslator()
    assert translator.semaphore._value == 4


@patch("core.phase4_translate.ParallelTranslator")
def test_run_phase4_passes_concurrent_to_translator(mock_translator_cls):
    """run_phase4 の max_concurrent_sections が ParallelTranslator に渡されること。"""
    from core.phase4_translate import _run_phase4_async
    import asyncio, json
    from pathlib import Path
    import tempfile

    # 最小限のフェイクstate ファイルを用意
    with tempfile.TemporaryDirectory() as tmpdir:
        sections_path = Path(tmpdir) / "sections.json"
        structure_path = Path(tmpdir) / "structure.json"
        phase2_path = Path(tmpdir) / "phase2.json"
        phase4_path = Path(tmpdir) / "phase4.json"

        sections_path.write_text(json.dumps({}))
        structure_path.write_text(json.dumps([]))
        phase2_path.write_text(json.dumps({"resume_content": "", "keywords_data": []}))

        mock_translator_cls.return_value = MagicMock()
        mock_translator_cls.return_value.translate_section_chunks = AsyncMock(return_value=[])

        asyncio.run(_run_phase4_async(
            phase2_state_path=phase2_path,
            structure_state_path=structure_path,
            sections_state_path=sections_path,
            phase4_state_path=phase4_path,
            glossary_path=None,
            api_key="dummy",
            max_concurrent_sections=2,
        ))

        mock_translator_cls.assert_called_once()
        call_kwargs = mock_translator_cls.call_args.kwargs
        assert call_kwargs.get("max_concurrent_sections") == 2
```

- [ ] **Step 2: テストが失敗することを確認する**

```bash
python3 -m pytest tests/unit/test_concurrent_flag.py -v
```

Expected: `test_run_phase4_passes_concurrent_to_translator` が FAIL（`_run_phase4_async` に `max_concurrent_sections` 引数がないため）

- [ ] **Step 3: `_run_phase4_async` に引数を追加し、ParallelTranslator に渡す**

`core/phase4_translate.py` の `_run_phase4_async` 関数シグネチャに追加:

```python
async def _run_phase4_async(
    phase2_state_path: str | Path,
    structure_state_path: str | Path,
    sections_state_path: str | Path,
    phase4_state_path: str | Path,
    glossary_path: str | None,
    api_key: str | None,
    save_state: bool = True,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
    tier: str = "paid",
    resume_only: bool = False,
    is_book: bool = False,
    pdf_mode: str = "default",
    max_concurrent_sections: int = 4,   # ← 追加
) -> List[TreeNode]:
```

同じファイル内の `ParallelTranslator` インスタンス生成箇所（107 行付近）を変更:

```python
# 変更前
translator = ParallelTranslator(api_key=api_key, model=model, tier=current_tier)
# 変更後
translator = ParallelTranslator(api_key=api_key, model=model, tier=current_tier,
                                 max_concurrent_sections=max_concurrent_sections)
```

`run_phase4`（同期ラッパー）も `**kwargs` で受け取っているので変更不要。

- [ ] **Step 4: `pipeline.py` で `max_concurrent_sections` を `run_phase4` に渡す**

`core/pipeline.py` の `run_pipeline` 関数シグネチャに追加:

```python
def run_pipeline(
    input_path: str | Path,
    api_key: str | None = None,
    ...
    resume_only: bool = False,
    resume_content: Optional[str] = None,
    max_concurrent_sections: int = 4,   # ← 追加
) -> list[str]:
```

`run_phase4` 呼び出し箇所（174 行付近）に追加:

```python
japanese_tree = run_phase4(
    phase2_state_path=state.phase2_meta,
    structure_state_path=state.phase3_structure,
    sections_state_path=state.phase3_sections,
    phase4_state_path=state.phase4_translate,
    glossary_path=glossary_path,
    api_key=api_key,
    expertise=expertise,
    model=model,
    thinking_level=thinking_level,
    state=state,
    tier=tier,
    resume_only=resume_only,
    is_book=is_book,
    pdf_mode=pdf_mode,
    max_concurrent_sections=max_concurrent_sections,  # ← 追加
)
```

- [ ] **Step 5: `main.py` に `--concurrent` 引数を追加する**

`main.py` の argparse セクションに追加（`--lite` などが定義されている近く）:

```python
parser.add_argument(
    '--concurrent', type=int, default=4,
    help='Phase 4 の並列セクション数（デフォルト: 4）'
)
```

`run_pipeline` 呼び出し箇所に追加:

```python
run_pipeline(
    ...,
    max_concurrent_sections=args.concurrent,
)
```

- [ ] **Step 6: テストが通ることを確認する**

```bash
python3 -m pytest tests/unit/test_concurrent_flag.py -v
```

Expected: 3 テスト全て PASS

- [ ] **Step 7: 既存テストも通ることを確認する**

```bash
python3 -m pytest tests/unit/ -v -x 2>&1 | tail -20
```

Expected: 全テスト PASS

- [ ] **Step 8: Commit**

```bash
git add core/phase4_translate.py core/pipeline.py main.py tests/unit/test_concurrent_flag.py
git commit -m "feat: add --concurrent N flag to control Phase 4 parallel sections"
```

---

## Task 3: ベンチマークスクリプトを作成する

**Files:**
- Create: `scripts/benchmark_concurrent.py`

- [ ] **Step 1: スクリプトを作成する**

`scripts/benchmark_concurrent.py` を新規作成する:

```python
#!/usr/bin/env python3
"""
Phase 4 concurrent_sections ベンチマーク
concurrent = 1, 2, 4, 8 を各 2 回実行し、TTFT 分布と総時間を比較する。

使い方:
  1. Phase 1-3 を一度実行して SESSION_ID を取得する:
     python3 main.py data/input/paperplain/AL/Arbitrarysample.txt
     → 出力された session_id を SESSION_ID に貼る

  2. このスクリプトを実行する:
     python3 scripts/benchmark_concurrent.py
"""
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 設定（実行前に SESSION_ID を設定すること） ──────────────────────────────
SESSION_ID = "FILL_IN_AFTER_PHASE1_3_RUN"   # 例: "20260510_123456"
INPUT_FILE = "data/input/paperplain/AL/Arbitrarysample.txt"
RESULTS_DIR = Path("data/benchmark_results")
GLOBAL_METRICS_CSV = Path("state/ttft_metrics.csv")

# 実験条件（ランダム順: 時間帯バイアスを分散させる）
CONDITIONS = [4, 1, 8, 2, 1, 8, 4, 2]
# ──────────────────────────────────────────────────────────────────────────


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def read_csv_rows_after(path: Path, start_row: int) -> list[dict]:
    """start_row 行目以降の新しい行を読む（ヘッダー除く）。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= start_row - 1:  # ヘッダー分を引く
                rows.append(row)
    return rows


def clear_phase4_state(session_id: str):
    phase4_json = Path("state") / session_id / "phase4_translate.json"
    if phase4_json.exists():
        phase4_json.unlink()
        print(f"  [清掃] {phase4_json} を削除")


def run_one(concurrent: int, trial: int) -> dict:
    """1条件1回を実行し、結果を dict で返す。"""
    print(f"\n{'='*60}")
    print(f"[実行] concurrent={concurrent}, trial={trial}  ({datetime.now().strftime('%H:%M:%S')})")

    clear_phase4_state(SESSION_ID)
    before_rows = count_csv_rows(GLOBAL_METRICS_CSV)
    start_ts = datetime.now().isoformat()
    start = time.time()

    result = subprocess.run(
        [
            sys.executable, "main.py", INPUT_FILE,
            "--session", SESSION_ID,
            "--resume", "4",
            "--concurrent", str(concurrent),
        ],
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - start
    end_ts = datetime.now().isoformat()

    if result.returncode != 0:
        print(f"  [ERROR] returncode={result.returncode}")
        print(result.stderr[-2000:])
        return {"concurrent": concurrent, "trial": trial, "elapsed": elapsed, "error": True, "rows": []}

    # この実行分のメトリクス行を抽出
    new_rows = read_csv_rows_after(GLOBAL_METRICS_CSV, before_rows)
    phase4_rows = [r for r in new_rows if r.get("section") not in ("N/A", "") and r.get("section")]
    ttfts = [float(r["ttft"]) for r in phase4_rows if r.get("ttft")]

    summary = {
        "concurrent": concurrent,
        "trial": trial,
        "elapsed": elapsed,
        "batches": len(phase4_rows),
        "ttft_avg": sum(ttfts) / len(ttfts) if ttfts else 0,
        "ttft_max": max(ttfts) if ttfts else 0,
        "ttft_p90": sorted(ttfts)[int(len(ttfts) * 0.9)] if ttfts else 0,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "error": False,
        "rows": phase4_rows,
    }

    print(f"  完了: {elapsed:.1f}s | バッチ数={len(phase4_rows)} | avg_TTFT={summary['ttft_avg']:.1f}s | max_TTFT={summary['ttft_max']:.1f}s")
    return summary


def save_results(all_results: list[dict]):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # 条件ごとに CSV を保存
    for res in all_results:
        if res.get("error") or not res.get("rows"):
            continue
        out_path = RESULTS_DIR / f"c{res['concurrent']}_trial{res['trial']}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=res["rows"][0].keys())
            writer.writeheader()
            writer.writerows(res["rows"])

    # サマリー JSON
    summary_path = RESULTS_DIR / "summary.json"
    summary_data = [
        {k: v for k, v in r.items() if k != "rows"}
        for r in all_results
    ]
    summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False))
    print(f"\n結果を {RESULTS_DIR} に保存しました。")


def print_summary_table(all_results: list[dict]):
    print("\n" + "="*70)
    print("実験結果サマリー")
    print("="*70)
    print(f"{'concurrent':>12} {'trial':>6} {'総時間(s)':>10} {'avg_TTFT':>10} {'max_TTFT':>10} {'バッチ数':>8}")
    print("-"*70)
    for res in sorted(all_results, key=lambda x: (x["concurrent"], x["trial"])):
        if res.get("error"):
            print(f"{res['concurrent']:>12} {res['trial']:>6}  ERROR")
        else:
            print(f"{res['concurrent']:>12} {res['trial']:>6} {res['elapsed']:>10.1f} "
                  f"{res['ttft_avg']:>10.1f} {res['ttft_max']:>10.1f} {res['batches']:>8}")
    print("="*70)

    # concurrent ごとの平均
    print("\nconcurrent ごとの平均（trial 2回平均）:")
    from collections import defaultdict
    grouped = defaultdict(list)
    for res in all_results:
        if not res.get("error"):
            grouped[res["concurrent"]].append(res)
    for c in sorted(grouped.keys()):
        trials = grouped[c]
        avg_elapsed = sum(r["elapsed"] for r in trials) / len(trials)
        avg_ttft = sum(r["ttft_avg"] for r in trials) / len(trials)
        print(f"  concurrent={c}: 総時間平均={avg_elapsed:.1f}s, avg_TTFT平均={avg_ttft:.1f}s")


def main():
    if SESSION_ID == "FILL_IN_AFTER_PHASE1_3_RUN":
        print("エラー: SESSION_ID を設定してください。")
        print("  python3 main.py data/input/paperplain/AL/Arbitrarysample.txt")
        print("  → 出力された session_id を scripts/benchmark_concurrent.py の SESSION_ID に設定")
        sys.exit(1)

    phase3_check = Path("state") / SESSION_ID / "phase3_sections.json"
    if not phase3_check.exists():
        print(f"エラー: Phase 3 の状態ファイルが見つかりません: {phase3_check}")
        print("先に Phase 1-3 を実行してください。")
        sys.exit(1)

    print(f"SESSION_ID: {SESSION_ID}")
    print(f"実験条件: {CONDITIONS}")
    print(f"合計 {len(CONDITIONS)} 回の実行")

    all_results = []
    trial_counts: dict[int, int] = {}

    for concurrent in CONDITIONS:
        trial = trial_counts.get(concurrent, 1)
        trial_counts[concurrent] = trial + 1
        result = run_one(concurrent, trial)
        all_results.append(result)

    save_results(all_results)
    print_summary_table(all_results)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: スクリプトに実行権限を付与する**

```bash
chmod +x scripts/benchmark_concurrent.py
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark_concurrent.py
git commit -m "feat: add Phase 4 concurrent_sections benchmark script"
```

---

## Task 4: 実験前準備 — AL テキストの Phase 1-3 を実行する

**前提:** Task 1-3 の実装が完了していること。

- [ ] **Step 1: Phase 1-3 を実行して session_id を取得する**

```bash
cd /Users/shufujita/Code/p2workflowy
source venv/bin/activate
python3 main.py data/input/paperplain/AL/Arbitrarysample.txt
```

出力ログから session_id（例: `20260510_143022`）を確認する。

- [ ] **Step 2: Phase 3 まで完了していることを確認する**

```bash
ls state/<session_id>/
```

Expected: `phase1_preprocessor.json`, `phase2_meta.json`, `phase3_sections.json`, `phase3_structure.json` が存在する。

- [ ] **Step 3: `scripts/benchmark_concurrent.py` の SESSION_ID を設定する**

`scripts/benchmark_concurrent.py` の 24 行目を編集:

```python
SESSION_ID = "20260510_143022"  # 実際の値に変更
```

---

## Task 5: ベンチマーク実行

- [ ] **Step 1: 実験を実行する**

```bash
cd /Users/shufujita/Code/p2workflowy
source venv/bin/activate
python3 scripts/benchmark_concurrent.py
```

8 回の実行で 約 90〜150 分かかる（1 回あたり avg TTFT 30s × 11 バッチ / 4 並列 ≈ 10〜15 分）。

- [ ] **Step 2: 結果ファイルを確認する**

```bash
ls data/benchmark_results/
cat data/benchmark_results/summary.json
```

Expected: `c1_trial1.csv`, `c1_trial2.csv`, `c2_trial1.csv`, `c2_trial2.csv`, `c4_trial1.csv`, `c4_trial2.csv`, `c8_trial1.csv`, `c8_trial2.csv`, `summary.json` が生成されている。

---

## Task 6: 結果の分析とドキュメント更新

- [ ] **Step 1: 結果を分析する**

```bash
python3 -c "
import json
from collections import defaultdict

with open('data/benchmark_results/summary.json') as f:
    results = json.load(f)

grouped = defaultdict(list)
for r in results:
    if not r.get('error'):
        grouped[r['concurrent']].append(r)

print('concurrent | 総時間(avg) | avg_TTFT(avg) | max_TTFT(avg)')
print('-' * 60)
for c in sorted(grouped.keys()):
    trials = grouped[c]
    avg_e = sum(r['elapsed'] for r in trials) / len(trials)
    avg_t = sum(r['ttft_avg'] for r in trials) / len(trials)
    max_t = sum(r['ttft_max'] for r in trials) / len(trials)
    print(f'{c:>10} | {avg_e:>11.1f}s | {avg_t:>13.1f}s | {max_t:>13.1f}s')
"
```

- [ ] **Step 2: 判定基準で最適値を決める**

以下の基準で `OPTIMAL_CONCURRENT` を決定する:

- 総時間が concurrent=1 より **20% 以上短縮** → その値を採用
- concurrent 増加でTTFTが **増加傾向** → 並列過剰（小さい値を選ぶ）
- concurrent=8 でエラーなし かつ最速 → 8 を採用

- [ ] **Step 3: `model_optimization.md` の Section 3・6 を実データで更新する**

`docs/model_optimization.md` を開き、以下を更新する:

**Section 3 の修正箇所:**
- 「240秒の塩漬け」の説明を「過去の特殊状況（2026-04-04 の一時的サーバー混雑）に由来する外れ値」と修正
- 実測 avg TTFT 29.6s を追記
- 最適並列数を実験結果の値で更新

**Section 6 の修正箇所:**
- 実験結果の表（concurrent × 総時間 × avg_TTFT）を追加
- 「best = X（実測 YYYY-MM-DD, AL論文 18p, gemini-3-flash-preview）」と根拠を明記

- [ ] **Step 4: `ParallelTranslator` のデフォルト値を実験結果で更新する**

`core/engine/p4_translate/parallel_translator.py` の 23 行目:

```python
# 実験前
max_concurrent_sections: int = 4
# 実験後（OPTIMALが変わった場合）
max_concurrent_sections: int = <OPTIMAL_CONCURRENT>
```

- [ ] **Step 5: Commit**

```bash
git add docs/model_optimization.md core/engine/p4_translate/parallel_translator.py
git commit -m "docs: update concurrent_sections design doc with benchmark results (YYYY-MM-DD)"
```

---

## 自己レビューチェック

- [x] dead semaphore 除去: Task 1 でカバー
- [x] `--concurrent` フラグ: Task 2 でカバー（テスト付き）
- [x] ベンチマークスクリプト: Task 3 でカバー
- [x] Phase 1-3 準備手順: Task 4 に明記
- [x] 実行手順: Task 5 に明記
- [x] ドキュメント更新: Task 6 にカバー
- [x] プレースホルダーなし: SESSION_ID のみ実行時設定（意図的）
- [x] 型整合性: `max_concurrent_sections: int` が全ファイルで一致
