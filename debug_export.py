import sys
import json
from pathlib import Path

# プロジェクトルートを追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.phase5_export import run_phase5
from core.config import STATE_DIR

def run_debug_export():
    # state 直下にある中間ファイルを使用
    phase2 = STATE_DIR / "phase2_meta.json"
    phase3 = STATE_DIR / "phase3_structure.json"
    phase4 = STATE_DIR / "phase4_translation.json"
    
    # ダミーの入力パス（ファイル名抽出用）
    input_path = "data/sample/debug_paper.txt"
    title = "Debug Paper (From State)"
    
    print(f"Exporting using state files...")
    print(f"  Phase 2: {phase2}")
    print(f"  Phase 3: {phase3}")
    print(f"  Phase 4: {phase4}")
    
    try:
        md, wf, rn = run_phase5(
            input_path_str=input_path,
            title=title,
            phase2_state_path=phase2,
            structure_state_path=phase3,
            phase4_state_path=phase4
        )
        print(f"\nSuccess!")
        print(f"  Standard MD: {md}")
        print(f"  Workflowy:   {wf}")
        print(f"  RonbunNihongo: {rn}")
        
        # RonbunNihongo の中身を表示
        print("\n--- RonbunNihongo Content Preview ---")
        with open(rn, "r", encoding="utf-8") as f:
            print(f.read())
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_debug_export()
