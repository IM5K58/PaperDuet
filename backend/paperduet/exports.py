"""Offline document exports; only document content, never settings or threads."""
import base64
import html
import json
import re
import sys
from pathlib import Path
from .adapter import plain

def image_data(store,doc_id,path):
    asset=store.asset_path(doc_id,path) if path else None
    if not asset or asset.suffix!='.png':return None
    return 'data:image/png;base64,'+base64.b64encode(asset.read_bytes()).decode('ascii')

def table_html(table):
    rows=[]
    for group in [table.header,table.body]:
        for row in group:
            cells=[]
            for cell in row:
                tag='th' if cell.is_header else 'td'
                text=html.escape(plain(cell.text_en))
                if cell.text_ko and cell.text_ko!=cell.text_en:text+='<br>'+html.escape(plain(cell.text_ko))
                cells.append(f'<{tag} colspan="{cell.colspan}" rowspan="{cell.rowspan}">{text}</{tag}>')
            rows.append('<tr>'+''.join(cells)+'</tr>')
    return '<table>\n'+'\n'.join(rows)+'\n</table>'

def block_markdown(block):
    b=block; parts=[]
    if b.type in {'sec','sub','ssub'}:
        return '#'*({'sec':2,'sub':3,'ssub':4}[b.type])+' '+plain((b.n or '')+' '+(b.en or b.ko or ''))+ ('\n\n'+plain(b.ko) if b.ko and b.ko!=b.en else '')
    if b.note:
        n=b.note;return f'### [{n.kind}] {plain(n.title)}\n\n{n.body_md}\n\n주장: {n.claim} · 근거: '+', '.join(f'[{r}](#{r})' for r in n.refs)
    if b.latex:parts.append('$$\n'+b.latex+'\n$$')
    if b.table:parts.append(table_html(b.table))
    if b.card:parts.extend(['```text\n'+b.card.body.replace('```','` ` `')+'\n```',b.card.explain_ko])
    for label,value in [('EN',b.en),('KO',b.ko),('Caption EN',b.caption_en),('Caption KO',b.caption_ko)]:
        if value:parts.append(f'**{label}**\n\n'+plain(value))
    return '\n\n'.join(parts)

def markdown_export(store,doc):
    parts=[f'# {plain(doc.title)}',doc.title_ko,'개인 학습용 내보내기 · 공유 시 원문의 이용 조건을 확인하세요.']
    for b in doc.blocks:
        parts.extend([f'<a id="{html.escape(b.id,quote=True)}"></a>',block_markdown(b)])
        image=image_data(store,doc.id,b.image_path)
        if image:parts.append(f'![{plain(b.n or b.id)}]({image})')
    return '\n\n'.join(filter(None,parts))+'\n'

def html_export(store,doc,redact):
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    template=(root/'resources/export-template.html').read_text(encoding='utf-8')
    data=doc.model_dump();images={}
    data['reading_position']={'block_id':None,'offset':0}
    for b in data['blocks']:
        image=image_data(store,doc.id,b.get('image_path'))
        if image:images[b['id']]=image;b['image_path']='embedded'
        else:b['image_path']=None
    payload=json.dumps({'document':data,'images':images},ensure_ascii=False)
    payload=redact(payload).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return template.replace('__PAPERDUET_DATA__',payload,1)

def presentation_export(store,doc):
    with store.connect() as db:notes=[dict(r) for r in db.execute('SELECT * FROM presentation_notes WHERE doc_id=? ORDER BY sort_order,id',(doc.id,))]
    parts=[f'# {plain(doc.title)} — 발표 노트','논문 명시·해석·계산값을 구분하고 발표 전에 원문과 수치를 확인하세요.']
    for i,n in enumerate(notes,1):
        parts.extend([f'## {i}. {n["title"]}',n['body_md'],f'근거 블록: {n["block_id"] or "직접 작성"}'])
    return '\n\n'.join(parts)+'\n'
