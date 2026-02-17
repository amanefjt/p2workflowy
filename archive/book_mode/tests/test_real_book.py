import asyncio
import sys
from pathlib import Path

# プロジェクトのディレクトリをパスに追加
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.skills import BookProcessorSkills
from src.utils import Utils

async def test_real_book_toc():
    skills = BookProcessorSkills()
    input_file = Path("/Users/shufujita/Antigravity/p2workflowy/tests/sample_data/Thinking Tnrough things.txt")
    
    if not input_file.exists():
        print(f"File not found: {input_file}")
        return
        
    raw_text = Utils.read_text_file(input_file)
    print(f"File loaded. Length: {len(raw_text)} chars.")
    
    print("Extracting TOC...")
    toc = await skills.extract_toc(raw_text)
    
    print("\nExtracted TOC:")
    import json
    print(json.dumps(toc, indent=2, ensure_ascii=False))
    
    if toc:
        print("\nSplitting by TOC...")
        chapters = skills.split_by_toc(raw_text, toc)
        print(f"Total chapters identified: {len(chapters)}")
        for i, c in enumerate(chapters[:5]): # Show first 5
            print(f"Chapter {i+1}: {c['title']} (Length: {len(c['text'])})")

if __name__ == "__main__":
    asyncio.run(test_real_book_toc())
