"""Bounded offline OCR that never changes the saved PDF or native text.

MuPDF's bundled Tesseract implementation consumes only explicitly selected
traineddata. No executable discovery, environment override, AI or network call
is used. Results are plain text candidates with unrotated source coordinates;
the caller decides whether to preview/apply them alongside the original scan.
"""
import html
import math
from pathlib import Path

import pymupdf as fitz

from .pdf_parser import PDF_LOCK,reading_order


LANGUAGES=('eng','kor','eng+kor')
MAX_PIXELS=16_000_000
MIN_DPI=72
MAX_DPI=600


def _tessdata(directory=None):
    return Path(directory) if directory is not None else Path(__file__).resolve().parents[1]/'resources'/'ocr'/'tessdata'


def ocr_availability(*,tessdata_dir=None) -> dict:
    """Describe selectable bundled languages without initializing native OCR."""
    directory=_tessdata(tessdata_dir)
    available=[]
    for language in ('eng','kor'):
        try:
            file=directory/(language+'.traineddata')
            if file.is_file() and file.stat().st_size>=1024:available.append(language)
        except OSError:
            pass
    if len(available)==2:available.append('eng+kor')
    return {'available':bool(available),'languages':available,'reason':None if available else 'OCR_UNAVAILABLE'}


def _resolution(page,dpi):
    width,height=page.rect.width,page.rect.height
    if not all(math.isfinite(v) and v>0 for v in (width,height)):
        raise ValueError('OCR_PAGE_INVALID')
    # Enforce the budget before MuPDF allocates a pixmap. Ceil is important:
    # fractional page edges can otherwise exceed the nominal pixel allowance.
    bound=math.floor(72*math.sqrt(MAX_PIXELS/(width*height)))
    actual=min(dpi,bound)
    while actual>=MIN_DPI and math.ceil(width*actual/72)*math.ceil(height*actual/72)>MAX_PIXELS:
        actual-=1
    if actual<MIN_DPI:raise ValueError('OCR_PAGE_TOO_LARGE')
    return actual


def _bounds(page,value):
    if not isinstance(value,(tuple,list)) or len(value)!=4:return None
    try:values=list(map(float,value))
    except (TypeError,ValueError):return None
    if not all(math.isfinite(v) for v in values):return None
    native=page.rect*page.derotation_matrix
    x0,y0=max(native.x0,values[0]),max(native.y0,values[1])
    x1,y1=min(native.x1,values[2]),min(native.y1,values[3])
    return [x0,y0,x1,y1] if x1>x0 and y1>y0 else None


def _items(page,textpage):
    data=page.get_text('dict',textpage=textpage)
    # Sparse two-column scans can be recognized as one long row spanning both
    # columns. Repeated, empty central gutters in word geometry disambiguate
    # these rows without treating a single spaced title as two paragraphs.
    words_by_line={}
    if data.get('blocks'):
        for word in page.get_text('words',textpage=textpage):
            bbox=_bounds(page,word[:4])
            if bbox and str(word[4]).strip():
                words_by_line.setdefault((word[5],word[6]),[]).append(
                    {'text':str(word[4]).strip(),'bbox':bbox,'viewport':list(fitz.Rect(bbox)*page.rotation_matrix)})
    divided={};middle=page.rect.width/2
    for key,words in words_by_line.items():
        left=[word for word in words if word['viewport'][2]<=middle-8]
        right=[word for word in words if word['viewport'][0]>=middle+8]
        if len(left)<3 or len(right)<3 or len(left)+len(right)!=len(words):continue
        gap=min(word['viewport'][0] for word in right)-max(word['viewport'][2] for word in left)
        if gap>=max(24,page.rect.width*.06):divided[key]=(left,right)
    rows=[]
    all_words=[word for words in words_by_line.values() for word in words]
    for word in sorted(all_words,key=lambda value:(value['viewport'][1]+value['viewport'][3])/2):
        center=(word['viewport'][1]+word['viewport'][3])/2
        if not rows or abs(center-rows[-1][0])>4:rows.append((center,[word]))
        else:rows[-1][1].append(word)
    paired_rows=0
    for _,words in rows:
        left=[word for word in words if word['viewport'][2]<=middle-8]
        right=[word for word in words if word['viewport'][0]>=middle+8]
        if len(left)<3 or len(right)<3 or len(left)+len(right)!=len(words):continue
        gap=min(word['viewport'][0] for word in right)-max(word['viewport'][2] for word in left)
        if gap>=max(24,page.rect.width*.06):paired_rows+=1
    columns=len(divided)>=2 or paired_rows>=2
    items=[]
    for block_index,block in enumerate(data.get('blocks',[])):
        lines=[]
        for line_index,line in enumerate(block.get('lines',[])):
            split=divided.get((block.get('number',block_index),line_index)) if columns else None
            if split:
                for words in split:
                    words=sorted(words,key=lambda word:word['viewport'][0])
                    box=fitz.Rect(words[0]['bbox'])
                    for word in words[1:]:box |= fitz.Rect(word['bbox'])
                    lines.append({'text':' '.join(word['text'] for word in words),'bbox':list(box),
                                  'viewport':list(box*page.rotation_matrix)})
                continue
            text=''.join(span.get('text','') for span in line.get('spans',[])).replace('\x00','').strip()
            bbox=_bounds(page,line.get('bbox'))
            if text and bbox:
                lines.append({'text':text,'bbox':bbox,'viewport':list(fitz.Rect(bbox)*page.rotation_matrix)})
        if not lines:continue
        groups=[lines]
        # A TextPage can group independent columns into the same block. Split
        # only when all lines are narrow and both columns have repeated lines.
        width=page.rect.width
        left=[line for line in lines if line['viewport'][2]<=width/2+8]
        right=[line for line in lines if line['viewport'][0]>=width/2-8]
        if left and right and (columns or (len(left)>=2 and len(right)>=2)) and len(left)+len(right)==len(lines):groups=[left,right]
        for group in groups:
            text=' '.join(line['text'] for line in group)
            region=fitz.Rect(group[0]['bbox'])
            for line in group[1:]:region |= fitz.Rect(line['bbox'])
            items.append({'type':'li' if text.startswith(('•','– ')) else 'p','en':html.escape(text,quote=False),
                          'bbox':list(region),'page':page.number+1})
    # Sorting happens in the rendered reading frame, while stored highlights
    # stay in native coordinates for source_compare's rotation transformation.
    proxies=[{'bbox':list(fitz.Rect(item['bbox'])*page.rotation_matrix),'index':index} for index,item in enumerate(items)]
    if columns:
        # Evidence comes from repeated word rows, so even a single paragraph
        # in each column should use column order rather than starting height.
        narrow=[item for item in proxies if item['bbox'][2]<=middle+8 or item['bbox'][0]>=middle-8]
        wide=sorted([item for item in proxies if item not in narrow],key=lambda item:item['bbox'][1])
        sequence=[]
        column_key=lambda item:(0 if item['bbox'][0]<middle else 1,item['bbox'][1])
        for anchor in wide:
            band=[item for item in narrow if item['bbox'][1]<anchor['bbox'][1]]
            sequence.extend(sorted(band,key=column_key))
            narrow=[item for item in narrow if item not in band]
            sequence.append(anchor)
        sequence.extend(sorted(narrow,key=column_key))
    else:
        sequence=reading_order(proxies,page.rect.width)
    ordered=[];seen=set()
    for item in sequence:
        if item['index'] not in seen:
            ordered.append(items[item['index']]);seen.add(item['index'])
    return ordered


def recognize_page(pdf_path,page_index,*,language='eng',dpi=300,tessdata_dir=None) -> dict:
    """Recognize one zero-based page, returning candidates and actual settings.

    A native-text page returns has_native_text=True/items=[] without invoking
    OCR. No recognition yields items=[]; callers must not call that a recovered
    page. OCR does not assert a table, equation or font style was reconstructed.
    Friendly ValueError codes distinguish missing models, bad/oversized pages,
    unsupported language and native OCR failures.
    """
    if language not in LANGUAGES:raise ValueError('OCR_LANGUAGE_UNAVAILABLE')
    if isinstance(page_index,bool) or not isinstance(page_index,int) or page_index<0:
        raise ValueError('OCR_PAGE_INVALID')
    if isinstance(dpi,bool) or not isinstance(dpi,int) or not MIN_DPI<=dpi<=MAX_DPI:
        raise ValueError('OCR_PAGE_INVALID')
    with PDF_LOCK:
        try:pdf=fitz.open(pdf_path)
        except Exception:raise ValueError('OCR_PAGE_INVALID') from None
        with pdf:
            if not pdf.is_pdf or pdf.needs_pass or page_index>=len(pdf):raise ValueError('OCR_PAGE_INVALID')
            page=pdf[page_index]
            if not all(math.isfinite(v) and v>0 for v in (page.rect.width,page.rect.height)):
                raise ValueError('OCR_PAGE_INVALID')
            try:native=bool(page.get_text('text').strip())
            except Exception:raise ValueError('OCR_PAGE_INVALID') from None
            result={'page':page_index+1,'page_size':{'width':float(page.rect.width),'height':float(page.rect.height)},
                    'language':language,'dpi':dpi,'engine':'tesseract','has_native_text':native,'items':[]}
            if native:return result
            directory=_tessdata(tessdata_dir)
            availability=ocr_availability(tessdata_dir=directory)
            if not availability['available']:raise ValueError('OCR_UNAVAILABLE')
            if language not in availability['languages']:raise ValueError('OCR_LANGUAGE_UNAVAILABLE')
            actual=_resolution(page,dpi)
            try:
                textpage=page.get_textpage_ocr(flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES,
                                             language=language,dpi=actual,full=True,tessdata=str(directory))
                result['items']=_items(page,textpage)
            except Exception:raise ValueError('OCR_FAILED') from None
            result['dpi']=actual
            return result
