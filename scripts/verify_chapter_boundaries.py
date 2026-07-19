"""章分割境界の実測検証（I-22 / I-24 / I-25）。

VLM フルランは行わず PDFSplitter.split() のみを実行する。
Route 3 の LLM TOC 抽出は state/vlm_cache.json にキャッシュされる。
"""
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.engine.p1_ingest.pdf_splitter import PDFSplitter  # noqa: E402
from core.config import PROJECT_ROOT  # noqa: E402

BOOKS = {
    "corfra": "data/input/Booksample/corfra/corfrapdf_split.pdf",
    "relations": "data/input/Booksample/relations/relationspdf.pdf",
    "Naven": "data/input/Booksample/Naven/Naven.pdf",
    "PSE": "data/input/Booksample/pse/PSEpdf.pdf",
}

# 実測済みの正解（仕様書 §2 参照）
EXPECTED = {
    "corfra": {"3 Place": 77, "4 Things": 93, "7 Knowing": 153,
               "8 Anonymous Introduction": 171},
    "relations": {"1. Experimentations, English and Otherwise": 36,
                  "2. Registers of Comparison": 56,
                  "3. Expansion and Contraction": 82,
                  "4. The Dissimilar and the Different": 106,
                  "5. Enlightenment Dramas": 128,
                  "6. Kinship Unbound": 150},
}


def main() -> int:
    api_key = None
    import os
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    out_root = PROJECT_ROOT / "state" / "_verify_chapters"
    failures = 0

    for name, rel in BOOKS.items():
        path = str(PROJECT_ROOT / rel)
        print(f"\n===== {name} =====")
        splitter = PDFSplitter(api_key=api_key)
        chapters = splitter.split(path, out_root / name)
        print(f"  章数: {len(chapters)}")

        for ch in chapters:
            rng = ch.get("page_range")
            print(f"    {ch['title'][:40]:42s} {rng} role={ch['role']}")

        for title, expected_idx in EXPECTED.get(name, {}).items():
            match = [c for c in chapters if c["title"] == title]
            if not match:
                print(f"  NG  '{title}' が章リストに無い")
                failures += 1
                continue
            actual = match[0]["page_range"][0] - 1
            mark = "OK " if actual == expected_idx else "NG "
            if actual != expected_idx:
                failures += 1
            print(f"  {mark}'{title}' 期待idx{expected_idx} 実際idx{actual}")

    print(f"\n不一致: {failures} 件")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
