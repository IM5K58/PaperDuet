"""arXiv HTML-first importer. Fixed HTTPS hosts; never execute downloaded content."""
import asyncio
import hashlib
import html
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin,urlparse,unquote
import httpx
from bs4 import BeautifulSoup,Tag,NavigableString
from .adapter import inline,plain,parse_table
from .models import Block
from .pipeline import atomic_json
from .table_style import decorate_tables

ID=re.compile(r'^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v[1-9]\d*)?$')

def arxiv_id(value):
    value=value.strip()
    if value.startswith(('http://','https://')):
        u=urlparse(value)
        if u.scheme!='https' or u.hostname not in {'arxiv.org','www.arxiv.org','export.arxiv.org'} or u.username or u.password or u.port not in {None,443}:raise ValueError('ARXIV_INVALID')
        value=re.sub(r'^/(abs|pdf|html|e-print)/','',u.path)
    value=re.sub(r'\.pdf$','',value)
    if not ID.fullmatch(value):raise ValueError('ARXIV_INVALID')
    return value

def content(node):
    clone=BeautifulSoup(str(node),'html.parser')
    for bad in clone.select('script,style,nav,.ltx_note,.ltx_ERROR'):bad.decompose()
    for math in clone.find_all('math'):
        latex=math.get('alttext') or (math.find('annotation').get_text() if math.find('annotation') else math.get_text())
        math.replace_with(NavigableString(r'\('+latex+r'\)'))
    for bold in clone.select('.ltx_font_bold'):bold.name='b'
    return inline(clone.decode_contents()).strip()

def parse_html(doc_id,source):
    soup=BeautifulSoup(source,'html.parser');article=soup.select_one('article.ltx_document')
    if article is None:raise ValueError('ARXIV_HTML_UNAVAILABLE')
    title=article.select_one('.ltx_title_document');authors=article.select_one('.ltx_authors')
    meta={'title':title.get_text(' ',strip=True) if title else 'arXiv paper','authors':authors.get_text(' ',strip=True) if authors else ''}
    blocks=[];sources=[];images={};path=[]
    def add(kind,node,**values):
        b=Block(id=f'b{len(blocks):04d}',doc_id=doc_id,order=len(blocks),type=kind,section_path=list(path),**values)
        blocks.append(b);sources.append({'bbox':[0,0,0,0],'region':None,'references':any(p in {'References','Bibliography'} for p in path),'needs_restore':False,'origin':'arxiv_html','source_anchor':node.get('id')})
        return b
    def walk(node):
        nonlocal path
        for child in node.children:
            if not isinstance(child,Tag):continue
            classes=child.get('class',[])
            if child.name in {'script','style','nav'} or any(c in classes for c in ['ltx_authors','ltx_title_document','ltx_date','ltx_note','ltx_page_footer']):continue
            if 'ltx_title' in classes:
                text=child.get_text(' ',strip=True)
                if not any(c in classes for c in ('ltx_title_abstract','ltx_title_bibliography','ltx_title_section','ltx_title_subsection','ltx_title_subsubsection')):
                    # Paragraph/run-in titles belong to the current subsection.
                    # They must not reset the TOC or AI section context.
                    if text:add('p',child,en='<b>'+html.escape(text)+'</b>')
                    continue
                if 'ltx_title_abstract' in classes:level=0;number='Abstract';text='Abstract'
                elif 'ltx_title_bibliography' in classes:level=0;number='References';text='References'
                else:
                    level=2 if 'ltx_title_subsubsection' in classes else 1 if 'ltx_title_subsection' in classes else 0
                    tag=child.select_one('.ltx_tag');number=tag.get_text(' ',strip=True).strip('.') if tag else ''
                    if number and text.startswith(tag.get_text(' ',strip=True)):text=text[len(tag.get_text(' ',strip=True)):].strip()
                path=path[:level]+[number or text];add(['sec','sub','ssub'][level],child,n=number or None,en=html.escape(text));continue
            if 'ltx_equation' in classes or 'ltx_equationgroup' in classes:
                maths=child.find_all('math');latex=[m.get('alttext') for m in maths if m.get('alttext')]
                tag=child.select_one('.ltx_tag_equation,.ltx_tag_equationgroup');number=tag.get_text(strip=True) if tag else None
                rows=child.select('tr.ltx_eqn_row')
                lines=[' '.join(m.get('alttext','') for m in row.find_all('math')) for row in rows]
                expression=(r'\begin{aligned}'+r'\\'.join(lines)+r'\end{aligned}') if len(lines)>1 else ' '.join(latex)
                add('eq',child,n=number,latex=expression or None,en=' '.join(latex));continue
            if child.name=='figure':
                if child.find('figure') and not child.find('figcaption',recursive=False):walk(child);continue
                caption=child.find('figcaption');tag=child.select_one('.ltx_tag_table,.ltx_tag_figure')
                is_table='ltx_table' in classes or bool(tag and re.match(r'Table\s',tag.get_text(' ',strip=True)))
                number=tag.get_text(' ',strip=True).rstrip(': .') if tag else None
                table=child.select_one('table.ltx_tabular') if is_table else None
                values={'n':number,'caption_en':content(caption) if caption else ''}
                if table:
                    clean=BeautifulSoup(str(table),'html.parser')
                    for math in clean.find_all('math'):
                        latex=math.get('alttext') or math.get_text()
                        math.replace_with(NavigableString(latex if re.fullmatch(r'[−+\-]?\d+(?:\.\d+)?%?',latex.strip()) else r'\('+latex+r'\)'))
                    parsed=parse_table(str(clean))
                    # LaTeXML tables often encode header cells as td. Preserve
                    # all spans and move leading non-score rows into the header.
                    while parsed.body and not any(c.numeric or re.search(r'\d+\.\d+',plain(c.text_en)) for c in parsed.body[0]):
                        row=parsed.body.pop(0)
                        for c in row:c.is_header=True
                        parsed.header.append(row)
                    values['table']=parsed
                b=add('tab' if is_table else 'fig',child,**values)
                if not is_table:images[b.id]=[img.get('src') for img in child.select('img.ltx_graphics') if img.get('src')]
                continue
            if child.name in {'p','pre'} or 'ltx_bibitem' in classes:
                text=content(child)
                if text:add('p',child,en=text)
                continue
            walk(child)
    walk(article)
    if not blocks or not any(b.type=='p' for b in blocks):raise ValueError('ARXIV_HTML_UNAVAILABLE')
    decorate_tables(blocks)
    return meta,blocks,sources,images

class ArxivImporter:
    def __init__(self,store,pipeline,transport=None):self.store,self.pipeline,self.transport=store,pipeline,transport;self.lock=asyncio.Lock();self.last=0

    async def fetch(self,client,url,limit,optional=False):
        for _ in range(4):
            u=urlparse(url)
            if u.scheme!='https' or u.hostname not in {'arxiv.org','export.arxiv.org'} or u.port not in {None,443} or u.username:raise ValueError('ARXIV_DOWNLOAD_FAILED')
            if self.transport is None:
                await asyncio.sleep(max(0,3-(time.monotonic()-self.last)))
            self.last=time.monotonic()
            async with client.stream('GET',url) as r:
                if r.status_code in {301,302,303,307,308}:url=urljoin(url,r.headers['location']);continue
                if optional and r.status_code in {404,406,500,503}:return None
                if r.status_code!=200:raise ValueError('ARXIV_DOWNLOAD_FAILED')
                data=bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>limit:raise ValueError('ARXIV_TOO_LARGE')
                return bytes(data)
        raise ValueError('ARXIV_DOWNLOAD_FAILED')

    async def ingest(self,value):
        identifier=arxiv_id(value)
        async with self.lock:
            doc_id='arxiv-'+identifier.replace('/','-')
            if self.store.document(doc_id):return {'doc_id':doc_id,'duplicate':True}
            stage=self.store.data_dir/'incoming'/('arxiv-'+uuid.uuid4().hex);stage.mkdir(parents=True)
            try:
                async with httpx.AsyncClient(timeout=60,trust_env=False,follow_redirects=False,transport=self.transport,headers={'User-Agent':'PaperDuet/0.5 (local personal research reader)'}) as client:
                    raw=await self.fetch(client,f'https://arxiv.org/html/{identifier}',12*1024*1024,True)
                    parsed=None
                    if raw:
                        try:parsed=parse_html(doc_id,raw.decode('utf-8'))
                        except (ValueError,UnicodeError):pass
                    pdf=await self.fetch(client,f'https://arxiv.org/pdf/{identifier}',150*1024*1024)
                    if not pdf.startswith(b'%PDF-'):raise ValueError('ARXIV_DOWNLOAD_FAILED')
                    if not parsed:
                        with self.store.connect() as db:existing=db.execute("SELECT id FROM documents WHERE file_hash=? AND source_kind='pdf'",(hashlib.sha256(pdf).hexdigest(),)).fetchone()
                        if existing:return {'doc_id':existing[0],'duplicate':True,'fallback':True}
                    (stage/'source.pdf').write_bytes(pdf)
                    meta=await asyncio.to_thread(self.pipeline.parser.metadata,stage/'source.pdf');meta['arxiv_id']=identifier
                    if parsed:
                        metadata,blocks,sources,images=parsed;meta.update(metadata)
                        (stage/'source.html').write_bytes(raw)
                        (stage/'crops').mkdir()
                        for bid,urls in images.items():
                            rasters=[]
                            for src in urls[:16]:
                                url=urljoin(f'https://arxiv.org/html/{identifier}',src)
                                prefix=re.escape(identifier if re.search(r'v\d+$',identifier) else identifier)+('' if re.search(r'v\d+$',identifier) else r'(?:v[1-9]\d*)?')
                                if not re.match(r'^/html/'+prefix+'/',unquote(urlparse(url).path)):continue
                                image=await self.fetch(client,url,10*1024*1024,True)
                                if image:rasters.append(image)
                            block=next(b for b in blocks if b.id==bid)
                            if rasters:
                                try:
                                    await asyncio.to_thread(raster_figure,rasters,stage/'crops'/f'{bid}.png')
                                    block.image_path=f'documents/{doc_id}/crops/{bid}.png'
                                except Exception:block.qa_flags.append('EXTRACT_REVIEW')
                            else:block.qa_flags.append('EXTRACT_REVIEW')
                    atomic_json(stage/'metadata.json',meta)
                    target=self.store.data_dir/'documents'/doc_id;target.parent.mkdir(exist_ok=True)
                    if target.exists():raise ValueError('ARXIV_ALREADY_EXISTS')
                    stage.rename(target)
                checkpoint={'ai_approved':False,'parser_revision':2,'source_kind':'arxiv_html' if parsed else 'pdf','options':self.store.pipeline_options().model_dump(),'completed_stages':['Ingest','Extract','Structure'] if parsed else ['Ingest'],'extracted_pages':meta['page_count'] if parsed else 0,'translated':[],'restored':[],'error':None}
                status='awaiting_ai' if parsed else 'queued'
                with self.store.connect() as db:
                    db.execute('INSERT INTO documents(id,title,arxiv_id,file_hash,source_path,status,page_count,authors,source_kind,source_url) VALUES(?,?,?,?,?,?,?,?,?,?)',(doc_id,meta['title'],identifier,hashlib.sha256(pdf).hexdigest(),f'documents/{doc_id}/source.pdf',status,meta['page_count'],meta['authors'],'arxiv_html' if parsed else 'pdf',f'https://arxiv.org/abs/{identifier}'))
                    db.execute('INSERT INTO jobs(id,doc_id,stage,status,progress,checkpoint) VALUES(?,?,?,?,?,?)',(doc_id,doc_id,'Glossary' if parsed else 'Extract',status,.3 if parsed else 0,json.dumps(checkpoint)))
                    if parsed:
                        for b,s in zip(blocks,sources):
                            db.execute('INSERT INTO blocks VALUES(?,?,?,?,?,?)',(b.id,doc_id,b.order,b.type,json.dumps(b.section_path),b.model_dump_json(exclude_none=True)))
                            db.execute('INSERT INTO source_blocks VALUES(?,?,?,?)',(doc_id,b.id,b.model_dump_json(exclude_none=True),json.dumps(s)))
                if not parsed:self.pipeline.start(doc_id)
                return {'doc_id':doc_id,'duplicate':False,'source_kind':checkpoint['source_kind'],'fallback':not bool(parsed)}
            except httpx.HTTPError:raise ValueError('ARXIV_DOWNLOAD_FAILED') from None
            finally:
                if stage.exists():shutil.rmtree(stage)

def raster_figure(images,path):
    import pymupdf as fitz
    from .pdf_parser import PDF_LOCK
    with PDF_LOCK:
        pictures=[fitz.Pixmap(data) for data in images]
        if sum(p.width*p.height for p in pictures)>40_000_000:raise ValueError('Image too large')
        with fitz.open() as pdf:
            width=max(p.width for p in pictures);height=sum(p.height for p in pictures)
            page=pdf.new_page(width=width,height=height);y=0
            for pix in pictures:
                page.insert_image(fitz.Rect(0,y,pix.width,y+pix.height),pixmap=pix);y+=pix.height
            page.get_pixmap(matrix=fitz.Matrix(min(1,1800/width),min(1,1800/width)),alpha=False).save(path)
