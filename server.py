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
    text: str = Form(...),
    title: str = Form("Untitled"),
    api_key: Optional[str] = Form(None),
    expertise: str = Form("文化人類学"),
    glossary: Optional[UploadFile] = File(None),
    export_mode: str = Form("p2workflowy"),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    task_id = str(uuid.uuid4())
    
    # アップロード用の一時ディレクトリ
    upload_dir = DATA_DIR / "uploads" / task_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    input_path = upload_dir / "input.txt"
    with open(input_path, "w", encoding="utf-8") as f:
        f.write(text)
    
    glossary_path = None
    if glossary and glossary.filename:
        glossary_path = upload_dir / "glossary.csv"
        with open(glossary_path, "wb") as f:
            shutil.copyfileobj(glossary.file, f)
            
    print(f"Starting task {task_id} for input: {input_path} (Expertise: {expertise})")
    
    # Initialize status BEFORE starting background task to avoid KeyError
    task_status[task_id] = {
        "status": "processing",
        "title": title,
        "progress": "Pipeline started...",
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
            model="gemini-3.1-flash-lite"
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
    
    # プログレスの動的更新（ファイル存在チェック）
    if task_status[task_id]["status"] == "processing":
        session_dir = STATE_DIR / task_id
        if (session_dir / "phase4_translation.json").exists():
            task_status[task_id]["progress"] = "Phase 5: Exporting..."
        elif (session_dir / "phase3_structure.json").exists():
            task_status[task_id]["progress"] = "Phase 4: Translating..."
        elif (session_dir / "phase2_meta.json").exists():
            task_status[task_id]["progress"] = "Phase 3: Structuring..."
        elif (session_dir / "phase1_clean.json").exists():
            task_status[task_id]["progress"] = "Phase 2: Analyzing..."
        else:
            task_status[task_id]["progress"] = "Phase 1: Preprocessing..."
            
    return task_status[task_id]

@app.get("/api/download/{task_id}/{file_type}")
async def download(task_id: str, file_type: str):
    # まず uploads ディレクトリを確認（Web版の標準保存先）
    task_dir = DATA_DIR / "uploads" / task_id
    
    # もしなければ従来の STATE_DIR を確認
    if not task_dir.exists():
        task_dir = STATE_DIR / task_id
        
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="Result directory not found")
        
    if file_type == "markdown":
        files = list(task_dir.glob("*.md"))
        # ronbun.md を除外
        files = [f for f in files if not f.name.endswith("_ronbun.md")]
    elif file_type == "workflowy":
        files = list(task_dir.glob("*_p2.txt"))
    elif file_type == "ronbun":
        files = list(task_dir.glob("*_ronbun.md"))
    else:
        raise HTTPException(status_code=400, detail="Invalid file type")
        
    if not files:
        raise HTTPException(status_code=404, detail="Result file not found")
        
    # 最新のファイルを選択
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return FileResponse(files[0], filename=files[0].name)

@app.get("/api/glossary/sample")
async def get_sample_glossary():
    # サンプルが存在しない場合は空のCSVを生成
    sample_path = DATA_DIR / "sample" / "glossary_sample.csv"
    if not sample_path.exists():
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        with open(sample_path, "w", encoding="utf-8-sig") as f:
            f.write("Term,Translation\nLLM,大規模言語モデル\n")
    return FileResponse(sample_path, filename="glossary_sample.csv")

# 静的ファイルをルートで配信（他のルート定義の後で行う必要がある）
app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
