"""Run the production parser on the independent arXiv PDF, never on fixture data."""
import json
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'backend'))
from paperduet.pdf_parser import PyMuPDFParser
from paperduet.validation import table_grid

root=Path(__file__).resolve().parent.parent
target=root/'artifacts/m1/golden'
target.mkdir(parents=True,exist_ok=True)
source=root/'artifacts/m1/rex-omni.pdf'
parser=PyMuPDFParser()
metadata=parser.metadata(source)
pages=[]
for i in range(metadata['page_count']):
    page=parser.page(source,i,target/'crops')
    pages.append(page)
    print(i+1,Counter(item['type'] for item in page),flush=True)
blocks,provenance=parser.structure('golden',pages,metadata)
(target/'blocks.json').write_text(json.dumps([b.model_dump(exclude_none=True) for b in blocks],ensure_ascii=False,indent=2),encoding='utf-8')
(target/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False),encoding='utf-8')
summary={'pages':metadata['page_count'],'counts_first_31':dict(Counter(b.type for b in blocks if b.page<=31)),'headings':[{'n':b.n,'en':b.en,'page':b.page} for b in blocks if b.type in {'sec','sub','ssub'}]}
summary['tables']=[]
for b in blocks:
    if b.type!='tab':continue
    try: cols=table_grid(b.table)[1] if b.table else None
    except ValueError:cols='invalid'
    summary['tables'].append({'n':b.n,'columns':cols,'rows':len(b.table.body) if b.table else 0})
(target/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
