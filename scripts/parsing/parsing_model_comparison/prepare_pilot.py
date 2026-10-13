from inspect_inputs import ROOT, PDF
from prepare_document import prepare_document
import pypdfium2 as pdfium
from common import write_json

prepared = prepare_document(PDF, ROOT/'prepared', None, 4000, force=True)
write_json(ROOT/'prepared_all.json', prepared)
for chunk in prepared['chunks']:
    print(chunk['chunk_id'],chunk['pages'])
    for block in chunk['blocks']:
        print(' ',block['block_id'],len(block['text']),repr(block['text'][:160]))
doc = pdfium.PdfDocument(str(PDF))
for n in (1,2,4):
    page = doc[n]
    bitmap = page.render(scale=1.3)
    bitmap.to_pil().save(ROOT/f'page_{n+1}.png')
    bitmap.close()
    page.close()
doc.close()
