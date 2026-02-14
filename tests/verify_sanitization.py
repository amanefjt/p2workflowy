import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.utils import Utils

def test_sanitization():
    test_cases = [
        {
            "input": "はい、翻訳しました。\nこれはテストです。\n以上が翻訳結果です。",
            "expected": "これはテストです。"
        },
        {
            "input": "ご提示いただいた[Target Text]について、学術的な日本語で翻訳しました。\n[Context]や[Glossary]を確認しましたが、一部矛盾があるようです。\n原文の内容に集中して翻訳を行いました。\n\n文化とは多様なものである。\n---",
            "expected": "文化とは多様なものである。"
        },
        {
            "input": "おっしゃる通り、矛盾を感知しました。翻訳します。\n\n構造主義は重要である。\nどうぞ。",
            "expected": "構造主義は重要である。"
        }
    ]

    print("Running sanitization tests...")
    for i, case in enumerate(test_cases):
        output = Utils.sanitize_translated_output(case["input"])
        if output.strip() == case["expected"].strip():
            print(f"Test {i+1}: PASSED")
        else:
            print(f"Test {i+1}: FAILED")
            print(f"  Input: {case['input']!r}")
            print(f"  Output: {output!r}")
            print(f"  Expected: {case['expected']!r}")

if __name__ == "__main__":
    test_sanitization()
