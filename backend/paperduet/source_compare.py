"""Read-only, high-confidence locations in the saved original PDF.

Stored source coordinates win over text search. HTML sources have no PDF page
mapping, so only a unique substantial text match or a unique explicit caption /
equation label is used. Ambiguity leaves the page chooser available, without a
guessed highlight. All coordinates refer to the rendered (rotated) page viewport.
"""
import json
import math
import re
import unicodedata
from collections import OrderedDict
from copy import deepcopy

import pymupdf as fitz
from bs4 import BeautifulSoup

from .models import Block
from .pdf_parser import PDF_LOCK
from .reanalysis_match import source_signature


MANUAL_REASON='정확한 원문 위치를 찾지 못했습니다. 페이지를 직접 선택해 주세요.'
_TEXT_CACHE=OrderedDict()
_LOCATION_CACHE=OrderedDict()
_MAX_DOCUMENTS=4
_MAX_PAGES=1000
_MAX_TEXT=12_000_000
_MAX_LOCATIONS=512


def _normal(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFC',value or '').replace('\u00ad','')).strip()


def _plain(value):
    return _normal(BeautifulSoup(value or '', 'html.parser').get_text())


def _rectangle(value):
    if not isinstance(value,(list,tuple)) or len(value)!=4 or any(isinstance(v,bool) for v in value):
        return None
    try:
        numbers=list(map(float,value))
    except (TypeError,ValueError):
        return None
    if not all(math.isfinite(v) for v in numbers) or numbers[2]<=numbers[0] or numbers[3]<=numbers[1]:
        return None
    return numbers


def _native_rectangle(page,value):
    numbers=_rectangle(value)
    if numbers is None:
        return None
    bounds=page.rect*page.derotation_matrix
    # Clamp in Python before sending coordinates through MuPDF's C float API:
    # even finite JSON numbers can exceed its representable coordinate range.
    x0,y0=max(numbers[0],bounds.x0),max(numbers[1],bounds.y0)
    x1,y1=min(numbers[2],bounds.x1),min(numbers[3],bounds.y1)
    return fitz.Rect(x0,y0,x1,y1) if x1>x0 and y1>y0 else None


def _viewport_rectangle(page,value):
    rect=_native_rectangle(page,value)
    if rect is None:
        return None
    # MuPDF text/provenance coordinates are relative to the unrotated cropbox;
    # get_pixmap renders the rotated cropbox. Clip in the correct frame first.
    rect=rect*page.rotation_matrix
    rect &= page.rect
    if rect.is_empty:
        return None
    return [float(rect.x0-page.rect.x0),float(rect.y0-page.rect.y0),
            float(rect.x1-page.rect.x0),float(rect.y1-page.rect.y0)]


def _location(page_number,page,bbox=None,region=None,reason=None):
    return {'page':page_number,'bbox':_viewport_rectangle(page,bbox),
            'region':_viewport_rectangle(page,region),
            'page_size':{'width':float(page.rect.width),'height':float(page.rect.height)},'reason':reason}


def _document_text(pdf,key):
    cached=_TEXT_CACHE.get(key)
    if cached is not None:
        _TEXT_CACHE.move_to_end(key)
        return cached
    pages=[];characters=0;complete=True
    for page_number in range(len(pdf)):
        if page_number>=_MAX_PAGES:
            complete=False
            break
        page=pdf[page_number]
        lines=[];offsets=[];parts=[];position=0
        data=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
        for block in data['blocks']:
            for line in block.get('lines',[]):
                text=_normal(''.join(span['text'] for span in line.get('spans',[])))
                if not text:
                    continue
                rect=_native_rectangle(page,line['bbox'])
                if rect is None:
                    continue
                bbox=list(rect)
                lines.append((text,bbox))
                offsets.append((position,position+len(text),bbox))
                parts.append(text)
                position+=len(text)+1
        text=' '.join(parts)
        characters+=len(text)
        if characters>_MAX_TEXT:
            complete=False
            break
        pages.append({'text':text,'lines':lines,'offsets':offsets})
    result={'pages':pages,'complete':complete,'characters':min(characters,_MAX_TEXT)}
    # Incomplete searches are cached too, but never establish uniqueness.
    _TEXT_CACHE[key]=result
    while len(_TEXT_CACHE)>_MAX_DOCUMENTS or (len(_TEXT_CACHE)>1 and
            (sum(len(v['pages']) for v in _TEXT_CACHE.values())>_MAX_PAGES or sum(v['characters'] for v in _TEXT_CACHE.values())>_MAX_TEXT)):
        _TEXT_CACHE.popitem(last=False)
    return result


def _union(rectangles):
    result=fitz.Rect(rectangles[0])
    for rect in rectangles[1:]:
        result |= fitz.Rect(rect)
    return list(result)


def _text_match(pages,query):
    hits=[]
    for index,page in enumerate(pages):
        for match in re.finditer(re.escape(query),page['text']):
            boxes=[bbox for start,end,bbox in page['offsets'] if start<match.end() and end>match.start()]
            if boxes:hits.append((index+1,_union(boxes)))
            if len(hits)>1:return None
    return hits[0] if len(hits)==1 else None


def _label_pattern(block):
    label=_normal(block.n)
    if block.type in {'fig','tab'}:
        prefix=r'(?:Figure|Fig\.?)' if block.type=='fig' else 'Table'
        number=re.sub(r'^(?:Figure|Fig\.?|Table)\s*','',label,flags=re.I).rstrip(': .')
        if not re.fullmatch(r'[A-Za-z]?\d+(?:\.\d+)*[a-z]?',number):return None
        # A prose reference "Figure 1 shows ..." is not a caption location.
        return re.compile(r'^'+prefix+r'\s*'+re.escape(number)+r'\b(?:\s*[:.]|\s*$)',re.I)
    if block.type=='eq':
        number=label.strip('() ')
        if re.fullmatch(r'[A-Za-z]?\d+(?:\.\d+)*[a-z]?',number):
            return re.compile(r'\('+re.escape(number)+r'\)\s*$')
    return None


def _infer(block,pages):
    for query in (_plain(block.en),_plain(block.caption_en)):
        words=len(re.findall(r'\w+',query))
        sufficient=len(query)>=40 and words>=6
        if block.type in {'fig','tab'}:sufficient=len(query)>=24 and words>=4
        if sufficient:
            result=_text_match(pages,query)
            if result:return result
    pattern=_label_pattern(block)
    hits=[]
    if pattern:
        for index,page in enumerate(pages):
            for text,bbox in page['lines']:
                if not pattern.search(text):continue
                if block.type=='eq' and not (pattern.fullmatch(text) or
                        (re.search(r'[=+−∑∫<>≤≥]',text) and len(re.findall(r'[A-Za-z]{3,}',text))<4)):
                    continue
                hits.append((index+1,bbox))
        if len(hits)==1:return hits[0]
    if block.type in {'sec','sub','ssub'}:
        title=_plain(block.en)
        expected={title}
        if block.n:expected.update({_normal(block.n+' '+title),_normal(block.n+'. '+title)})
        expected.discard('')
        hits=[(index+1,bbox) for index,page in enumerate(pages) for text,bbox in page['lines'] if text in expected]
        if len(hits)==1:return hits[0]
    return None


def locate_source(store,doc_id,block_id) -> dict:
    """Locate an immutable source block; never write files, DB rows or AI data.

    Raises ValueError('DOCUMENT_NOT_FOUND') / ValueError('BLOCK_NOT_FOUND').
    Missing/unreadable PDFs return available=False while retaining source_url.
    A valid PDF without precise evidence returns available=True for manual view.
    """
    with store.connect() as db:
        document=db.execute('SELECT source_path,source_kind,source_url,page_count FROM documents WHERE id=?',(doc_id,)).fetchone()
        if document is None:raise ValueError('DOCUMENT_NOT_FOUND')
        saved=db.execute('SELECT payload,provenance FROM source_blocks WHERE doc_id=? AND block_id=?',(doc_id,block_id)).fetchone()
        if saved is None:raise ValueError('BLOCK_NOT_FOUND')
    block=Block.model_validate_json(saved['payload'])
    provenance=json.loads(saved['provenance'])
    result={'block_id':block_id,'page':None,'page_count':int(document['page_count'] or 0),
            'bbox':None,'region':None,'page_size':None,'source_kind':document['source_kind'],
            'source_url':document['source_url'] or '', 'source_anchor':provenance.get('source_anchor'),
            'available':False,'reason':'저장된 원본 PDF를 찾을 수 없습니다.'}
    path=store.asset_path(doc_id,document['source_path']) if document['source_path'] else None
    if path is None or path.suffix.lower()!='.pdf':return result
    with PDF_LOCK:
        try:
            stat=path.stat()
            file_key=(str(path),stat.st_mtime_ns,stat.st_size)
            key=(file_key,block_id,source_signature(block),block.page,
                 json.dumps({k:provenance.get(k) for k in ('bbox','region')},sort_keys=True))
            cached=_LOCATION_CACHE.get(key)
            if cached is not None:
                _LOCATION_CACHE.move_to_end(key)
                return {**result,**deepcopy(cached)}
            with fitz.open(path) as pdf:
                if not pdf.is_pdf or pdf.needs_pass or not len(pdf):
                    result['reason']='저장된 원본 PDF를 열 수 없습니다.'
                    return result
                result.update(available=True,page_count=len(pdf),reason=MANUAL_REASON)
                number=block.page
                if isinstance(number,int) and not isinstance(number,bool) and 1<=number<=len(pdf):
                    page=pdf[number-1]
                    location=_location(number,page,provenance.get('bbox'),provenance.get('region'))
                    if location['bbox'] is not None or location['region'] is not None:
                        result.update(location)
                    else:
                        # A stored PDF page is still a useful starting page;
                        # invalid or absent rectangles never become a highlight.
                        result.update(_location(number,page,reason=MANUAL_REASON))
                else:
                    text=_document_text(pdf,file_key)
                    match=_infer(block,text['pages']) if text['complete'] else None
                    if match:
                        number,bbox=match
                        result.update(_location(number,pdf[number-1],bbox=bbox))
        except (OSError,RuntimeError,ValueError):
            result.update(available=False,page=None,bbox=None,region=None,page_size=None,
                          reason='저장된 원본 PDF를 열 수 없습니다.')
            return result
        _LOCATION_CACHE[key]=deepcopy({k:result[k] for k in ('page','page_count','bbox','region','page_size','available','reason')})
        while len(_LOCATION_CACHE)>_MAX_LOCATIONS:_LOCATION_CACHE.popitem(last=False)
    return result
