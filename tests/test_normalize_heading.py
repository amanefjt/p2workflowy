"""
test_normalize_heading.py: normalize_heading のユニットテスト
BUG-002 の回帰テストを含む。
"""
import sys
from pathlib import Path

# プロジェクトルートを追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.phase3_structure import normalize_heading


def test_introduction_not_stripped():
    """BUG-002: Introduction の先頭 'I' がローマ数字として除去されない"""
    assert normalize_heading("Introduction") == "introduction"


def test_introduction_uppercase():
    """大文字全体がローマ数字として吸われないか確認"""
    assert normalize_heading("INTRODUCTION") == "introduction"


def test_conclusions_not_stripped():
    """Conclusions の 'C' が IVXLCDM に含まれないので壊れない（回帰テスト）"""
    assert normalize_heading("Conclusions") == "conclusions"


def test_roman_numeral_removed():
    """ローマ数字 + ピリオド付き見出しの正規化"""
    assert normalize_heading("III. Some Heading") == "some heading"


def test_chapter_prefix_removed():
    """Chapter prefix の除去"""
    assert normalize_heading("Chapter 3: The Result") == "the result"


def test_numbered_prefix_removed():
    """数字 prefix の除去"""
    assert normalize_heading("1.2. Methods") == "methods"


def test_part_heading():
    """PART + ローマ数字: PART は Chapter パターンに含まれないのでそのまま残る"""
    result = normalize_heading("PART IV")
    # normalize_heading の正規表現は ^(?:Chapter\s+)? なので PART は除去されない
    # IV も PART に続くため、先頭パターンにマッチしない
    assert result == "part iv"


def test_empty_input():
    """空文字列"""
    assert normalize_heading("") == ""


def test_plain_text():
    """プレーン テキスト（変換不要）"""
    assert normalize_heading("Methods and Data") == "methods and data"


if __name__ == "__main__":
    # 簡易実行
    tests = [
        test_introduction_not_stripped,
        test_introduction_uppercase,
        test_conclusions_not_stripped,
        test_roman_numeral_removed,
        test_chapter_prefix_removed,
        test_numbered_prefix_removed,
        test_part_heading,
        test_empty_input,
        test_plain_text,
    ]
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
        except AssertionError as e:
            print(f"  ✗ {test.__name__}: {e}")
    print(f"\n{len(tests)} tests completed.")
