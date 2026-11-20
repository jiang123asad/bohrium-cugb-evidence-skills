"""Generate the two-column demo PDF (requires pymupdf). Example only, not a contest case."""
import pymupdf
doc = pymupdf.open()
for pno in range(2):
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 40), 'Journal of Demo Studies  Vol. 1', fontsize=8)
    page.insert_text((72, 760), str(pno + 1), fontsize=8)
page = doc[0]
page.insert_text((72, 80), '1. Introduction', fontsize=12)
page.insert_text((72, 100), 'Layer A left column text begins here with', fontsize=9)
page.insert_text((72, 112), 'a sentence that continues on the next line.', fontsize=9)
page.insert_text((320, 100), 'Layer B right column text is separate and', fontsize=9)
page.insert_text((320, 112), 'must not be interleaved with the left one.', fontsize=9)
page.insert_text((72, 140), '1.1. Study area', fontsize=10)
page.insert_text((72, 158), 'The study area covers a small demo region.', fontsize=9)
page.insert_text((320, 140), 'The right column includes a second paragraph.', fontsize=9)
page.insert_text((320, 152), 'This paragraph remains after all left-column text.', fontsize=9)
page.insert_text((320, 176), 'E = m c2  (1)', fontsize=9)
page.insert_text((72, 200), 'Fig. 1. Demo layout of the sample page.', fontsize=8)
doc.save('sample-two-column.pdf')
print('saved sample-two-column.pdf')
