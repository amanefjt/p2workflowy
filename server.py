import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uuid
import shutil
import os
from pathlib import Path
from typing import Optional, Dict

from core.pipeline import run_pipeline
from core.config import DATA_DIR, STATE_DIR, PROJECT_ROOT

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

# タスク管理（簡易版）
task_status: Dict[str, dict] = {}

@app.get("/")
async def index():
    return FileResponse(web_dir / "index.html")

@app.get("/ronbun")
async def ronbun_page():
    return FileResponse(web_dir / "ronbun.html")

@app.post("/api/process")
async def process(
    text: str = Form(""),
    title: str = Form("Untitled"),
    api_key: Optional[str] = Form(None),
    expertise: str = Form("文化人類学"),
    glossary: Optional[UploadFile] = File(None),
    pdf_file: Optional[UploadFile] = File(None),
    export_mode: str = Form("p2workflowy"),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    # テスト時のダミーキーがブラウザの localStorage に残ってしまうケースの対策
    if api_key and api_key.strip() in ["DUMMY_KEY", ""]:
        api_key = None
        
    task_id = str(uuid.uuid4())
    
    # アップロード用の一時ディレクトリ
    upload_dir = DATA_DIR / "uploads" / task_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # ファイルの保存
    input_path = None
    if pdf_file and pdf_file.filename:
        input_path = upload_dir / pdf_file.filename
        print(f"Saving PDF to {input_path}")
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
        glossary_path = upload_dir / glossary.filename
        print(f"Saving Glossary to {glossary_path}")
        content = await glossary.read()
        with open(glossary_path, "wb") as f:
            f.write(content)
            
    print(f"Starting task {task_id} for input: {input_path} (Expertise: {expertise})")
    
    # Initialize status BEFORE starting background task to avoid KeyError
    task_status[task_id] = {
        "status": "processing",
        "title": title,
        "progress": "処理を開始します...",
        "percentage": 5,
        "error": None
    }
    
    background_tasks.add_task(
        run_task, task_id, str(input_path), str(glossary_path) if glossary_path else None, title, api_key, expertise, export_mode
    )
    
    return {"task_id": task_id}

def run_task(task_id: str, input_path: str, glossary_path: Optional[str], title: str, api_key: Optional[str], expertise: str, export_mode: str):
    try:
        # パイプライン実行
        run_pipeline(
            input_path=input_path,
            glossary_path=glossary_path,
            title=title,
            api_key=api_key,
            session_id=task_id,
            expertise=expertise,
            export_mode=export_mode,
            model="gemini-3.1-flash-lite-preview",
            pdf_mode="hybrid"
        )
        task_status[task_id]["status"] = "completed"
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"Error in task {task_id}: {error_msg}")
        task_status[task_id]["status"] = "failed"
        task_status[task_id]["error"] = str(e)

@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # プログレスの動的更新（status.json およびファイル存在チェック）
    if task_status[task_id]["status"] == "processing":
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
        
        # 2. ファイル存在によるバックアップ判定（status.json が詳細を更新していない場合用）
        elif (session_dir / "phase4_translation.json").exists():
            task_status[task_id]["progress"] = "最終書き出し中..."
            task_status[task_id]["percentage"] = 95
        elif (session_dir / "phase3_structure.json").exists():
            task_status[task_id]["progress"] = "本文翻訳中..."
            task_status[task_id]["percentage"] = 70
        elif (session_dir / "phase2_meta.json").exists():
            task_status[task_id]["progress"] = "本文構造の構築中..."
            task_status[task_id]["percentage"] = 50
        elif (session_dir / "phase1_clean.json").exists():
            task_status[task_id]["progress"] = "内容の分析中..."
            task_status[task_id]["percentage"] = 30
        elif (session_dir / "extracted_from_pdf.txt").exists():
            task_status[task_id]["progress"] = "テキストの準備中..."
            task_status[task_id]["percentage"] = 10
            
    return task_status[task_id]

@app.get("/api/download/{task_id}/{file_type}")
async def download(task_id: str, file_type: str):
    # uploads（アップロード先）と state（成果物保存先）の両方を確認
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
        
    # 最新のファイルを選択
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    target_file = files[0]
    
    # media_type の推定
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
    # サンプルが存在しない場合は空のCSVを生成
    sample_path = DATA_DIR / "sample" / "glossary_sample.csv"
    if not sample_path.exists():
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        with open(sample_path, "w", encoding="utf-8-sig") as f:
            f.write("Term,Translation\nLLM,大規模言語モデル\nAI,人工知能\n")
    
    # ユーザーが安心できるよう、拡張子と名称を非常に明示的にする
    return FileResponse(
        sample_path, 
        filename="p2workflowy_glossary_sample.csv",
        media_type="text/csv",
        content_disposition_type="attachment"
    )

# 静的ファイルをルートで配信（他のルート定義の後で行う必要がある）
app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
