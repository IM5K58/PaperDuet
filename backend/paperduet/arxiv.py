"""arXiv HTML-first importer. Fixed HTTPS hosts; never execute downloaded content."""
import asyncio
import hashlib
import json
import re
import shutil
import time
import uuid
from urllib.parse import urljoin,urlparse,unquote
from xml.etree import ElementTree
import httpx
from bs4 import BeautifulSoup,Tag,NavigableString
from .adapter import inline,plain,parse_table
from .models import Block
from .pipeline import atomic_json,PARSER_REVISION
from .table_style import decorate_tables
from .validation import table_grid

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
    if clone.select_one('figure,table,.ltx_equation,.ltx_equationgroup'):
        raise ValueError('ARXIV_HTML_UNAVAILABLE')
    for bad in clone.select('script,style,nav,.ltx_note,.ltx_ERROR'):bad.decompose()
    for math in clone.find_all('math'):
        latex=math_latex(math)
        math.replace_with(NavigableString(r'\('+latex+r'\)'))
    for bold in clone.select('.ltx_font_bold'):bold.name='b'
    return inline(clone.decode_contents()).strip()

def math_latex(node):
    latex=node.get('alttext')
    if latex and latex.strip():return latex.strip()
    annotation=node.find('annotation',attrs={'encoding':re.compile(r'(?:application/x-tex|application/x-latex|text/x-tex)',re.I)})
    if annotation and annotation.get_text().strip():return annotation.get_text().strip()
    # Flattening presentation MathML loses fractions, indices and operators.
    # Let the PDF importer preserve the rendered equation instead.
    raise ValueError('ARXIV_HTML_UNAVAILABLE')

def caption_only(node,caption):
    if node is caption:return True
    if node.name not in {'figure','div','span'} or node.get('style') or (node.name!='figure' and node.get('class')):
        return False
    return all((not child.strip()) if isinstance(child,NavigableString) else caption_only(child,caption)
               for child in node.children)

def structured_table(node):
    clean=BeautifulSoup(str(node),'html.parser')
    if len(clean.find_all('table'))!=1:raise ValueError('ARXIV_HTML_UNAVAILABLE')
    for math in clean.find_all('math'):
        latex=math_latex(math)
        math.replace_with(NavigableString(latex if re.fullmatch(r'[−+\-]?\d+(?:\.\d+)?%?',latex.strip()) else r'\('+latex+r'\)'))
    parsed=parse_table(str(clean))
    # Infer LaTeXML's td-based score headers, but do not turn every row of
    # a categorical table into a header or override an explicit thead.
    score=lambda row:any(c.numeric or re.search(r'\d+\.\d+',plain(c.text_en)) for c in row)
    has_score=any(score(row) for row in parsed.body)
    if not parsed.header:
        while parsed.body and not score(parsed.body[0]) and (has_score or all(c.is_header for c in parsed.body[0])):
            row=parsed.body.pop(0)
            for cell in row:cell.is_header=True
            parsed.header.append(row)
    table_grid(parsed)
    if any(cell.rowspan>len(parsed.header)-ri for ri,row in enumerate(parsed.header) for cell in row):
        raise ValueError('ARXIV_HTML_UNAVAILABLE')
    return parsed

def parse_html(doc_id,source,*,allow_visual_fallback=False):
    soup=BeautifulSoup(source,'html.parser');article=soup.select_one('article.ltx_document')
    if article is None:raise ValueError('ARXIV_HTML_UNAVAILABLE')
    # Inline graphics and embedded objects are otherwise stripped by content()
    # or skipped by walk(). Only the figure-image path below can preserve them.
    visuals=article.select('img,svg,object,embed,canvas,video,picture')
    for visual in visuals:
        if (visual.name!='img' or 'ltx_graphics' not in visual.get('class',[])
                or visual.find_parent('figure') is None or visual.find_parent(['figcaption','caption'])):
            owner=visual.find_parent('figure') or visual.find_parent('table')
            if (not allow_visual_fallback or owner is None or visual.find_parent(['figcaption','caption'])
                    or any(c in owner.get('class',[]) for c in ('ltx_equation','ltx_equationgroup'))):
                raise ValueError('ARXIV_HTML_UNAVAILABLE')
    title=article.select_one('.ltx_title_document');authors=article.select_one('.ltx_authors')
    meta={'title':plain(content(title)).strip() if title else 'arXiv paper','authors':authors.get_text(' ',strip=True) if authors else ''}
    blocks=[];sources=[];images={};path=[];handled_visuals=set()
    def add(kind,node,**values):
        b=Block(id=f'b{len(blocks):04d}',doc_id=doc_id,order=len(blocks),type=kind,section_path=list(path),**values)
        blocks.append(b);sources.append({'bbox':[0,0,0,0],'region':None,'references':any(p in {'References','Bibliography'} for p in path),'needs_restore':False,'origin':'arxiv_html','source_anchor':node.get('id')})
        return b
    def visual_metadata(node,captions):
        caption=captions[0] if captions else None
        tag=caption.select_one('.ltx_tag_table,.ltx_tag_figure') if caption else None
        text=plain(content(caption)).strip() if caption else ''
        label=tag.get_text(' ',strip=True).rstrip(': .') if tag else None
        if not label:
            match=re.match(r'^(Figure|Fig\.|Table)\s*(\d+[a-z]?)(?=[\s:.)]|$)',text,re.I)
            if match:label=('Table ' if match[1].lower()=='table' else 'Figure ')+match[2]
        kind='tab' if 'ltx_table' in node.get('class',[]) or bool(label and re.match(r'Table\s',label,re.I)) else 'fig'
        return caption,label,kind
    def placeholder(kind,node,caption,number):
        if not allow_visual_fallback:raise ValueError('ARXIV_HTML_UNAVAILABLE')
        captions=node.find_all('figcaption') if node.name=='figure' else node.find_all('caption')
        ordered=([caption] if caption else [])+[c for c in captions if c is not caption]
        texts=[content(c) for c in ordered]
        block=add(kind,node,n=number,caption_en='<br>'.join(texts))
        source=sources[-1]
        source.update(visual_fallback_reason='html_structure',visual_fallback_caption=content(caption) if caption else '',needs_restore=kind=='tab')
        urls=[img.get('src') for img in node.find_all('img') if img.get('src')]
        if urls:source['visual_source_urls']=urls
        panels=len(node.find_all('img'))
        if panels>1:source['visual_panel_count']=panels
        handled_visuals.update(id(v) for v in node.select('img,svg,object,embed,canvas,video,picture'))
        return block
    def walk(node):
        nonlocal path
        for child in node.children:
            if not isinstance(child,Tag):continue
            classes=child.get('class',[])
            if child.name in {'script','style','nav'} or any(c in classes for c in ['ltx_authors','ltx_title_document','ltx_date','ltx_note','ltx_page_footer']):continue
            if 'ltx_title' in classes:
                text=content(child)
                if not any(c in classes for c in ('ltx_title_abstract','ltx_title_bibliography','ltx_title_section','ltx_title_subsection','ltx_title_subsubsection')):
                    # Paragraph/run-in titles belong to the current subsection.
                    # They must not reset the TOC or AI section context.
                    if text:add('p',child,en='<b>'+text+'</b>')
                    continue
                if 'ltx_title_abstract' in classes:level=0;number='Abstract';text='Abstract'
                elif 'ltx_title_bibliography' in classes:level=0;number='References';text='References'
                else:
                    level=2 if 'ltx_title_subsubsection' in classes else 1 if 'ltx_title_subsection' in classes else 0
                    tag=child.select_one('.ltx_tag');number=tag.get_text(' ',strip=True).strip('.') if tag else ''
                    if tag:
                        heading=BeautifulSoup(str(child),'html.parser');heading.select_one('.ltx_tag').decompose()
                        text=content(heading)
                path=path[:level]+[number or plain(text)];add(['sec','sub','ssub'][level],child,n=number or None,en=text);continue
            if 'ltx_equation' in classes or 'ltx_equationgroup' in classes:
                maths=child.find_all('math');latex=[math_latex(m) for m in maths]
                if not latex:raise ValueError('ARXIV_HTML_UNAVAILABLE')
                tag=child.select_one('.ltx_tag_equation,.ltx_tag_equationgroup');number=tag.get_text(strip=True) if tag else None
                rows=child.select('tr.ltx_eqn_row')
                lines=[' '.join(math_latex(m) for m in row.find_all('math')) for row in rows]
                if any(not line.strip() for line in lines):raise ValueError('ARXIV_HTML_UNAVAILABLE')
                expression=(r'\begin{aligned}'+r'\\'.join(lines)+r'\end{aligned}') if len(lines)>1 else ' '.join(latex)
                add('eq',child,n=number,latex=expression or None,en=' '.join(latex));continue
            if child.name=='figure':
                captions=[c for c in child.find_all('figcaption') if c.find_parent('figure') is child]
                caption,number,kind=visual_metadata(child,captions)
                if child.find('figure') and not captions:
                    owned=child.select('img,svg,object,embed,canvas,video,picture,.ltx_graphics')
                    if any(v.find_parent('figure') is child for v in owned):
                        placeholder(kind,child,caption,number);continue
                    walk(child);continue
                # The block model cannot retain a panel grid and its subcaptions.
                # Preserve those layouts via the original PDF, rather than
                # choosing one caption and vertically stitching unrelated panels.
                graphics=child.select('img.ltx_graphics')
                if (child.find('figure') or len(captions)>1 or len(graphics)>1
                        or len(child.find_all('img'))!=len(graphics)
                        or child.select_one('svg,object,embed,canvas,video,picture')):
                    placeholder(kind,child,caption,number);continue
                is_table=kind=='tab'
                if allow_visual_fallback and not is_table:
                    remainder=BeautifulSoup(str(child),'html.parser')
                    for part in remainder.select('figcaption,img,svg,object,embed,canvas,video,picture,script,style,nav'):
                        part.decompose()
                    if remainder.get_text(strip=True) or remainder.select_one('table,math,.ltx_equation,.ltx_equationgroup'):
                        placeholder(kind,child,caption,number);continue
                table=child.select_one('table.ltx_tabular') if is_table else None
                if table and (graphics or len(child.select('table.ltx_tabular'))>1):
                    placeholder(kind,child,caption,number);continue
                values={'n':number,'caption_en':content(caption) if caption else ''}
                if table:
                    try:values['table']=structured_table(table)
                    except ValueError:
                        placeholder(kind,child,caption,number);continue
                    if not caption and table.find('caption'):values['caption_en']=content(table.find('caption'))
                urls=[img.get('src') for img in graphics if img.get('src')]
                if not table and not urls and not (caption and caption_only(child,caption)):
                    placeholder(kind,child,caption,number);continue
                b=add(kind,child,**values)
                if not table and urls:
                    images[b.id]=urls
                    sources[-1]['needs_restore']=is_table
                    sources[-1]['visual_source_urls']=urls
                handled_visuals.update(id(v) for v in child.select('img,svg,object,embed,canvas,video,picture'))
                continue
            if child.name=='table':
                caption=child.find('caption')
                _,number,_=visual_metadata(child,[caption] if caption else [])
                try:
                    if child.select_one('img,svg,object,embed,canvas,video,picture'):raise ValueError('ARXIV_HTML_UNAVAILABLE')
                    table=structured_table(child)
                except ValueError:
                    placeholder('tab',child,caption,number);continue
                add('tab',child,n=number,table=table,caption_en=content(caption) if caption else '')
                continue
            if child.name=='math':
                latex=math_latex(child)
                if child.get('display')=='block':add('eq',child,latex=latex,en=latex)
                else:add('p',child,en=content(child))
                continue
            if child.name in {'p','pre'} or 'ltx_bibitem' in classes:
                text=content(child)
                if text:add('p',child,en=text)
                continue
            walk(child)
    walk(article)
    if handled_visuals!={id(v) for v in visuals}:
        raise ValueError('ARXIV_HTML_UNAVAILABLE')
    if not blocks or not any(b.type=='p' for b in blocks):raise ValueError('ARXIV_HTML_UNAVAILABLE')
    decorate_tables(blocks)
    return meta,blocks,sources,images

def visual_asset_versions(identifier,sources):
    base=re.sub(r'v[1-9]\d*$','',identifier)
    prefix=re.compile(r'^/html/('+re.escape(base)+r'v[1-9]\d*)/')
    versions=set()
    for source in sources:
        for src in source.get('visual_source_urls',[]):
            url=urlparse(urljoin(f'https://arxiv.org/html/{identifier}',src))
            path=unquote(url.path)
            match=prefix.match(path)
            try:trusted=url.scheme=='https' and url.hostname in {'arxiv.org','export.arxiv.org'} and url.port in {None,443} and not url.username and not url.password
            except ValueError:trusted=False
            if trusted and match and '..' not in path.split('/'):
                versions.add(match[1])
    return versions

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
                    source_identifier=identifier
                    fallback_reason='html_unavailable' if not raw else 'html_structure'
                    if raw:
                        (stage/'source.html').write_bytes(raw)
                        try:parsed=parse_html(doc_id,raw.decode('utf-8'),allow_visual_fallback=True)
                        except (ValueError,UnicodeError):pass
                    if parsed:
                        versions=visual_asset_versions(identifier,parsed[2])
                        pinned=bool(re.search(r'v[1-9]\d*$',identifier))
                        if (pinned and versions-{identifier}) or (not pinned and len(versions)>1):
                            parsed=None
                            fallback_reason='html_images'
                        elif not pinned and versions:source_identifier=next(iter(versions))
                    async def original(paper_id):
                        data=await self.fetch(client,f'https://arxiv.org/pdf/{paper_id}',150*1024*1024)
                        if not data.startswith(b'%PDF-'):raise ValueError('ARXIV_DOWNLOAD_FAILED')
                        (stage/'source.pdf').write_bytes(data)
                        try:metadata=await asyncio.to_thread(self.pipeline.parser.metadata,stage/'source.pdf')
                        except Exception as error:
                            if paper_id==identifier:raise
                            raise ValueError('ARXIV_DOWNLOAD_FAILED') from error
                        return data,metadata
                    try:pdf,meta=await original(source_identifier)
                    except (ValueError,httpx.HTTPError):
                        if source_identifier==identifier:raise
                        # A version inferred from HTML assets may be unavailable.
                        # Retry the user's original PDF, keeping no HTML mixture.
                        parsed=None;fallback_reason='html_images';source_identifier=identifier
                        pdf,meta=await original(source_identifier)
                    meta['arxiv_id']=identifier
                    if parsed:
                        metadata,blocks,sources,images=parsed
                        (stage/'crops').mkdir()
                        by_id={b.id:(b,s) for b,s in zip(blocks,sources)}
                        for bid,urls in images.items():
                            block,source=by_id[bid]
                            try:
                                rasters=[]
                                for src in urls:
                                    url=urljoin(f'https://arxiv.org/html/{identifier}',src)
                                    prefix=re.escape(identifier)+('' if re.search(r'v\d+$',identifier) else r'(?:v[1-9]\d*)?')
                                    image_path=unquote(urlparse(url).path)
                                    if not re.match(r'^/html/'+prefix+'/',image_path) or '..' in image_path.split('/'):
                                        raise ValueError('ARXIV_HTML_UNAVAILABLE')
                                    image=await self.fetch(client,url,10*1024*1024,True)
                                    if not image:raise ValueError('ARXIV_HTML_UNAVAILABLE')
                                    rasters.append(image)
                                try:
                                    await asyncio.to_thread(raster_figure,rasters,stage/'crops'/f'{bid}.png')
                                except Exception as error:
                                    raise ValueError('ARXIV_HTML_UNAVAILABLE') from error
                                block.image_path=f'documents/{doc_id}/crops/{bid}.png'
                            except (ValueError,httpx.HTTPError):
                                source.update(visual_fallback_reason='html_images',visual_fallback_caption=block.caption_en or '')
                                (stage/'crops'/f'{bid}.png').unlink(missing_ok=True)
                        pending={b.id for b,s in zip(blocks,sources) if s.get('visual_fallback_reason')}
                        if pending:
                            try:
                                if not re.search(r'v[1-9]\d*$',source_identifier):
                                    raise ValueError('ARXIV_HTML_UNAVAILABLE')
                                from .html_visual_fallback import resolve_pdf_visuals
                                resolved=await asyncio.to_thread(resolve_pdf_visuals,self.pipeline.parser,stage/'source.pdf',blocks,sources,pending,stage/'crops')
                            except Exception:resolved=set()
                            if resolved!=pending:
                                # An uncertain visual must not make a partially
                                # missing HTML document look complete.
                                fallback_reason='html_images' if any(by_id[bid][1]['visual_fallback_reason']=='html_images' for bid in pending) else 'html_structure'
                                parsed=None
                                shutil.rmtree(stage/'crops')
                        if parsed:meta.update(metadata)
                    if not parsed:
                        with self.store.connect() as db:existing=db.execute("SELECT id FROM documents WHERE file_hash=? AND source_kind='pdf'",(hashlib.sha256(pdf).hexdigest(),)).fetchone()
                        if existing:return {'doc_id':existing[0],'duplicate':True,'fallback':True}
                    atomic_json(stage/'metadata.json',meta)
                    target=self.store.data_dir/'documents'/doc_id;target.parent.mkdir(exist_ok=True)
                    if target.exists():raise ValueError('ARXIV_ALREADY_EXISTS')
                    stage.rename(target)
                visual_fallbacks=[{'block_id':b.id,'reason':s['visual_fallback_reason'],'page':b.page}
                                  for b,s in zip(blocks,sources) if s.get('visual_fallback_reason')] if parsed else []
                source_kind='mixed' if visual_fallbacks else 'arxiv_html' if parsed else 'pdf'
                checkpoint={'ai_approved':False,'parser_revision':PARSER_REVISION,'source_kind':source_kind,'options':self.store.pipeline_options().model_dump(),'completed_stages':['Ingest','Extract','Structure'] if parsed else ['Ingest'],'extracted_pages':meta['page_count'] if parsed else 0,'translated':[],'restored':[],'error':None}
                checkpoint['source_arxiv_id']=source_identifier
                if visual_fallbacks:checkpoint['visual_fallbacks']=visual_fallbacks
                if not parsed:checkpoint['source_fallback_reason']=fallback_reason
                status='awaiting_ai' if parsed else 'queued'
                with self.store.connect() as db:
                    db.execute('INSERT INTO documents(id,title,arxiv_id,file_hash,source_path,status,page_count,authors,source_kind,source_url) VALUES(?,?,?,?,?,?,?,?,?,?)',(doc_id,meta['title'],identifier,hashlib.sha256(pdf).hexdigest(),f'documents/{doc_id}/source.pdf',status,meta['page_count'],meta['authors'],source_kind,f'https://arxiv.org/abs/{source_identifier}'))
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
        pictures=[]
        for data in images:
            try:picture=fitz.Pixmap(data)
            except fitz.mupdf.FzErrorBase:
                # MuPDF accepts arbitrary XML/HTML as a blank SVG document.
                # Check the root before treating a failed raster decode as SVG.
                if ElementTree.fromstring(data).tag.rsplit('}',1)[-1]!='svg':
                    raise ValueError('Not an SVG image')
                with fitz.open(stream=data,filetype='svg') as svg:
                    page=svg[0]
                    if page.rect.width<=0 or page.rect.height<=0:raise ValueError('Empty SVG image')
                    scale=min(1,1800/max(page.rect.width,page.rect.height))
                    picture=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            pictures.append(picture)
        if sum(p.width*p.height for p in pictures)>40_000_000:raise ValueError('Image too large')
        with fitz.open() as pdf:
            width=max(p.width for p in pictures);height=sum(p.height for p in pictures)
            page=pdf.new_page(width=width,height=height);y=0
            for pix in pictures:
                page.insert_image(fitz.Rect(0,y,pix.width,y+pix.height),pixmap=pix);y+=pix.height
            page.get_pixmap(matrix=fitz.Matrix(min(1,1800/width),min(1,1800/width)),alpha=False).save(path)
