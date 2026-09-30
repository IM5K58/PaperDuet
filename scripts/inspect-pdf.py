"""Read-only PDF diagnostics for the M1 golden document."""
import json
from pathlib import Path
import pymupdf as fitz

root = Path(__file__).resolve().parent.parent
doc = fitz.open(root / 'artifacts/m1/rex-omni.pdf')
out = root / 'artifacts/m1'
print('pages', len(doc), 'toc', doc.get_toc()[:10])
pages = []
for page in doc:
    blocks = page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
    rows = []
    for b in blocks:
        rows.append({'bbox': b['bbox'], 'lines': [{'bbox': l['bbox'], 'text': ''.join(s['text'] for s in l['spans']), 'size': max(s['size'] for s in l['spans']), 'font': l['spans'][0]['font']} for l in b.get('lines', [])]})
    tables = page.find_tables()
    pages.append({'page': page.number+1, 'blocks': rows, 'tables': [{'bbox': t.bbox, 'rows': t.extract(), 'cells': t.cells} for t in tables.tables], 'images': [i['bbox'] for i in page.get_image_info()]})
    if page.number < 31:
        print(page.number+1, 'tables', [(t.row_count, t.col_count) for t in tables.tables], 'captions', [l['text'][:90] for b in rows for l in b['lines'] if l['text'].startswith(('Table ', 'Figure '))])
(out / 'pdf-layout.json').write_text(json.dumps(pages, ensure_ascii=False), encoding='utf-8')
for index in [0, 4, 9, 13]:
    doc[index].get_pixmap(matrix=fitz.Matrix(1.3, 1.3)).save(out / f'page-{index+1}.png')
