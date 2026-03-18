import fitz
import re
from core.pdf_ingester import extract_text_fast, _detect_repeating_elements

doc = fitz.open("data/Booksample/pse/psdpdf.pdf")
ignored = _detect_repeating_elements(doc)
print(f"Ignored patterns: {ignored}")
page = doc[9] # p9
text = extract_text_fast(page, ignored_patterns=ignored)
# print(text)
import json
print("The Ethnographic Effect I 9" in text)
