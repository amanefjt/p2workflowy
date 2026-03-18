import json
import re
import difflib

def normalize_heading(text: str) -> str:
    # 現在の core/phase3_structure.py と同じ正規化ロジック
    t = re.sub(r'[^\w\s]', ' ', text)
    norm = " ".join(t.lower().split())
    return norm

def is_valid_chapter(title: str, norm_toc: list[str]) -> bool:
    norm_title = normalize_heading(title)
    # 1. 完全一致
    if norm_title in norm_toc:
        return True, "Exact Match"
    # 2. 類似度判定
    for t in norm_toc:
        if norm_title in t or t in norm_title:
            return True, f"Substring Match: '{t}'"
        ratio = difflib.SequenceMatcher(None, norm_title, t).ratio()
        if ratio > 0.85:
            return True, f"Fuzzy Match ({ratio:.3f}): '{t}'"
    return False, None

def show_matches():
    with open("state/psdpdf/phase3_toc.json", "r") as f:
        toc_data = json.load(f)
    toc_list = [e["title"] for e in toc_data["toc"]]
    norm_toc = [normalize_heading(t) for t in toc_list]

    with open("state/psdpdf/phase1_clean.json", "r") as f:
        chunks = json.load(f)

    print(f"=== TOC Entries ({len(toc_list)}) ===")
    for t in toc_list:
        print(f"- {t}")
    print("\n=== Body Heading Matches in phase1_clean.json ===")
    
    found_any = False
    for chunk in chunks:
        text = chunk["text"].strip()
        # VLM出力風の見出しを検出
        if text.startswith("# ") and not text.startswith("##"):
            title = text[2:].strip()
            valid, reason = is_valid_chapter(title, norm_toc)
            status = "✅ MATCH" if valid else "❌ DEMOTE"
            print(f"[{chunk['id']:4}] {status:8} | '{text}'")
            if valid:
                print(f"       Reason: {reason}")
            found_any = True
            
    if not found_any:
        print("No # headings found in phase1_clean.json")

if __name__ == "__main__":
    show_matches()
