import fitz
import re
import statistics

def _detect_repeating_elements(doc):
    patterns = set()
    for page in doc:
        blocks = page.get_text("blocks")
        ph = page.rect.height
        for b in blocks:
            txt = b[4].strip()
            if not txt: continue
            y1 = b[3]
            if y1 <= ph * 0.15 or b[1] >= ph * 0.85:
                norm = re.sub(r'\d+', '', txt).strip()
                if len(norm) > 3:
                     patterns.add(norm)
    # Actually, the real logic is more complex (threshold based)
    return patterns

doc = fitz.open("data/Booksample/pse/psdpdf.pdf")
ignored = {"The Ethnographic Effect I", "Preface"} # Mocking for test
page = doc[5] # p6
ph = page.rect.height
limit_top = ph * 0.15
limit_bottom = ph * 0.85

print(f"Page Height: {ph}, Top Limit: {limit_top:.1f}")

for b in page.get_text("blocks"):
    y0, y1 = b[1], b[3]
    txt = b[4].strip().replace("\n", " ")
    if not txt: continue
    norm = re.sub(r'\d+', '', txt).strip()
    is_near = (y1 <= limit_top or y0 >= limit_bottom)
    skip = (norm in ignored and is_near)
    print(f"[{'SKIP' if skip else 'KEEP'}] Y:{y0:.1f}-{y1:.1f} Near:{is_near} Text: {repr(txt)}")

