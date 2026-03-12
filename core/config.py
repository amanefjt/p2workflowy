"""
p2workflowy V2: 設定管理
.env と coreprompts.json の読み込み、定数管理。
"""

import json
import os
from pathlib import Path

# --- ロギングユーティリティ ---
import time
from datetime import datetime

def print_log(message: str = "", end: str = "\n"):
    """
    タイムスタンプ付きのログを出力するヘルパー関数。
    出力例: [12:30:15] [Phase 1] 処理を開始します
    """
    if not message:
        print(end=end)
        return
        
    ts = datetime.now().strftime("%H:%M:%S")
    # すでに時刻等が付随している形式の場合はスキップを考慮せず、単純に付与する
    print(f"[{ts}] {message}", end=end)

from dotenv import load_dotenv


# プロジェクトルートの特定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
DATA_DIR = PROJECT_ROOT / "data"
STATE_DIR = PROJECT_ROOT / "state"

# .env の読み込み（プロジェクトルートの .env を優先、なければ core/.env ...など、通常の load_dotenv デフォルト動作に近い形にするが、明示的に指定）
_env_path = PROJECT_ROOT / ".env"
load_dotenv(_env_path)

# API キー
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""

# state ディレクトリの自動作成
STATE_DIR.mkdir(exist_ok=True)


def load_coreprompts() -> dict:
    """coreprompts.json を読み込んで辞書として返す。"""
    prompts_path = CORE_DIR / "coreprompts.json"
    with open(prompts_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_glossary_csv(glossary_path: str | None = None) -> dict[str, str]:
    """
    glossary.csv を読み込み、{英語: 日本語} の辞書として返す。
    glossary_path が未指定の場合、デフォルトの data/glossary.csv を使用。
    """
    if glossary_path is None:
        csv_path = DATA_DIR / "glossary.csv"
    else:
        csv_path = Path(glossary_path)
        if not csv_path.is_absolute():
            csv_path = PROJECT_ROOT / csv_path

    glossary = {}
    if not csv_path.exists():
        return glossary
        
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue
            # CSV: English,Japanese（ヘッダーなし）
            parts = line.split(",", 1)
            if len(parts) == 2:
                en_term = parts[0].strip()
                ja_term = parts[1].strip()
                if en_term:
                    glossary[en_term] = ja_term
    return glossary


# 保持するセッション状態の最大数
MAX_STATE_SESSIONS = 10

class SessionState:
    """セッション（1つのPDF処理）ごとの状態パスを管理する。"""
    def __init__(self, input_path: str, session_id: str | None = None):
        if session_id:
            self.session_id = session_id
        else:
            self.session_id = Path(input_path).stem
        self.session_dir = STATE_DIR / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        # self._cleanup_old_sessions() # コンストラクタでの自動実行は廃止

    def cleanup_old_sessions(self):
        """古いセッションディレクトリをクリーンアップする。"""
        self._cleanup_old_sessions()

    def _cleanup_old_sessions(self):
        """古いセッションディレクトリを削除し、MAX_STATE_SESSIONS 以内に収める。"""
        # state/ 直下のディレクトリをすべて取得（隠しフォルダは除く）
        dirs = [d for d in STATE_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
        
        # ディレクトリ数が上限を超えている場合
        if len(dirs) > MAX_STATE_SESSIONS:
            # 最終更新日時（mtime）が古い順にソート
            dirs.sort(key=lambda d: d.stat().st_mtime)
            
            # 削除対象のディレクトリを特定
            num_to_delete = len(dirs) - MAX_STATE_SESSIONS
            dirs_to_delete = dirs[:num_to_delete]
            
            print_log(f"  [Cleanup] 古いセッションを削除します（保持上限: {MAX_STATE_SESSIONS}）")
            import shutil
            for d in dirs_to_delete:
                try:
                    shutil.rmtree(d)
                    print_log(f"  [Cleanup] 削除完了: {d.name}")
                except Exception as e:
                    print_log(f"  [Cleanup] 削除失敗: {d.name} ({e})")

    @property
    def phase1(self) -> Path:
        return self.session_dir / "phase1_clean.json"

    @property
    def phase2(self) -> Path:
        return self.session_dir / "phase2_meta.json"

    @property
    def phase3_structure(self) -> Path:
        return self.session_dir / "phase3_structure.json"

    @property
    def phase3_sections(self) -> Path:
        return self.session_dir / "phase3_sections.json"

    @property
    def phase4(self) -> Path:
        return self.session_dir / "phase4_translation.json"

    @property
    def metrics_csv(self) -> Path:
        return self.session_dir / "ttft_metrics.csv"

    @property
    def status_json(self) -> Path:
        return self.session_dir / "status.json"

    def update_status(self, progress: str, percentage: int | float | None = None):
        """進捗状況（メッセージとパーセンテージ）を status.json に書き込む。"""
        status_data = {
            "progress_message": progress,
            "percentage": percentage,
            "timestamp": datetime.now().isoformat()
        }
        with open(self.status_json, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=4)
        
        # CLI版でも進捗が見えるように標準出力にも出す
        if percentage is not None:
            print_log(f"  [Progress] {progress} ({percentage}%)")
        else:
            print_log(f"  [Progress] {progress}")

    def read_status(self) -> dict:
        """status.json から現在の進捗状況を読み取る。"""
        if not self.status_json.exists():
            return {"progress_message": "Initializing...", "percentage": 0}
        try:
            with open(self.status_json, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"progress_message": "Processing...", "percentage": 0}


# --- メトリクス計測（継続運用） ---
METRICS_CSV_PATH = STATE_DIR / "ttft_metrics.csv"


# --- 互換性のための古い定義（削除予定だが一時的に残す場合は注意） ---
# 今後は SessionState インスタンス経由でアクセスすることを推奨
PHASE1_STATE_DEFAULT = STATE_DIR / "phase1_clean.json"
PHASE2_STATE_DEFAULT = STATE_DIR / "phase2_meta.json"
PHASE3_STRUCTURE_STATE_DEFAULT = STATE_DIR / "phase3_structure.json"
PHASE3_SECTIONS_STATE_DEFAULT = STATE_DIR / "phase3_sections.json"
PHASE4_STATE_DEFAULT = STATE_DIR / "phase4_translation.json"
