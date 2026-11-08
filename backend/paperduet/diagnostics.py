"""Read-only evidence checks. Observed gaps are not prose-quality verdicts."""
import hashlib
import json
import math
import struct
import threading
from collections import Counter, OrderedDict
from datetime import datetime, timezone

import pymupdf as fitz

from .adapter import plain
from .models import Block
from .pdf_parser import PDF_LOCK
from .pipeline import BUSY
from .validation import table_grid, translation_complete


_cache = OrderedDict()
_cache_lock = threading.RLock()


def _file_key(path, category):
    value=path.stat()
    return category,str(path.resolve()),value.st_size,value.st_mtime_ns,value.st_ctime_ns


def _cached(key, build, limit=128):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    value=build()
    with _cache_lock:
        _cache[key]=value
        _cache.move_to_end(key)
        while len(_cache)>limit:_cache.popitem(last=False)
    return value


def _rectangle(value, width, height, rotation=None):
    if not isinstance(value,(list,tuple)) or len(value)!=4 or any(isinstance(v,bool) for v in value):
        return None
    try:
        numbers=list(map(float,value))
        if not all(math.isfinite(v) for v in numbers) or numbers[2]<=numbers[0] or numbers[3]<=numbers[1]:return None
        bounds=fitz.Rect(0,0,width,height)
        if rotation:
            matrix=fitz.Matrix(*rotation)
            inverse=fitz.Matrix(matrix);inverse.invert()
            bounds=bounds*inverse
        clipped=[max(bounds.x0,min(bounds.x1,numbers[0])),max(bounds.y0,min(bounds.y1,numbers[1])),
                 max(bounds.x0,min(bounds.x1,numbers[2])),max(bounds.y0,min(bounds.y1,numbers[3]))]
        if clipped[2]<=clipped[0] or clipped[3]<=clipped[1]:return None
        rect=fitz.Rect(clipped)
        if rotation:rect=rect*matrix
        rect &= fitz.Rect(0,0,width,height)
        return list(rect) if not rect.is_empty else None
    except (TypeError,ValueError,OverflowError,RuntimeError):return None


def inspect_original(store,doc_id):
    """Inspect the stored PDF; no parser cache, crop or document is written."""
    with store.connect() as db:
        row=db.execute('SELECT source_path,page_count,source_kind,source_url FROM documents WHERE id=?',(doc_id,)).fetchone()
    if not row:raise ValueError('DOCUMENT_NOT_FOUND')
    result={'available':False,'page_count':row['page_count'],'pages':[],
            'source_kind':row['source_kind'],'source_url':row['source_url'],
            'reason':'저장된 원본 PDF를 찾을 수 없습니다.'}
    path=store.asset_path(doc_id,row['source_path']) if row['source_path'] else None
    if not path or path.suffix.lower()!='.pdf':return result

    def read():
        try:
            with PDF_LOCK,fitz.open(path) as pdf:
                if not pdf.is_pdf or pdf.needs_pass or not 1<=len(pdf)<=1000:
                    return {'available':False,'reason':'원본 PDF가 손상되었거나 암호가 필요해 열 수 없습니다.','pages':[]}
                pages=[]
                for page in pdf:
                    width,height=page.rect.width,page.rect.height
                    if not all(math.isfinite(v) and v>0 for v in (width,height)):raise ValueError('Invalid page size')
                    rotation=list(page.rotation_matrix)
                    images=[]
                    for image in page.get_image_info():
                        rect=_rectangle(image['bbox'],width,height,rotation)
                        if not rect:continue
                        area=fitz.Rect(rect)
                        if area.width>=24 and area.height>=24 and area.get_area()>=900 and rect not in images:
                            images.append(rect)
                    pages.append({'page':page.number+1,'width':width,'height':height,'rotation':rotation,
                                  'has_text':bool(page.get_text('text').strip()),'images':images})
                return {'available':True,'reason':None,'page_count':len(pdf),'pages':pages}
        except Exception:
            return {'available':False,'reason':'원본 PDF를 열어 확인하지 못했습니다.','pages':[]}
    try:result.update(_cached(_file_key(path,'pdf'),read,limit=128))
    except OSError:pass
    return result


def document_source(store,doc_id):
    original=inspect_original(store,doc_id)
    return {'block_id':None,'page':None,'page_count':original['page_count'],'bbox':None,'region':None,
            'page_size':None,'source_kind':original['source_kind'],'source_url':original['source_url'],
            'source_anchor':None,'available':original['available'],'reason':original['reason']}


def _valid_picture(store,doc_id,stored):
    path=store.asset_path(doc_id,stored)
    if not path or path.suffix.lower()!='.png':return False
    def decode():
        try:
            with path.open('rb') as stream:header=stream.read(24)
            if header[:8]!=b'\x89PNG\r\n\x1a\n' or header[12:16]!=b'IHDR':return False
            width,height=struct.unpack('>II',header[16:24])
            if not width or not height or width*height>40_000_000:return False
            with PDF_LOCK:
                picture=fitz.Pixmap(str(path))
                return picture.width>0 and picture.height>0
        except Exception:return False
    try:return _cached(_file_key(path,'png'),decode)
    except OSError:return False


def diagnose_document(store,doc_id):
    doc=store.document(doc_id)
    if not doc:raise ValueError('DOCUMENT_NOT_FOUND')
    with store.connect() as db:
        job=db.execute('SELECT status,stage,checkpoint FROM jobs WHERE doc_id=?',(doc_id,)).fetchone()
        sources={r['block_id']:{'block':Block.model_validate_json(r['payload']),**json.loads(r['provenance'])}
                 for r in db.execute('SELECT * FROM source_blocks WHERE doc_id=?',(doc_id,))}
        last=db.execute('SELECT * FROM document_reanalyses WHERE doc_id=? ORDER BY rowid DESC LIMIT 1',(doc_id,)).fetchone()
    checkpoint=json.loads(job['checkpoint'] or '{}') if job else {}
    busy=bool(job and (job['status'] in BUSY or checkpoint.get('pending_batch')))
    original=inspect_original(store,doc_id)
    active=[b for b in doc.blocks if not b.superseded_by and not b.note and not b.card]
    # Source-less fixture/older documents remain inspectable without inventing
    # provenance. Generated cards and notes are never original-paper counts.
    if sources:active=[b for b in active if b.id in sources]
    ocr_by_page = {}
    for block in active:
        if sources.get(block.id, {}).get('origin') == 'pdf_ocr' and block.page is not None:
            ocr_by_page.setdefault(block.page, []).append(block)
    counts={'paragraphs':sum(b.type in {'p','li'} for b in active),'figures':sum(b.type=='fig' for b in active),
            'tables':sum(b.type=='tab' for b in active),'equations':sum(b.type=='eq' for b in active)}
    issues=[]
    notices=['저장된 원문·이미지·번역 상태를 확인합니다. 모든 문장이나 도형의 누락을 확정하는 검사는 아닙니다.']
    if busy:notices.append('논문을 처리 중입니다. 진행 중인 항목은 완료 후 다시 확인해 주세요.')
    elif job and job['status'] in {'paused','failed'}:notices.append('논문 처리가 멈춘 상태입니다. 처리 상태에서 원인을 확인하고 이어서 진행할 수 있습니다.')
    if not original['available']:notices.append(original['reason'])
    fallback={
        'html_unavailable':'arXiv HTML 원문을 가져오지 못해 원본 PDF 방식으로 가져온 논문입니다.',
        'html_structure':'HTML의 일부 구조를 안전하게 보존하기 어려워 원본 PDF 방식으로 가져온 논문입니다.',
        'html_images':'HTML 그림을 불러오거나 변환하지 못해 원본 PDF 방식으로 가져온 논문입니다.',
    }
    partial_visuals=[b for b in active if b.type in {'fig','tab'} and sources.get(b.id,{}).get('origin')=='arxiv_pdf']
    if partial_visuals:
        notices.append('일부 그림·표는 HTML 표시 문제로 원본 PDF 영역을 사용합니다. 다른 HTML 원문은 유지하며, 대체한 항목은 아래 참고 사항에서 확인할 수 있습니다.')
    elif doc.source_kind=='mixed':notices.append('HTML 원문과 원본 PDF 추출 결과를 함께 사용하는 논문입니다.')
    elif checkpoint.get('source_fallback_reason') in fallback:notices.append(fallback[checkpoint['source_fallback_reason']])
    elif doc.source_kind=='pdf' and doc.source_url.startswith('https://arxiv.org/'):
        notices.append('arXiv 원문을 PDF 방식으로 가져온 논문입니다. 이전 버전에서 가져온 자료에는 전환 사유가 저장되어 있지 않을 수 있습니다.')
    if ocr_by_page:
        notices.append('OCR로 읽은 글자는 원본과 다를 수 있습니다. 숫자·기호·수식과 표의 읽기 순서는 원문 대조에서 확인해 주세요.')

    def add(kind,block,page,title,detail,severity='attention',region=None,key=None):
        block_id=block.id if block else None
        identity=f'{kind}|{block_id}|{page}|{key or ""}'
        issues.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:24],'kind':kind,'severity':severity,
                       'block_id':block_id,'page':page,'title':title,'detail':detail,
                       'source_available':original['available'],'region':region})

    for block in active:
        source=sources.get(block.id,{})
        page=block.page if isinstance(block.page,int) and 1<=block.page<=original['page_count'] else None
        label=plain(block.n or '').strip()[:100]
        visual={'fig':'그림','tab':'표','eq':'수식'}.get(block.type,'본문')
        name=label or visual
        if block.type in {'fig','tab'} and source.get('origin')=='arxiv_pdf':
            detail={
                'html_structure':'HTML의 이 그림·표 구조를 안전하게 보존하기 어려워 PDF 원본 영역을 사용한 항목입니다.',
                'html_images':'HTML 이미지를 불러오거나 변환하지 못해 PDF 원본 영역을 사용한 항목입니다.',
            }.get(source.get('visual_fallback_reason'),'이 항목은 HTML 원문과 연결된 PDF 원본 영역을 사용합니다.')
            add('visual_pdf_fallback',block,page,f'{name} PDF 원본 영역 사용',detail,'info')
        image_broken=bool(block.image_path and not _valid_picture(store,doc_id,block.image_path))
        if image_broken:
            add('missing_image',block,page,f'{name} 이미지 확인 필요','저장된 이미지 파일이 없거나 열리지 않습니다. 원본 PDF와 대조해 주세요.',
                'info' if busy else 'attention')
        malformed=False
        if block.table:
            try:table_grid(block.table)
            except ValueError:malformed=True
        if block.restore_error or malformed:
            detail=('복원 결과의 숫자가 비교 가능한 원문 표와 달라 적용하지 않았습니다. 원본 이미지를 확인해 주세요.'
                    if block.restore_error=='numeric_mismatch' else '표·수식 복원 결과를 확인하지 못했습니다. 원본 이미지 또는 PDF로 대조해 주세요.')
            add('restore_failed',block,page,f'{name} 복원 확인 필요',detail)
        elif not image_broken and ((block.type=='eq' and not block.latex) or
                                   (block.type=='tab' and not block.table and (block.image_path or source.get('needs_restore') or block.en))):
            add('restore_pending',block,page,f'{name} 복원 전','아직 읽기용 표·수식으로 복원되지 않았습니다. 원문 대조나 처리 상태를 확인해 주세요.','info')
        reference=source.get('references') or any(p.casefold() in {'references','bibliography'} for p in block.section_path)
        target=not reference and (block.type in {'sec','sub','ssub','p','li'} or block.caption_en or block.table)
        if not target:continue
        translated=translation_complete(block,source.get('block'))
        if block.translation_error:
            add('translation_rejected',block,page,f'{name} 새 번역 적용 안 됨',
                '원문 숫자·수식 보존이나 응답 형식을 확인하지 못해 새 번역을 적용하지 않았습니다. 기존 번역이 있으면 유지합니다.',
                'info' if translated or busy else 'attention')
        elif not translated:
            add('untranslated',block,page,f'{name} 미번역','본문·캡션 또는 표 머리글에 아직 번역되지 않은 내용이 있습니다.',
                'info' if busy or not checkpoint.get('ai_approved') else 'attention')

    pages=original['pages']
    page_by_number={page['page']:page for page in pages}
    unmapped_html_visual=False
    for block in active:
        source=sources.get(block.id,{})
        if block.type not in {'fig','tab','eq'} or source.get('origin')!='arxiv_html':continue
        page=page_by_number.get(block.page)
        if page is None or _rectangle(source.get('region') or source.get('bbox'),page['width'],page['height'],page['rotation']) is None:
            unmapped_html_visual=True
            break
    scan_pdf_visuals=doc.source_kind in {'pdf','mixed'} and not unmapped_html_visual
    if original['available'] and doc.source_kind in {'pdf','mixed'} and unmapped_html_visual:
        notices.append('PDF 위치를 확인하지 못한 HTML 그림·표·수식은 원문 대조로 직접 확인해 주세요. 해당 영역의 자동 연결 진단은 보류했습니다.')
    for page in pages:
        if page['page'] in ocr_by_page:
            recognized = ocr_by_page[page['page']]
            add('ocr_recognized', recognized[0], page['page'], f'{page["page"]}쪽 OCR 원문 대조',
                f'이 쪽에서 OCR로 읽은 본문 {len(recognized)}개가 저장되어 있습니다. 원본 PDF는 그대로 보존됩니다. 인식한 숫자·기호와 읽기 순서를 대조해 주세요.', 'info')
        elif not page['has_text']:
            add('page_without_text',None,page['page'],f'{page["page"]}쪽 텍스트 확인 필요',
                '원본 페이지에서 텍스트를 찾지 못했습니다. 이미지·도표 페이지이거나 스캔 문서일 수 있습니다.','info')
        elif doc.source_kind=='pdf' and not any(b.page==page['page'] for b in doc.blocks if not b.note and not b.card):
            add('unmatched_source',None,page['page'],f'{page["page"]}쪽 원문 연결 확인',
                '원본에는 텍스트가 있지만 이 쪽에 연결된 저장 원문 위치를 찾지 못했습니다. 다른 쪽의 문단과 합쳐졌을 수도 있으므로 직접 대조해 주세요.','info',key='text-page')
        if not scan_pdf_visuals:continue
        covered=[]
        for block in doc.blocks:
            source=sources.get(block.id,{})
            if block.type not in {'fig','tab','eq'} or block.page!=page['page']:continue
            rect=_rectangle(source.get('region') or source.get('bbox'),page['width'],page['height'],page['rotation'])
            if rect:covered.append(fitz.Rect(rect))
        for index,image in enumerate(page['images']):
            rect=fitz.Rect(image)
            if any((rect & region).get_area()/rect.get_area()>=.55 for region in covered):continue
            add('unmatched_source',None,page['page'],f'{page["page"]}쪽 원본 이미지 영역 대조',
                '저장된 그림·표·수식의 위치와 연결되지 않은 원본 이미지 영역입니다. 장식이나 로고일 수도 있으므로 직접 대조해 주세요.',
                'info',region=image,key=f'image-{index}')

    if last:
        path=store.asset_path(doc_id,last['manifest_path'])
        try:
            manifest=json.loads(path.read_text(encoding='utf-8')) if path else None
            applied=set(json.loads(last['applied_ids']))
            candidates=[c for c in manifest['changes'] if c['kind'] in {'added','missing'} and c['id'] not in applied] if manifest else []
            for change in candidates:
                value=change['old_block'] if change['kind']=='missing' else change['new_block']
                block=next((b for b in active if value and b.id==value['id']),None) if change['kind']=='missing' else None
                page=value.get('page') if value else None
                is_ocr = manifest.get('method') == 'ocr'
                title=('OCR로 읽은 미반영 원문 후보' if is_ocr else '새로 찾은 미반영 원문 후보') if change['kind']=='added' else '새 추출에서 찾지 못한 기존 원문'
                detail=('OCR로 읽고 아직 반영하지 않은 후보입니다. OCR 글자 읽기에서 원본과 비교하고 필요한 항목만 반영할 수 있습니다.' if is_ocr
                        else '마지막 재분석에서 발견된 후보입니다. 기존 내용은 유지되며, 다시 분석에서 현재 원문과 비교한 뒤 적용할 수 있습니다.')
                add('unmatched_source',block,page,title,detail,'info',key=last['id']+change['id'])
            if candidates:notices.append('OCR 후보는 인식한 글자를 검토하기 위한 미리보기입니다. 아직 저장된 본문에 반영되지 않은 항목이 포함되어 있습니다.' if manifest.get('method') == 'ocr'
                                         else '재분석 후보는 실제 누락 확정이 아닙니다. 비교 후 원문이나 메모가 바뀌었을 수 있으므로 적용 전 다시 분석해 주세요.')
        except (ValueError,OSError,KeyError,TypeError):
            notices.append('저장된 재분석 후보를 읽지 못했습니다. 필요한 경우 원문을 다시 분석해 주세요.')
    without=[p['page'] for p in pages if not p['has_text']]
    page_counts = {'total':original['page_count'],'with_text':len(pages)-len(without),'without_text':without}
    if ocr_by_page:page_counts['with_ocr'] = sorted(ocr_by_page)
    return {'doc_id':doc_id,'generated_at':datetime.now(timezone.utc).isoformat(),'busy':busy,
            'summary':{'total':len(issues),'by_kind':dict(Counter(issue['kind'] for issue in issues)),
                       'blocks':counts,'pages':page_counts},
            'issues':issues,'notices':notices}
