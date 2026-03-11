import sys
import json
from pathlib import Path

# プロジェクトルートを追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
        # p2workflowy モードでテスト
        output_paths = run_phase5(
            input_path_str=input_path,
            title=title,
            phase2_state_path=phase2,
            structure_state_path=phase3,
            phase4_state_path=phase4,
            export_mode="p2workflowy",
        )
        print(f"\n[p2workflowy] Success! ({len(output_paths)} files)")
        for p in output_paths:
            print(f"  - {p}")

        # ronbunnihongo モードでテスト
        rn_paths = run_phase5(
            input_path_str=input_path,
            title=title,
            phase2_state_path=phase2,
            structure_state_path=phase3,
            phase4_state_path=phase4,
            export_mode="ronbunnihongo",
        )
        print(f"\n[ronbunnihongo] Success! ({len(rn_paths)} files)")
        for p in rn_paths:
            print(f"  - {p}")
            # RonbunNihongo の中身をプレビュー表示
            print(f"\n--- {p.name} Content Preview ---")
            with open(p, "r", encoding="utf-8") as f:
                print(f.read()[:500])
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_debug_export()
