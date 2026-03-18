import sys
from pathlib import Path

# プロジェクトルートを追加
sys.path.insert(0, str(Path.cwd()))

from core.pdf_ingester import diagnose_pdf_quality, SYMBOL_DENSITY_THRESHOLD, FRAGMENT_RATIO_THRESHOLD

def test_diagnostic():
    pdf_path = "data/Booksample/pse/psdpdf.pdf"
    print(f"Testing PDF: {pdf_path}")
    
    # 1. 正常なPDFのテスト
    is_clean = diagnose_pdf_quality(pdf_path)
    print(f"Result for clean PDF: {is_clean}")
    
    # 2. 内部関数のロジックをシミュレーション（テキストベース）
    import re
    from core.pdf_ingester import COMMON_WORDS_WL
    
    def check_text(text):
        print(f"\nChecking text snippet: {text[:50]}...")
        # 指標A
        symbols = re.findall(r'[~|^\\_<{}\[\]]', text)
        density = len(symbols) / len(text)
        print(f"Symbol density: {density:.2%} (Threshold: {SYMBOL_DENSITY_THRESHOLD:.2%})")
        
        # 指標B
        all_words = re.findall(r'\b[a-zA-Z]+\b', text)
        lowercase_words = [w for w in all_words if w[0].islower()]
        fragments = [w for w in lowercase_words if len(w) <= 2 and w not in COMMON_WORDS_WL]
        
        if lowercase_words:
            frag_ratio = len(fragments) / len(lowercase_words)
            print(f"Fragment ratio: {frag_ratio:.2%} (Threshold: {FRAGMENT_RATIO_THRESHOLD:.2%})")
        else:
            print("No lowercase words found.")

    # 破損テキスト例（記号過多）
    bad_text_symbols = "This is a test with too many symbols: ~|^\\_~|^\\_~|^\\_~|^\\_~|^\\_~|^\\_~|^\\_~|^\\_~|^\\_."
    check_text(bad_text_symbols)
    
    # 破損テキスト例（断片化過多）
    bad_text_fragments = "h ns ti h ns ti h ns ti h ns ti h ns ti h ns ti h ns ti h ns ti h ns ti we me us by am vs."
    check_text(bad_text_fragments)
    
    # 正常なテキスト例（固有名詞 Hagen/Etoro 混み）
    good_text = "Hagen and Etoro are examples of proper nouns. We are using these in our anthropologists' research about society. The relation between persons is key."
    check_text(good_text)

if __name__ == "__main__":
    test_diagnostic()
