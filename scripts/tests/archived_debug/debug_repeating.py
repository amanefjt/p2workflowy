import json
import os
from core.pdf_ingester import detect_repeating_elements

# 最新の VLM 抽出結果をロード
state_dir = "state/psdpdf"
vlm_raw_path = os.path.join(state_dir, "phase0_vlm.json")

if os.path.exists(vlm_raw_path):
    with open(vlm_raw_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    
    repeating = detect_repeating_elements(raw_data)
    print("Detected repeating elements:")
    for r in sorted(list(repeating)):
        print(f" - '{r}'")
else:
    print("VLM raw data not found.")
