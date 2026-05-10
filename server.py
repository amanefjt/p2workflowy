import asyncio
import uuid
import os
import re
import hmac
import secrets
import shutil
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, BackgroundTasks, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.pipeline import run_pipeline
from core.config import DATA_DIR, STATE_DIR, PROJECT_ROOT, APP_ADMIN_PASSCODE, GEMINI_API_KEY
from core.llm_client import get_default_model

app = FastAPI(title="p2workflowy Web")

# CORS 設定: Cloudflare Pages からの通信を許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://p2workflowy.pages.dev", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ワークスペース内の web ディレクトリのパス
web_dir = PROJECT_ROOT / "web"
web_dir.mkdir(exist_ok=True)

# 【追加】UUID検証用の正規表現
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# タスク管理（簡易版）
MAX_TASK_STATUS_ENTRIES = 50
task_status: Dict[str, dict] = {}

# パイプライン直列化: 同時実行を1タスクに制限する
# asyncio.Semaphore はイベントループ生成後に初期化する必要があるため遅延初期化
_pipeline_semaphore: Optional[asyncio.Semaphore] = None
_pipeline_queue: list = []  # 待機中のタスクIDを順番に保持

def _get_pipeline_semaphore() -> asyncio.Semaphore:
    global _pipeline_semaphore
    if _pipeline_semaphore is None:
        _pipeline_semaphore = asyncio.Semaphore(1)
    return _pipeline_semaphore

def _cleanup_task_status():
    """完了済みタスクが多すぎる場合、古いエントリを削除する。"""
    if len(task_status) <= MAX_TASK_STATUS_ENTRIES:
        return
    # 完了 or 失敗のタスクを古い順に削除
    completed = [tid for tid, info in task_status.items() if info.get('status') in ('completed', 'failed')]
    num_to_delete = len(task_status) - MAX_TASK_STATUS_ENTRIES
    for tid in completed[:num_to_delete]:
        # 【重要】物理ファイルも削除する
        try:
            shutil.rmtree(DATA_DIR / "uploads" / tid, ignore_errors=True)
            shutil.rmtree(STATE_DIR / tid, ignore_errors=True)
        except Exception as e:
            print(f"Cleanup error for {tid}: {e}")
        del task_status[tid]

@app.get("/")
async def index():
    return FileResponse(web_dir / "index.html")

@app.get("/ronbun")
async def ronbun_page():
    return FileResponse(web_dir / "ronbun.html")

# パイプラインを別スレッドで実行するための非同期ラッパー関数
async def run_pipeline_in_background(task_id: str, input_path: str, glossary_path: Optional[str], title: Optional[str], api_key: Optional[str], expertise: str, export_mode: str, is_book: bool, max_chapters: Optional[int] = None):
    semaphore = _get_pipeline_semaphore()

    # キューに追加して待機状態を通知
    _pipeline_queue.append(task_id)
    queue_pos = len(_pipeline_queue)
    if queue_pos > 1:
        task_status[task_id]["status"] = "queued"
        task_status[task_id]["progress"] = f"待機中... (キュー位置: {queue_pos - 1})"
        task_status[task_id]["percentage"] = 0
        print(f"Task {task_id}: キュー待機中 (位置: {queue_pos - 1})")

    try:
        async with semaphore:
            # キュー位置表示を更新
            _pipeline_queue.remove(task_id)
            task_status[task_id]["status"] = "processing"
            task_status[task_id]["progress"] = "準備中..."
            task_status[task_id]["percentage"] = 5

            print(f"Task {task_id}: バックグラウンドスレッドでパイプラインを開始します...")

            # asyncio.to_thread で同期関数である run_pipeline をスレッドプールにオフロード
            if is_book:
                from core.book_manager import BookManager
                manager = BookManager(input_path=input_path, api_key=api_key)
                await asyncio.to_thread(
                    manager.run,
                    glossary_path=glossary_path,
                    thinking_level="High",
                    pdf_mode="hybrid",
                    tier="paid",
                    max_chapters=max_chapters,
                )
            else:
                await asyncio.to_thread(
                    run_pipeline,
                    input_path=input_path,
                    glossary_path=glossary_path,
                    title=title,
                    api_key=api_key,
                    session_id=task_id,
                    expertise=expertise,
                    export_mode=export_mode,
                    model=None,
                    thinking_level="High",
                    pdf_mode="hybrid",
                    tier="paid",
                    is_book=False,
                    structure_only=False,
                    resume_only=False
                )
            task_status[task_id]["status"] = "completed"
            print(f"Task {task_id}: 処理が正常に完了しました。")
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"Task {task_id}: エラーが発生しました - {error_msg}")
        if task_id in _pipeline_queue:
            _pipeline_queue.remove(task_id)
        task_status[task_id]["status"] = "failed"
        task_status[task_id]["error"] = str(e)

@app.post("/api/process")
async def process(
    text: str = Form(""),
    title: Optional[str] = Form(None),
    api_key: Optional[str] = Form(None),
    expertise: str = Form("文化人類学"),
    glossary: Optional[UploadFile] = File(None),
    pdf_file: Optional[UploadFile] = File(None),
    export_mode: str = Form("p2workflowy"),
    is_book: bool = Form(False),
    max_chapters: Optional[int] = Form(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    task_id = str(uuid.uuid4())
    
    # パスコード認証ロジック (V3.2.8: task_id 順序修正)
    clean_api_key = str(api_key).strip() if api_key else ""
    clean_passcode = str(APP_ADMIN_PASSCODE).strip() if APP_ADMIN_PASSCODE else ""
    
    if clean_api_key and clean_passcode and hmac.compare_digest(clean_api_key, clean_passcode):
        print(f"Admin Passcode detected for task {task_id}. Using server-side API key.")
        api_key = GEMINI_API_KEY
    elif clean_api_key:
        mask_key = (clean_api_key[:4] + "...") if len(clean_api_key) > 4 else clean_api_key
        print(f"Passcode/Key provided but did not match ADMIN_PASSCODE (Input: {mask_key})")

    # テスト時のダミーキーがブラウザの localStorage に残ってしまうケースの対策
    if api_key and api_key.strip() in ["DUMMY_KEY", ""]:
        api_key = None
        
    # アップロード用の一時ディレクトリ
    upload_dir = DATA_DIR / "uploads" / task_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    def _safe_upload_path(upload_dir: Path, filename: str, fallback: str) -> Path:
        """ファイル名からディレクトリ成分を除去し、upload_dir 内に限定する。"""
        safe_name = Path(filename).name or fallback
        path = (upload_dir / safe_name).resolve()
        if not path.is_relative_to(upload_dir.resolve()):
            raise HTTPException(status_code=400, detail="Invalid filename")
        return path

    # ファイルの保存
    input_path = None
    if pdf_file and pdf_file.filename:
        input_path = _safe_upload_path(upload_dir, pdf_file.filename, "input.pdf")
        print(f"Saving PDF to {input_path.name}")
        content = await pdf_file.read()
        with open(input_path, "wb") as f:
            f.write(content)
    elif text:
        input_path = upload_dir / "input.txt"
        print(f"Saving text input to {input_path}")
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        raise HTTPException(status_code=400, detail="テキストの入力、またはPDFファイルのアップロードが必要です。")

    glossary_path = None
    if glossary and glossary.filename:
        glossary_path = _safe_upload_path(upload_dir, glossary.filename, "glossary.csv")
        print(f"Saving Glossary to {glossary_path.name}")
        content = await glossary.read()
        with open(glossary_path, "wb") as f:
            f.write(content)
            
    print(f"Starting task {task_id} for input: {input_path} (Expertise: {expertise})")
    
    download_token = secrets.token_urlsafe(32)
    task_status[task_id] = {
        "status": "processing",
        "title": title,
        "progress": "準備中...",
        "percentage": 5,
        "error": None,
        "download_token": download_token,
    }

    background_tasks.add_task(
        run_pipeline_in_background, task_id, str(input_path), str(glossary_path) if glossary_path else None, title, api_key, expertise, export_mode, is_book, max_chapters
    )

    _cleanup_task_status()
    return {"task_id": task_id, "download_token": download_token}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    # 【追加】多層防御: task_id が正しいUUIDフォーマットか検証
    if not UUID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id format")

    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # キュー待機中はキュー位置を動的に更新
    if task_status[task_id]["status"] == "queued":
        pos = _pipeline_queue.index(task_id) if task_id in _pipeline_queue else 0
        task_status[task_id]["progress"] = f"待機中... (キュー位置: {pos})"

    # プログレスの動的更新（status.json およびファイル存在チェック）
    elif task_status[task_id]["status"] == "processing":
        session_id = task_id
        session_dir = STATE_DIR / session_id
        
        # 1. status.json があればそれを優先
        status_file = session_dir / "status.json"
        if status_file.exists():
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    import json
                    data = json.load(f)
                    task_status[task_id]["progress"] = data.get("progress_message", "Processing...")
                    task_status[task_id]["percentage"] = data.get("percentage", 0)
            except Exception:
                pass
        
        # 2. ファイル存在によるバックアップ判定
        elif (session_dir / "phase4_translation.json").exists():
            task_status[task_id]["progress"] = "最終ファイルを作成中..."
            task_status[task_id]["percentage"] = 95
        elif (session_dir / "phase3_structure.json").exists():
            task_status[task_id]["progress"] = "解析・翻訳中..."
            task_status[task_id]["percentage"] = 75
        elif (session_dir / "phase2_meta.json").exists():
            task_status[task_id]["progress"] = "解析・翻訳中..."
            task_status[task_id]["percentage"] = 55
        elif (session_dir / "phase1_clean.json").exists():
            task_status[task_id]["progress"] = "解析準備中..."
            task_status[task_id]["percentage"] = 35
        elif (session_dir / "extracted_from_pdf.txt").exists():
            task_status[task_id]["progress"] = "準備中..."
            task_status[task_id]["percentage"] = 15
            
    return task_status[task_id]

@app.get("/api/download/{task_id}/{file_type}")
async def download(task_id: str, file_type: str, token: str = ""):
    if not UUID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id format")
    # ダウンロードトークンで所有者を検証
    info = task_status.get(task_id)
    stored_token = info.get("download_token", "") if info else ""
    if not stored_token or not secrets.compare_digest(token, stored_token):
        raise HTTPException(status_code=403, detail="Invalid download token")

    search_dirs = [DATA_DIR / "uploads" / task_id, STATE_DIR / task_id]
    
    files = []
    for task_dir in search_dirs:
        if not task_dir.exists():
            continue
            
        if file_type == "markdown":
            found = list(task_dir.glob("*.md"))
            files.extend([f for f in found if not f.name.endswith("_ronbun.md")])
        elif file_type == "workflowy":
            files.extend(list(task_dir.glob("*_p2.txt")))
        elif file_type == "ronbun":
            files.extend(list(task_dir.glob("*_ronbun.md")))
        else:
            raise HTTPException(status_code=400, detail="Invalid file type")
            
    if not files:
        raise HTTPException(status_code=404, detail="Result file not found")
        
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    target_file = files[0]
    
    media_type = "application/octet-stream"
    if target_file.suffix == ".md":
        media_type = "text/markdown"
    elif target_file.suffix == ".txt":
        media_type = "text/plain"
        
    return FileResponse(
        target_file, 
        filename=target_file.name, 
        media_type=media_type,
        content_disposition_type="attachment"
    )

@app.get("/api/glossary/sample")
async def get_sample_glossary():
    sample_path = DATA_DIR / "sample" / "glossary_sample.csv"
    if not sample_path.exists():
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        with open(sample_path, "w", encoding="utf-8-sig") as f:
            f.write("Term,Translation\nLLM,大規模言語モデル\nAI,人工知能\n")
    
    return FileResponse(
        sample_path, 
        filename="p2workflowy_glossary_sample.csv",
        media_type="text/csv",
        content_disposition_type="attachment"
    )

# 静的ファイルをルートで配信（他のルート定義の後で行う必要がある）
app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)