import asyncio
import sys
from pathlib import Path

# プロジェクトのディレクトリをパスに追加
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.skills import BookProcessorSkills

def test_split_by_toc():
    skills = BookProcessorSkills()
    raw_text = """
Table of Contents
Introduction .... 1
Part I: Theory .... 5
Chapter 1: The Concept .... 10
Chapter 2: Application .... 30

Introduction
This is the intro text.
Part I: Theory
Chapter 1: The Concept
This is chapter 1 body.
Chapter 2: Application
This is chapter 2 body.
"""
    toc = [
        {"title": "Introduction", "type": "chapter"},
        {"title": "Part I: Theory", "type": "part"},
        {"title": "Chapter 1: The Concept", "type": "chapter"},
        {"title": "Chapter 2: Application", "type": "chapter"}
    ]
    
    chapters = skills.split_by_toc(raw_text, toc)
    
    print(f"Total chapters: {len(chapters)}")
    for i, c in enumerate(chapters):
        print(f"--- Chapter {i+1}: {c['title']} ({c['type']}) ---")
        print(f"Length: {len(c['text'])}")
        print(f"Preview: {c['text'][:50]}...")

if __name__ == "__main__":
    test_split_by_toc()
