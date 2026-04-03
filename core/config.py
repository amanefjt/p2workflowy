import os
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# .env ファイルを読み込む
load_dotenv()

# プロジェクトルートの設定
PROJECT_ROOT = Path(__file__).parent.parent
STATE_DIR = PROJECT_ROOT / "state"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# 設定定数
MAX_SESSION_HISTORY = 10

# LLM 設定 (環境変数から取得)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
APP_ADMIN_PASSCODE = os.environ.get("APP_ADMIN_PASSCODE")
if not GEMINI_API_KEY:
    print("Warning: Neither GEMINI_API_KEY nor GOOGLE_API_KEY is set in environment variables.")

def load_coreprompts() -> Dict[str, str]:
    """core/coreprompts.json から全てのプロンプトテンプレートを読み込む。"""
    prompts_path = PROJECT_ROOT / "core" / "coreprompts.json"
    if prompts_path.exists():
        with open(prompts_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def print_log(msg: str):
    """標準出力とログファイルにメッセージを記録する。"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(formatted_msg + "\n")

class SessionState:
    """パイプラインの実行状態（中間データ、メタデータ）を管理するクラス。"""
    
    MAX_STATE_SESSIONS = 300

    def __init__(self, session_id: str = None, base_dir: Path = None, mode: str = "paper"):
        if session_id is None:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = session_id
        self.mode = mode # "book" or "paper"
        
        if base_dir is None:
            base_dir = STATE_DIR / session_id
        self.session_dir = Path(base_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        self.status_json = self.session_dir / "status.json"
        self.logs_dir = self.session_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # 各フェーズの状態保存先
        self.phase1_preprocessor = self.session_dir / "phase1_preprocessor.json"
        self.phase2_meta = self.session_dir / "phase2_meta.json"
        self.phase3_structure = self.session_dir / "phase3_structure.json"
        self.phase3_sections = self.session_dir / "phase3_sections.json"
        self.phase4_translate = self.session_dir / "phase4_translate.json"
        self.phase5_export = self.session_dir / "phase5_export.txt"
        
        self.vlm_cache = self.session_dir / "vlm_cache.json"

    def update_status(self, message: str, percentage: int = None, phase_n: int = 0, phase_name: str = "Unknown"):
        """status.json を更新し、進捗を記録する。"""
        status_data = {
            "session_id": self.session_id,
            "phase": phase_n,
            "phase_name": phase_name,
            "message": message,
            "percentage": percentage,
            "updated_at": datetime.now().isoformat()
        }
        with open(self.status_json, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=4)
        
        log_msg = f"Phase {phase_n} [{phase_name}]: {message}"
        if percentage is not None:
            log_msg += f" ({percentage}%)"
        print_log(log_msg)

    def cleanup_old_sessions(self):
        """古いセッションディレクトリを削除し、MAX_STATE_SESSIONS 以内に収める。"""
        # state/ 直下のディレクトリをすべて取得（隠しフォルダは除く）
        dirs = [d for d in STATE_DIR.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != "book_sessions"]
        
        # ディレクトリ数が上限を超えている場合
        if len(dirs) > self.MAX_STATE_SESSIONS:
            # 最終更新日時（mtime）が古い順にソート
            dirs.sort(key=lambda d: d.stat().st_mtime)
            
            # 削除対象のディレクトリを特定
            num_to_delete = len(dirs) - self.MAX_STATE_SESSIONS
            dirs_to_delete = dirs[:num_to_delete]
            
            print_log(f"  [Cleanup] 古いセッションを削除します（保持上限: {self.MAX_STATE_SESSIONS}）")
            import shutil
            for d in dirs_to_delete:
                try:
                    shutil.rmtree(d)
                except Exception as e:
                    print_log(f"  [Cleanup] 削除失敗 {d}: {e}")

import csv

def load_glossary_csv(path: str | Path | None = None) -> dict:
    """glossary.csv を読み込んで辞書形式で返す。"""
    if path is None:
        path = PROJECT_ROOT / "data" / "glossary.csv"
    
    path = Path(path)
    if not path.exists():
        return {}

    glossary = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                glossary[row[0].strip()] = row[1].strip()
    return glossary
