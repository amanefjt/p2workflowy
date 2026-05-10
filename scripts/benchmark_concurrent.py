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
