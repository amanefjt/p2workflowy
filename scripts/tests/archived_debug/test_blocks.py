import fitz
import sys

doc = fitz.open("data/Booksample/pse/psdpdf.pdf")
page = doc[14] # book p9
for b in page.get_text("blocks"):
    print("--- BLOCK ---")
    print(repr(b[4]))
