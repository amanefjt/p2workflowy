import fitz

doc = fitz.open("psdpdf.pdf")
doc_new = fitz.open()
doc_new.insert_pdf(doc, from_page=0, to_page=29)
doc_new.save("psdpdf_30.pdf")
