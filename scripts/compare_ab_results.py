import json
from pathlib import Path

def compare(pdf_name):
    base_dir = Path("state/ab_test") / pdf_name
    path_a = base_dir / "results_native.json"
    path_b = base_dir / "results_divider.json"
    
    if not path_a.exists() or not path_b.exists():
        return f"Results missing for {pdf_name}"
        
    data_a = json.loads(path_a.read_text())
    data_b = json.loads(path_b.read_text())
    
    report = []
    report.append(f"### Comparison: {pdf_name}")
    
    for i in range(len(data_a)):
        blocks_a = data_a[i]["blocks"]
        blocks_b = data_b[i]["blocks"]
        
        text_a = "\n".join([f"[{b.get('role')}] {b.get('content')[:50]}..." for b in blocks_a])
        text_b = "\n".join([f"[{b.get('role')}] {b.get('content')[:50]}..." for b in blocks_b])
        
        if text_a == text_b:
            report.append(f"Page {i+1}: MATCH")
        else:
            report.append(f"Page {i+1}: DIFF")
            report.append(f"  A: {text_a[:200]}")
            report.append(f"  B: {text_b[:200]}")
            
    return "\n".join(report)

print(compare("ALpdf"))
print("\n")
print(compare("chap1relations"))
