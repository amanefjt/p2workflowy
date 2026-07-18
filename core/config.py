import os
import json
import csv
import logging as _logging
import functools
import sys
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

@functools.lru_cache(maxsize=1)
def load_coreprompts() -> Dict[str, str]:
    """core/coreprompts.json から全てのプロンプトテンプレートを読み込む。
    
    NOTE: @lru_cache により初回のみ JSON を読み込み、以降はメモリから返す。
    coreprompts.json を変更した場合はプロセスの再起動が必要。
    """
    prompts_path = PROJECT_ROOT / "core" / "coreprompts.json"
    if prompts_path.exists():
        with open(prompts_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

_pipeline_logger: _logging.Logger | None = None

def _get_pipeline_logger() -> _logging.Logger:
    global _pipeline_logger
    if _pipeline_logger is not None:
        return _pipeline_logger
    logger = _logging.getLogger("p2workflowy.pipeline")
    logger.setLevel(_logging.DEBUG)
    logger.propagate = False
    fmt = _logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    sh = _logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = _logging.FileHandler(
        LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    _pipeline_logger = logger
    return logger

def print_log(msg: str):
    """標準出力とログファイルにメッセージを記録する（スレッドセーフ）。"""
    _get_pipeline_logger().info(msg)

class SessionState:
    """パイプラインの実行状態（中間データ、メタデータ）を管理するクラス。"""
    
    MAX_STATE_SESSIONS = 10

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
        self.phase1_route = self.session_dir / "phase1_route.json"
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

def load_glossary_csv(path: str | Path | None = None) -> dict:
    """glossary.csv を読み込んで辞書形式で返す。

    形式: 1列目=英語, 2列目=日本語 （ヘッダー行は自動スキップ）
    ヘッダー判定: 1行目の1列目が小文字化して既知のヘッダーキーワードと一致する場合スキップ。
    """
    if path is None:
        path = PROJECT_ROOT / "data" / "glossary.csv"

    path = Path(path)
    if not path.exists():
        return {}

    # ヘッダー行として認識する1列目の値（大文字小文字不問）
    _HEADER_KEYWORDS = {"en", "english", "englishterm", "word", "term", "source", "original"}

    glossary = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if len(row) < 2:
                continue
            # 1行目がヘッダーキーワードと一致する場合はスキップ
            if i == 0 and row[0].strip().lower() in _HEADER_KEYWORDS:
                continue
            key = row[0].strip()
            val = row[1].strip()
            if key:  # 空キーは除外
                glossary[key] = val
    return glossary

def load_glossary_entries(path: str | Path | None = None) -> list[dict]:
    """glossary CSV を en/ja の 2 列で読み込む（3 列目以降は無視）。

    load_glossary_csv（dict を返す）とは別に、build_term_layer が扱う list[dict] 形式で返す。
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
            })
    return entries
