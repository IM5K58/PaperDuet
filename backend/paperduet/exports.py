"""Offline document exports; only document content, never settings or threads."""
import base64
import html
import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup,NavigableString
from .adapter import inline,plain

# A dollar followed by a digit belongs to a price, not a closing math delimiter.
EXPORT_MATH=re.compile(r'(?<!\\)\\\([\s\S]*?(?<!\\)\\\)|(?<!\\)\\\[[\s\S]*?(?<!\\)\\\]|(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<![\\$])\$(?![\s$])(?:\\.|[^\\$\n])+?(?<![\s\\])\$(?![$\d])')


def markdown_text(value):
    """Escape literal source text rather than allowing it to become Markdown."""
    value=html.escape(value,quote=False)
    value=re.sub(r'([\\`*_{}\[\]()#!|~$+\-])',r'\\\1',value)
    return re.sub(r'(?m)^(\s*\d+)\.',r'\1\\.',value)


def markdown_inline(source):
    soup=BeautifulSoup(inline(source),'html.parser')
    def text(value,code=False):
        if code:return markdown_text(value)
        parts=[];offset=0
        for match in EXPORT_MATH.finditer(value):
            parts.append(markdown_text(value[offset:match.start()]))
            expression=match[0];single=expression.startswith('$') and not expression.startswith('$$')
            latex=expression[1:-1] if single else expression[2:-2]
            # Raw HTML-looking notation must remain inert even in a Markdown
            # viewer without math support. These commands work in math and text.
            latex=latex.replace('<',r'\textless{}').replace('>',r'\textgreater{}')
            delimiter='$'*max(1,max((len(run) for run in re.findall(r'\$+',latex)),default=0)+1)
            if expression.startswith(('\\[','$$')):
                delimiter=delimiter if len(delimiter)>=2 else '$$'
                parts.append('\n\n'+delimiter+'\n'+latex+'\n'+delimiter+'\n\n')
            else:parts.append(delimiter+latex+delimiter)
            offset=match.end()
        parts.append(markdown_text(value[offset:]))
        return ''.join(parts)
    def render(node,code=False):
        if isinstance(node,NavigableString):return text(str(node),code)
        if node.name=='br':return '<br/>'
        children=''.join(render(child,code or node.name=='code') for child in node.children)
        return f'<{node.name}>{children}</{node.name}>'
    return ''.join(render(node) for node in soup.contents)


def fenced_text(value):
    delimiter='`'*max(3,max((len(run) for run in re.findall(r'`+',value)),default=0)+1)
    return delimiter+'text\n'+value+'\n'+delimiter


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
                text=inline(cell.text_en)
                if cell.text_ko and cell.text_ko!=cell.text_en:text+='<br>'+inline(cell.text_ko)
                cells.append(f'<{tag} colspan="{cell.colspan}" rowspan="{cell.rowspan}">{text}</{tag}>')
            rows.append('<tr>'+''.join(cells)+'</tr>')
    return '<table>\n'+'\n'.join(rows)+'\n</table>'

def block_markdown(block):
    b=block; parts=[]
    if b.superseded_by:
        previous=b.model_copy(update={'superseded_by':None})
        return '**이전 원문 · 기존 번역** · [새 원문으로 이동](#'+markdown_text(b.superseded_by)+')\n\n'+block_markdown(previous)
    if b.type in {'sec','sub','ssub'}:
        return '#'*({'sec':2,'sub':3,'ssub':4}[b.type])+' '+markdown_inline((b.n or '')+' '+(b.en or b.ko or ''))+ ('\n\n'+markdown_inline(b.ko) if b.ko and b.ko!=b.en else '')
    if b.note:
        n=b.note;return f'### [{n.kind}] {markdown_inline(n.title)}\n\n{n.body_md}\n\n주장: {n.claim} · 근거: '+', '.join(f'[{r}](#{r})' for r in n.refs)
    if b.ocr:
        parts.append('**OCR로 읽은 원문** · 숫자·기호·수식과 읽기 순서를 원본 PDF와 대조하세요.')
    unverified=b.restore_error and b.type in {'tab','eq'}
    if unverified:
        kind='표' if b.type=='tab' else '수식'
        parts.append(f'{kind} 복원 결과를 확인하지 못했습니다. '+('원본 이미지를 확인하세요.' if b.image_path else '추출한 원문을 표시합니다.'))
    else:
        if b.latex:parts.append(markdown_inline(r'\['+b.latex+r'\]'))
        if b.table:parts.append(table_html(b.table))
    if b.card:parts.extend([fenced_text(b.card.body),markdown_inline(b.card.explain_ko)])
    for label,value in [('EN',b.en),('KO',b.ko),('Caption EN',b.caption_en),('Caption KO',b.caption_ko)]:
        if unverified and label=='EN':label='추출 원문'
        if value:parts.append(f'**{label}**\n\n'+markdown_inline(value))
    return '\n\n'.join(parts)

def markdown_export(store,doc):
    parts=[f'# {markdown_inline(doc.title)}',markdown_inline(doc.title_ko),'개인 학습용 내보내기 · 공유 시 원문의 이용 조건을 확인하세요.']
    for b in doc.blocks:
        parts.extend([f'<a id="{html.escape(b.id,quote=True)}"></a>',block_markdown(b)])
        image=image_data(store,doc.id,b.image_path)
        if image:parts.append(f'![{markdown_text(plain(b.n or b.id))}]({image})')
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
    parts=[f'# {plain(doc.title)} — 메모장','이 논문에 저장한 나의 메모입니다.']
    for i,n in enumerate(notes,1):
        parts.extend([f'## {i}. {n["title"]}',n['body_md'],f'근거 블록: {n["block_id"] or "직접 작성"}'])
    return '\n\n'.join(parts)+'\n'
