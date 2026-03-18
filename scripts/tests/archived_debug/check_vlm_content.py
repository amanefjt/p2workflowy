import json
import os

vlm_cache_path = "state/psdpdf/vlm_cache.json"

if os.path.exists(vlm_cache_path):
    with open(vlm_cache_path, "r", encoding="utf-8") as f:
        cache = json.load(f)
    
    for pg, text in cache.items():
        if "Contents" in text or "Chapter 1" in text:
            print(f"--- Page {pg} ---")
            print(text)
            print("-" * 20)
else:
    print("VLM cache not found.")
