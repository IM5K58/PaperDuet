"""Derive display metadata only. Never alter table text, numbers, or spans."""
import re
from decimal import Decimal, InvalidOperation

from .adapter import plain
from .annotations import source_text
from .validation import table_grid


def identify_models(blocks):
    models=set()
    for b in blocks:
        # Intro subsections are included; later experiments cannot identify ours.
        if not b.section_path or b.section_path[0].casefold() not in {'abstract','1','introduction'}:continue
        for m in re.finditer(r'\b(?i:we\s+(?:(?:here|thus|also)\s+)?(?:introduce|present|propose|develop)|and\s+propose)\s+(?:a\s+novel\s+model\s+(?:called|named)\s+)?([A-Z][\w.-]*(?:[ \t]+[A-Z][\w.-]*){0,2})',source_text(b)):
            name=m.group(1).strip('., ')
            if name.casefold() not in {'a','an','the','our','this'}:models.add(name)
    return sorted(models)


def decorate_table(table,models):
    try:grid,width=table_grid(table)
    except ValueError:return
    table.highlight_rows=[];table.best_cells=[]
    nh=len(table.header)
    # Reconstruct logical columns so rowspans cannot shift maxima to other metrics.
    occupied=set();origins={}
    for r,row in enumerate(table.header+table.body):
        col=0
        for ci,c in enumerate(row):
            while (r,col) in occupied:col+=1
            origins[r,col]=(ci,c)
            occupied.update((rr,cc) for rr in range(r,r+c.rowspan) for cc in range(col,col+c.colspan))
            col+=c.colspan
    for ri,row in enumerate(table.body):
        texts=' '.join(plain(c.text_en) for c in row if not c.numeric)
        if any(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',texts,re.I) for name in models):table.highlight_rows.append(ri)
    for col in range(width):
        headers=' '.join(plain(grid.get((r,col),'')) for r in range(nh))
        # Hyperparameters, identifiers, size and time are not score maxima.
        if re.search(r'thresh|param|size|epoch|batch|step|time|latency|flops|#|↓|loss|error',headers,re.I):continue
        values=[]
        for ri in range(len(table.body)):
            pair=origins.get((ri+nh,col))
            if not pair:continue
            ci,c=pair;text=plain(c.text_en).strip().replace('−','-')
            if c.is_header or c.colspan!=1 or c.rowspan!=1 or not re.fullmatch(r'[+-]?\d+(?:\.\d+)?%?',text):continue
            try:values.append((Decimal(text.rstrip('%')),ri,ci,text.endswith('%')))
            except InvalidOperation:pass
        if len(values)<2 or len({v[3] for v in values})!=1:continue
        maximum=max(v[0] for v in values)
        table.best_cells.extend((r,c) for value,r,c,_ in values if value==maximum)


def decorate_tables(blocks):
    models=identify_models(blocks)
    for b in blocks:
        if b.table:decorate_table(b.table,models)
    return models
