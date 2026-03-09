import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uuid
import shutil
import os
from pathlib import Path
from typing import Optional, Dict

from core.pipeline import run_pipeline
from core.config import DATA_DIR, STATE_DIR, PROJECT_ROOT

app = FastAPI(title="p2workflowy Web")

# ワークスペース内の web ディレクトリをマウント
# 静的ファイル（CSS, JS, 画像）用
# index.html はルートで返すため、StaticFiles ではなく FileResponse を使う
web_dir = PROJECT_ROOT / "web"
web_dir.mkdir(exist_ok=True)
app.mount("/web", StaticFiles(directory=str(web_dir)), name="web")

# タスク管理（簡易版）
task_status: Dict[str, dict] = {}

@app.get("/")
async def index():
    index_path = web_dir / "index.html"
    if not index_path.exists():
        return JSONResponse({"error": "index.html not found. Please wait while it's being created."}, status_code=503)
    return FileResponse(index_path)

@app.post("/api/process")
async def process(
    text: str = Form(...),
    title: str = Form("Untitled"),
    api_key: Optional[str] = Form(None),
    expertise: str = Form("文化人類学"),
    glossary: Optional[UploadFile] = File(None),
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
        run_task, task_id, str(input_path), str(glossary_path) if glossary_path else None, title, api_key, expertise
    )
    
    return {"task_id": task_id}

def run_task(task_id: str, input_path: str, glossary_path: Optional[str], title: str, api_key: Optional[str], expertise: str):
    try:
        # パイプライン実行
        run_pipeline(
            input_path=input_path,
            glossary_path=glossary_path,
            title=title,
            api_key=api_key,
            session_id=task_id,
            expertise=expertise
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
            task_status[task_id]["progress"] = "Phase 4: Translating..."
        elif (session_dir / "phase3_structure.json").exists():
            task_status[task_id]["progress"] = "Phase 3: Structuring..."
        elif (session_dir / "phase2_meta.json").exists():
            task_status[task_id]["progress"] = "Phase 2: Analyzing..."
        elif (session_dir / "phase1_clean.json").exists():
            task_status[task_id]["progress"] = "Phase 1: Preprocessing..."
            
    return task_status[task_id]

@app.get("/api/download/{task_id}/{file_type}")
async def download(task_id: str, file_type: str):
    session_dir = STATE_DIR / task_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Result directory not found")
        
    if file_type == "markdown":
        files = list(session_dir.glob("*.md"))
    elif file_type == "workflowy":
        files = list(session_dir.glob("*_workflowy.txt"))
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
        with open(sample_path, "w", encoding="utf-8") as f:
            f.write("Term,Translation\nLLM,大規模言語モデル\n")
    return FileResponse(sample_path, filename="glossary_sample.csv")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
