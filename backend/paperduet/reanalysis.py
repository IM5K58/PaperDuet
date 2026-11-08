"""Local, reviewable source upgrades. Never call AI or overwrite source history."""
import asyncio
import hashlib
import json
import math
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .adapter import inline,plain
from .models import Block
from .pipeline import BUSY, PARSER_REVISION, atomic_json
from .reanalysis_match import match_blocks, source_signature
from .validation import translation_complete


REANALYSIS_REVISION = 3
OCR_MAX_PAGES = 20


class ReanalysisError(ValueError):
    pass


class ReanalysisManager:
    def __init__(self, store, pipeline):
        self.store, self.pipeline = store, pipeline
        self.locks = {}

    def lock(self, doc_id):
        return self.locks.setdefault(doc_id, asyncio.Lock())

    def _row(self, db, doc_id):
        if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}', doc_id):
            raise ReanalysisError('REANALYSIS_NOT_FOUND')
        row = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not row:
            raise ReanalysisError('REANALYSIS_NOT_FOUND')
        if row['status'] == 'fixture' or not row['source_path']:
            raise ReanalysisError('REANALYSIS_UNSUPPORTED')
        return row

    def _busy(self, db, doc_id):
        job = db.execute('SELECT status,checkpoint FROM jobs WHERE doc_id=?', (doc_id,)).fetchone()
        pending = json.loads(job['checkpoint'] or '{}').get('pending_batch') if job else None
        tasks = (self.pipeline.tasks.get(doc_id), self.pipeline.summary_tasks.get(doc_id))
        summary = db.execute("SELECT 1 FROM paper_summaries WHERE doc_id=? AND status='running'", (doc_id,)).fetchone()
        answering = db.execute("SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id WHERE t.doc_id=? AND m.status='running'", (doc_id,)).fetchone()
        if (job and job['status'] in BUSY) or pending or summary or answering or any(t and not t.done() for t in tasks):
            raise ReanalysisError('JOB_BUSY')

    @staticmethod
    def _hash_file(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def _fingerprint(self, db, doc_id):
        row = self._row(db, doc_id)
        # Reading position is intentionally excluded: reading a preview must
        # not make it stale. Source, edits and every linked artifact are included.
        state = {'document': {k: row[k] for k in row.keys() if k not in {'last_block_id', 'last_offset', 'opened_at'}}}
        for table, query in {
            'blocks': 'SELECT * FROM blocks WHERE doc_id=? ORDER BY id',
            'sources': 'SELECT * FROM source_blocks WHERE doc_id=? ORDER BY block_id',
            'jobs': 'SELECT id,stage,status,progress,checkpoint FROM jobs WHERE doc_id=? ORDER BY id',
            'notes': 'SELECT * FROM note_anchors WHERE doc_id=? ORDER BY note_id',
            'threads': 'SELECT * FROM threads WHERE doc_id=? ORDER BY id',
            'messages': 'SELECT m.* FROM messages m JOIN threads t ON m.thread_id=t.id WHERE t.doc_id=? ORDER BY m.id',
            'memos': 'SELECT * FROM presentation_notes WHERE doc_id=? ORDER BY id',
            'summary': 'SELECT * FROM paper_summaries WHERE doc_id=?',
            'glossary': 'SELECT * FROM glossary WHERE doc_id=? ORDER BY term',
        }.items():
            state[table] = [dict(r) for r in db.execute(query, (doc_id,))]
        paths = {row['source_path'], f'documents/{doc_id}/source.html'}
        paths.update(json.loads(r['payload']).get('image_path') for r in state['blocks'])
        state['assets'] = {}
        for stored in sorted(p for p in paths if p):
            path = self.store.asset_path(doc_id, stored)
            state['assets'][stored] = self._hash_file(path) if path else None
        return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _sources(self, db, doc_id):
        return {r['block_id']: {'block': Block.model_validate_json(r['payload']), **json.loads(r['provenance'])}
                for r in db.execute('SELECT * FROM source_blocks WHERE doc_id=?', (doc_id,))}

    def _image_hash(self, doc_id, block):
        if not block.image_path:
            return None
        path = self.store.asset_path(doc_id, block.image_path)
        return self._hash_file(path) if path else None

    @staticmethod
    def _ocr_availability():
        try:
            from .ocr_engine import ocr_availability
            return ocr_availability()
        except (ImportError,OSError):
            return {'available':False,'languages':[],'reason':'OCR_UNAVAILABLE'}

    def ocr_candidates(self,doc_id):
        from .diagnostics import inspect_original
        with self.store.connect() as db:
            self._row(db,doc_id)
            try:self._busy(db,doc_id);busy=False
            except ReanalysisError as error:
                if str(error)!='JOB_BUSY':raise
                busy=True
            blocks=[Block.model_validate_json(row[0]) for row in db.execute('SELECT payload FROM blocks WHERE doc_id=?',(doc_id,))]
            sources=self._sources(db,doc_id)
            job=db.execute('SELECT checkpoint FROM jobs WHERE doc_id=?',(doc_id,)).fetchone()
        original=inspect_original(self.store,doc_id)
        availability=self._ocr_availability()
        notices=['선택한 쪽에서만 로컬 OCR로 글자를 읽습니다. 원본 PDF와 기존 번역·메모는 바꾸지 않으며 AI를 호출하지 않습니다.']
        if not original['available']:notices.append(original['reason'])
        if not availability.get('available'):notices.append('이 환경에서 OCR 언어 자료를 사용할 수 없습니다.')
        if busy or self.lock(doc_id).locked():notices.append('다른 처리가 진행 중입니다. 완료 후 OCR을 시작해 주세요.')
        pages=[]
        for page in original['pages']:
            recognized=sum(not block.superseded_by and block.page==page['page'] and sources.get(block.id,{}).get('origin')=='pdf_ocr' for block in blocks)
            pages.append({'page':page['page'],'eligible':not page['has_text'],'has_text':page['has_text'],'recognized_blocks':recognized})
        completed=json.loads(job['checkpoint'] or '{}').get('completed_stages',[]) if job else []
        active_source=any(block.id in sources and not block.superseded_by and not block.note and not block.card for block in blocks)
        extraction_required=not active_source and 'Structure' not in completed and any(page['has_text'] for page in pages)
        if extraction_required:notices.append('텍스트가 있는 쪽의 원문 추출을 먼저 다시 실행한 뒤 OCR을 시작해 주세요.')
        return {'available':bool(original['available'] and availability.get('available')),
                'languages':availability.get('languages',[]),'reason':None if original['available'] and availability.get('available') else 'OCR_UNAVAILABLE',
                'max_pages':OCR_MAX_PAGES,'pages':pages,'notices':notices,'busy':busy or self.lock(doc_id).locked(),
                'source_extraction_required':extraction_required}

    @staticmethod
    def _insert_page_blocks(blocks,sources,additional):
        for block,source in additional:
            later=next((i for i,current in enumerate(blocks) if current.page is not None and current.page>block.page),None)
            if later is not None:position=later
            else:
                earlier=[i for i,current in enumerate(blocks) if current.page is not None and current.page<=block.page]
                position=earlier[-1]+1 if earlier else len(blocks)
            blocks.insert(position,block);sources.insert(position,source)
        for index,block in enumerate(blocks):block.order=index

    def _reuse_ocr(self,original,old_blocks,old_sources,blocks,sources):
        saved=[(block,old_sources[block.id]) for block in old_blocks if old_sources.get(block.id,{}).get('origin')=='pdf_ocr']
        if not saved:return blocks,sources
        fingerprint=self._hash_file(original)
        additional=[(source['block'].model_copy(deep=True),{key:value for key,value in source.items() if key!='block'})
                    for _,source in saved if source.get('ocr_source_hash')==fingerprint]
        self._insert_page_blocks(blocks,sources,additional)
        return blocks,sources

    def _extract_ocr(self,doc_id,row,old_blocks,old_sources,request):
        from .ocr_engine import recognize_page
        original=self.store.asset_path(doc_id,row['source_path'])
        if not original or original.suffix.lower()!='.pdf':raise ReanalysisError('OCR_UNAVAILABLE')
        fingerprint=self._hash_file(original)
        results=[];recognized={}
        notices=['선택한 쪽에서만 로컬 OCR로 글자를 읽었습니다. 원본 PDF와 기존 내용은 적용 전까지 그대로 유지됩니다.',
                 'OCR은 글자나 숫자를 잘못 읽거나 표·수식을 평문으로 읽을 수 있습니다. 적용 전 원본 쪽과 대조해 주세요.']
        for page in request['pages']:
            try:
                result=recognize_page(original,page-1,language=request['language'],dpi=300)
                if result.get('has_native_text'):raise ReanalysisError('OCR_PAGE_HAS_TEXT')
                items=[]
                for item in result.get('items',[]):
                    value=item.get('en');rect=item.get('bbox')
                    if not isinstance(value,str) or not plain(value).strip():continue
                    if (item.get('type') not in {'p','li'} or item.get('page')!=page or not isinstance(rect,(list,tuple)) or len(rect)!=4
                            or not all(isinstance(v,(int,float)) and math.isfinite(v) and abs(v)<1e7 for v in rect)
                            or rect[2]<=rect[0] or rect[3]<=rect[1]):raise ValueError('OCR_FAILED')
                    items.append((item,result))
                previous=[old_sources[block.id] for block in old_blocks
                          if block.page==page and old_sources[block.id].get('origin')=='pdf_ocr']
                if items and previous:
                    if len(items)!=len(previous):raise ValueError('OCR_SEGMENTATION_CHANGED')
                    for source,(item,_) in zip(previous,items):
                        before=source.get('bbox') or source.get('region');after=item['bbox']
                        if (not isinstance(before,(list,tuple)) or len(before)!=4
                                or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in before)
                                or before[2]<=before[0] or before[3]<=before[1]):raise ValueError('OCR_SEGMENTATION_CHANGED')
                        intersection=max(0,min(before[2],after[2])-max(before[0],after[0]))*max(0,min(before[3],after[3])-max(before[1],after[1]))
                        union=(before[2]-before[0])*(before[3]-before[1])+(after[2]-after[0])*(after[3]-after[1])-intersection
                        if not union or intersection/union<.65:raise ValueError('OCR_SEGMENTATION_CHANGED')
                if items:recognized[page]=items
                results.append({'page':page,'status':'recognized' if items else 'empty','blocks':len(items)})
                if not items:notices.append(f'{page}쪽에서는 인식한 글자가 없습니다. 기존 내용은 유지합니다.')
            except ReanalysisError:raise
            except Exception as error:
                code=str(error) if str(error) in {'OCR_UNAVAILABLE','OCR_LANGUAGE_UNAVAILABLE','OCR_PAGE_INVALID','OCR_PAGE_TOO_LARGE','OCR_SEGMENTATION_CHANGED','OCR_FAILED'} else 'OCR_FAILED'
                results.append({'page':page,'status':'failed','blocks':0,'error':code})
                notices.append(f'{page}쪽은 이전에 읽은 문단 구분과 달라 중복을 막기 위해 기존 결과를 유지합니다.' if code=='OCR_SEGMENTATION_CHANGED'
                               else f'{page}쪽의 글자를 인식하지 못했습니다. 원본과 기존 내용은 유지합니다.')
        blocks=[];sources=[]
        for block in old_blocks:
            source=old_sources[block.id]
            if source.get('origin')=='pdf_ocr' and block.page in recognized:continue
            blocks.append(source['block'].model_copy(deep=True))
            sources.append({key:value for key,value in source.items() if key!='block'})
        additional=[]
        for page,items in recognized.items():
            previous=[block for block in old_blocks if block.page is not None and block.page<=page and block.section_path]
            section=list(previous[-1].section_path) if previous else []
            # A scanned appendix can follow the native bibliography. OCR has
            # not established its section, so never silently exclude its text
            # from translation by inheriting a references-only scope.
            if any(part.casefold() in {'references','bibliography'} for part in section):section=[]
            for index,(item,result) in enumerate(items):
                block=Block(id=f'ocr-p{page:04d}-{index:04d}',doc_id=doc_id,order=0,type=item['type'],section_path=section,
                            page=page,en=inline(item['en']),ocr=True)
                source={'origin':'pdf_ocr','page':page,'bbox':list(item['bbox']),'region':list(item['bbox']),
                        'source_anchor':f'ocr-p{page:04d}-{index:04d}','references':any(part.casefold() in {'references','bibliography'} for part in section),
                        'needs_restore':False,'ocr_language':result.get('language',request['language']),'ocr_dpi':result.get('dpi',300),
                        'ocr_engine':result.get('engine','tesseract'),'ocr_source_hash':fingerprint}
                additional.append((block,source))
        self._insert_page_blocks(blocks,sources,additional)
        return blocks,sources,'ocr',notices,results

    def _extract(self, doc_id, row, directory, old_blocks, old_sources):
        original = self.store.asset_path(doc_id, row['source_path'])
        if not original or original.suffix.lower() != '.pdf':
            raise ReanalysisError('REANALYSIS_UNSUPPORTED')
        notices = ['다시 분석은 저장된 원문으로만 진행하며 AI를 호출하지 않습니다.']
        html = self.store.asset_path(doc_id, f'documents/{doc_id}/source.html')
        if html:
            try:
                from .arxiv import parse_html, visual_asset_versions
                from .diagnostics import _valid_picture
                from .html_visual_fallback import resolve_pdf_visuals
                _, blocks, sources, images = parse_html(doc_id, html.read_text(encoding='utf-8'), allow_visual_fallback=True)
                matching = [old_sources.get(b.id, {}).get('block', b) for b in old_blocks]
                pairs = match_blocks(matching, old_sources, blocks, sources)
                by_index = {index: old_id for old_id, index in pairs if old_id is not None and index is not None}
                previous = {b.id: b for b in old_blocks}
                pending = {b.id for b, source in zip(blocks, sources) if source.get('visual_fallback_reason')}
                for index, block in enumerate(blocks):
                    if block.id not in images or block.id in pending:
                        continue
                    old_id = by_index.get(index)
                    old = previous.get(old_id)
                    source = old_sources.get(old_id, {})
                    saved_original = source.get('block', old)
                    same_urls = (not source.get('visual_source_urls') or
                                 source['visual_source_urls'] == sources[index].get('visual_source_urls'))
                    if (old and source.get('origin') == 'arxiv_html'
                            and same_urls
                            and source_signature(saved_original) == source_signature(block)
                            and old.image_path and _valid_picture(self.store, doc_id, old.image_path)):
                        block.image_path = old.image_path
                    else:
                        sources[index]['visual_fallback_reason'] = source.get('visual_fallback_reason') or 'html_images'
                        pending.add(block.id)
                if pending:
                    if row['arxiv_id']:
                        job = self.store.job(doc_id)
                        source_id = (job['checkpoint'].get('source_arxiv_id') if job else None) or row['arxiv_id']
                        if (not re.search(r'v[1-9]\d*$', source_id)
                                or visual_asset_versions(source_id, sources) - {source_id}):
                            raise ValueError('Saved HTML and PDF revision cannot be established')
                    resolved = resolve_pdf_visuals(self.pipeline.parser, original, blocks, sources, pending,
                        directory / 'crops', image_prefix=f'documents/{doc_id}/reanalysis/{directory.name}/crops')
                    if resolved != pending:
                        raise ValueError('Saved HTML visual has no certain PDF match')
                    notices.append(f'HTML에서 보존하기 어려운 그림·표 {len(resolved)}개만 원본 PDF 영역으로 대체해 비교합니다.')
                    blocks,sources=self._reuse_ocr(original,old_blocks,old_sources,blocks,sources)
                    return blocks, sources, 'mixed', notices
                blocks,sources=self._reuse_ocr(original,old_blocks,old_sources,blocks,sources)
                return blocks, sources, 'arxiv_html', notices
            except (ValueError, UnicodeError):
                notices.append('저장된 HTML의 일부 구조를 안전하게 보존하기 어려워 원본 PDF로 비교합니다.')
        metadata = self.pipeline.parser.metadata(original)
        crops = directory / 'crops'
        pages = [self.pipeline.parser.page(original, page, crops) for page in range(metadata['page_count'])]
        blocks, sources = self.pipeline.parser.structure(doc_id, pages, metadata)
        for block in blocks:
            if block.image_path:
                block.image_path = f'documents/{doc_id}/reanalysis/{directory.name}/crops/{Path(block.image_path).name}'
        blocks,sources=self._reuse_ocr(original,old_blocks,old_sources,blocks,sources)
        if not blocks:raise ReanalysisError('REANALYSIS_FAILED')
        return blocks, sources, 'pdf', notices

    @staticmethod
    def _has_translation(block):
        return bool(block and (block.ko or block.caption_ko or (block.table and any(c.text_ko for row in block.table.header + block.table.body for c in row))))

    def _preview(self, doc_id, row, baseline, old_blocks, old_sources, directory):
        blocks, sources, method, notices = self._extract(doc_id, row, directory, old_blocks, old_sources)
        return self._build_preview(doc_id,baseline,old_blocks,old_sources,directory,blocks,sources,method,notices)

    def _preview_ocr(self,doc_id,row,baseline,old_blocks,old_sources,directory,request):
        blocks,sources,method,notices,results=self._extract_ocr(doc_id,row,old_blocks,old_sources,request)
        manifest=self._build_preview(doc_id,baseline,old_blocks,old_sources,directory,blocks,sources,method,notices)
        manifest['ocr']={**request,'results':results}
        manifest['_ocr_request']=request
        return manifest

    def _build_preview(self,doc_id,baseline,old_blocks,old_sources,directory,blocks,sources,method,notices):
        for block in old_blocks:
            old_sources.setdefault(block.id, {})['image_fingerprint'] = self._image_hash(doc_id, block)
        for block, source in zip(blocks, sources):
            source['image_fingerprint'] = self._image_hash(doc_id, block)
        matching = [old_sources.get(b.id, {}).get('block', b) for b in old_blocks]
        pairs = match_blocks(matching, old_sources, blocks, sources)
        previous = {b.id: b for b in old_blocks}
        old_order = {b.id: i for i, b in enumerate(old_blocks)}
        matched_order = [(old_id, index) for old_id, index in pairs if old_id is not None and index is not None]
        reordered = set()
        largest=-1
        for old_id,_ in matched_order:
            rank=old_order[old_id]
            if rank<largest:reordered.add(old_id)
            largest=max(largest,rank)
        smallest=len(old_order)
        for old_id,_ in reversed(matched_order):
            rank=old_order[old_id]
            if rank>smallest:reordered.add(old_id)
            smallest=min(smallest,rank)
        changes, provenance = [], {}
        for old_id, index in pairs:
            old = previous.get(old_id)
            new = blocks[index] if index is not None else None
            original = old_sources.get(old_id, {}).get('block', old)
            source_same = bool(old and new and source_signature(original) == source_signature(new))
            if old is None:
                kind, reason = 'added', '새로 찾은 원문입니다.'
            elif new is None:
                kind, reason = 'missing', '새 결과에서 찾지 못했으므로 기존 원문과 번역을 유지합니다.'
            else:
                before = old_sources.get(old_id, {})
                after = sources[index]
                if source_same and old.image_path and self.store.asset_path(doc_id,old.image_path) and not new.image_path:
                    new.image_path=old.image_path
                    new.page=old.page
                    for key in ('bbox','region','image_fingerprint'):
                        after[key]=before.get(key)
                layout = old.page != new.page or old.section_path != new.section_path or any(before.get(k) != after.get(k) for k in ('bbox', 'region')) or old_id in reordered
                image = before.get('image_fingerprint') != after.get('image_fingerprint')
                kind = 'changed' if not source_same or layout or image else 'unchanged'
                reason = ('원문 내용이 달라져 새 블록으로 추가합니다. 기존 번역과 메모는 이전 원문에서 유지합니다.'
                          if not source_same else '원문 그림·배치·읽기 위치를 개선합니다. 기존 번역을 유지합니다.' if kind == 'changed' else '변경이 없습니다.')
            change_id = f'c{len(changes):04d}'
            changes.append({'id': change_id, 'kind': kind, 'old_block': old.model_dump(exclude_none=True) if old else None,
                            'new_block': new.model_dump(exclude_none=True) if new else None,
                            'translation_kept': self._has_translation(old), 'requires_translation': bool(new and not source_same and
                                ((new.type in {'sec','sub','ssub','p','li'} and new.en) or new.caption_en or
                                 (new.table and any(not cell.numeric and cell.text_en.strip() for row in new.table.header for cell in row)))),
                            'selectable': kind in {'added', 'changed'}, 'reason': reason})
            if index is not None:
                provenance[change_id] = {k: v for k, v in sources[index].items() if k != 'block'}
        notices.append('원문 내용이 바뀐 부분은 새 블록으로 추가하며, 기존 번역·주석·메모·대화는 이전 원문에서 유지합니다.')
        notices.append('적용 후 새 원문의 번역은 처리 상태에서 시작할 수 있습니다. 기존 주석은 자동으로 다시 만들지 않습니다.')
        return {'id': directory.name, 'doc_id': doc_id, 'method': method, 'created_at': datetime.now(timezone.utc).isoformat(),
                'applied': False, 'changes': changes, 'counts': {kind: sum(c['kind'] == kind for c in changes) for kind in ('added', 'changed', 'missing', 'unchanged')},
                'notices': notices, '_baseline': baseline, '_sources': provenance, '_parser_revision': PARSER_REVISION,
                '_reanalysis_revision': REANALYSIS_REVISION}

    @staticmethod
    def _public(manifest, row=None):
        result = {k: v for k, v in manifest.items() if not k.startswith('_')}
        if row is not None:
            result['applied'] = bool(row['applied'])
        return result

    def _load(self, db, doc_id, preview_id):
        if not re.fullmatch(r'[a-f0-9]{32}', preview_id):
            raise ReanalysisError('REANALYSIS_NOT_FOUND')
        row = db.execute('SELECT * FROM document_reanalyses WHERE id=? AND doc_id=?', (preview_id, doc_id)).fetchone()
        path = self.store.asset_path(doc_id, row['manifest_path']) if row else None
        if not path:
            raise ReanalysisError('REANALYSIS_NOT_FOUND')
        return row, json.loads(path.read_text(encoding='utf-8'))

    def _assets_valid(self,doc_id,manifest,selected=None):
        for change in manifest['changes']:
            if manifest['method']=='ocr' and not change['selectable']:continue
            if selected is not None and change['id'] not in selected:
                continue
            block=change.get('new_block')
            if not block or not block.get('image_path'):
                continue
            image=self.store.asset_path(doc_id,block['image_path'])
            expected=manifest['_sources'][change['id']].get('image_fingerprint')
            if not image or not expected or self._hash_file(image)!=expected:
                return False
        return True

    async def create(self,doc_id):
        return await self._create(doc_id)

    async def create_ocr(self,doc_id,pages,language='eng'):
        if (not isinstance(pages,list) or not 1<=len(pages)<=OCR_MAX_PAGES
                or any(type(page) is not int for page in pages) or len(set(pages))!=len(pages)):
            raise ReanalysisError('OCR_SELECTION_INVALID')
        if language not in {'eng','kor','eng+kor'}:raise ReanalysisError('OCR_LANGUAGE_UNAVAILABLE')
        information=await asyncio.to_thread(self.ocr_candidates,doc_id)
        if information['busy']:raise ReanalysisError('JOB_BUSY')
        if not information['available']:raise ReanalysisError('OCR_UNAVAILABLE')
        if information['source_extraction_required']:raise ReanalysisError('OCR_EXTRACTION_REQUIRED')
        if language not in information['languages']:raise ReanalysisError('OCR_LANGUAGE_UNAVAILABLE')
        indexed={page['page']:page for page in information['pages']}
        if any(page not in indexed for page in pages):raise ReanalysisError('OCR_PAGE_INVALID')
        if any(indexed[page]['has_text'] for page in pages):raise ReanalysisError('OCR_PAGE_HAS_TEXT')
        return await self._create(doc_id,{'pages':sorted(pages),'language':language})

    async def _create(self,doc_id,ocr=None):
        async with self.lock(doc_id):
            with self.store.connect() as db:
                row = dict(self._row(db, doc_id))
                self._busy(db, doc_id)
                baseline = self._fingerprint(db, doc_id)
                cached_records=db.execute('SELECT id FROM document_reanalyses WHERE doc_id=? AND baseline=? AND applied=0 ORDER BY rowid DESC',(doc_id,baseline)).fetchall()
                for cached in cached_records:
                    try:cached_row,manifest=self._load(db,doc_id,cached['id'])
                    except (ReanalysisError,ValueError,OSError):continue
                    if (manifest.get('_parser_revision') == PARSER_REVISION
                            and manifest.get('_reanalysis_revision') == REANALYSIS_REVISION and manifest.get('_ocr_request')==ocr
                            and (ocr is None or all(result['status']=='recognized' for result in manifest.get('ocr',{}).get('results',[])))
                            and self._assets_valid(doc_id,manifest)):
                        return self._public(manifest, cached_row)
                all_blocks = [Block.model_validate_json(r[0]) for r in db.execute('SELECT payload FROM blocks WHERE doc_id=? ORDER BY sort_order', (doc_id,))]
                sources = self._sources(db, doc_id)
                old = [b for b in all_blocks if b.id in sources and not b.superseded_by and not b.note and not b.card]
                if not old and ocr is None:
                    raise ReanalysisError('REANALYSIS_UNSUPPORTED')
            directory = (self.store.data_dir / 'documents' / doc_id / 'reanalysis' / uuid.uuid4().hex).resolve()
            directory.mkdir(parents=True)
            try:
                arguments=(doc_id,row,baseline,old,sources,directory)
                worker=asyncio.create_task(asyncio.to_thread(self._preview_ocr if ocr else self._preview,*arguments,*([ocr] if ocr else [])))
                try:
                    manifest=await asyncio.shield(worker)
                except asyncio.CancelledError:
                    # Native extraction cannot be interrupted halfway through a
                    # write. Let it finish before removing its private staging.
                    try:await worker
                    except Exception:pass
                    raise
                if not self._assets_valid(doc_id,manifest):
                    raise ReanalysisError('REANALYSIS_FAILED')
                if self.store.summary(doc_id):
                    manifest['notices'].append('기존 요약은 이전 원문의 근거와 함께 유지됩니다. 새 원문까지 반영하려면 요약에서 다시 만들어 주세요.')
                path = f'documents/{doc_id}/reanalysis/{directory.name}/manifest.json'
                atomic_json(directory / 'manifest.json', manifest)
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._row(db, doc_id)
                    self._busy(db, doc_id)
                    if self._fingerprint(db, doc_id) != baseline:
                        raise ReanalysisError('REANALYSIS_STALE')
                    db.execute('INSERT INTO document_reanalyses(id,doc_id,baseline,manifest_path) VALUES(?,?,?,?)', (directory.name, doc_id, baseline, path))
                return self._public(manifest)
            except BaseException:
                # The directory is always a freshly generated child, never a
                # caller-supplied path or an existing source/asset directory.
                if directory.parent.name == 'reanalysis' and re.fullmatch(r'[a-f0-9]{32}', directory.name):
                    shutil.rmtree(directory, ignore_errors=True)
                raise

    def get(self, doc_id, preview_id):
        with self.store.connect() as db:
            self._row(db, doc_id)
            row, manifest = self._load(db, doc_id, preview_id)
            return self._public(manifest, row)

    def image(self, doc_id, preview_id, change_id, side):
        with self.store.connect() as db:
            self._row(db, doc_id)
            _, manifest = self._load(db, doc_id, preview_id)
        change = next((c for c in manifest['changes'] if c['id'] == change_id), None)
        block = change.get('old_block' if side == 'before' else 'new_block') if change else None
        path = self.store.asset_path(doc_id, block.get('image_path', '')) if block else None
        if not path or path.suffix.lower() != '.png':
            raise ReanalysisError('REANALYSIS_NOT_FOUND')
        return path.read_bytes()

    @staticmethod
    def _family(db, doc_id, block_id):
        anchors = [(r[0], r[1]) for r in db.execute('SELECT note_id,after_block_id FROM note_anchors WHERE doc_id=?', (doc_id,))]
        result = {block_id}
        while True:
            expanded = result | {note for note, parent in anchors if parent in result}
            if expanded == result:
                return result
            result = expanded

    async def apply(self, doc_id, preview_id, change_ids):
        if not change_ids or len(change_ids) != len(set(change_ids)):
            raise ReanalysisError('REANALYSIS_SELECTION_INVALID')
        async with self.lock(doc_id):
            if self.pipeline.work_lock.locked():
                raise ReanalysisError('JOB_BUSY')
            async with self.pipeline.work_lock:
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._row(db, doc_id)
                    self._busy(db, doc_id)
                    record, manifest = self._load(db, doc_id, preview_id)
                    if record['applied']:
                        if set(json.loads(record['applied_ids'])) != set(change_ids):
                            raise ReanalysisError('REANALYSIS_STALE')
                        count = len(change_ids)
                    else:
                        if self._fingerprint(db, doc_id) != record['baseline']:
                            raise ReanalysisError('REANALYSIS_STALE')
                        selected = {c['id'] for c in manifest['changes'] if c['selectable']}
                        if not set(change_ids) <= selected:
                            raise ReanalysisError('REANALYSIS_SELECTION_INVALID')
                        if not self._assets_valid(doc_id,manifest,set(change_ids)):
                            raise ReanalysisError('REANALYSIS_STALE')
                        count = self._apply(db, doc_id, manifest, set(change_ids))
                        db.execute('UPDATE document_reanalyses SET applied=1,applied_ids=?,applied_at=CURRENT_TIMESTAMP WHERE id=?', (json.dumps(sorted(change_ids)), preview_id))
                return {'applied': count, 'document': self.store.document(doc_id)}

    def _apply(self, db, doc_id, manifest, selected):
        ordered = [r[0] for r in db.execute('SELECT id FROM blocks WHERE doc_id=? ORDER BY sort_order', (doc_id,))]
        old_max = max([r[0] for r in db.execute('SELECT sort_order FROM blocks WHERE doc_id=?', (doc_id,))] or [-1])
        next_order = old_max + 1
        anchors = {i: c['old_block']['id'] for i, c in enumerate(manifest['changes']) if c['old_block'] and c['new_block']}
        new_ids, changed_source = set(), False
        for index, change in enumerate(manifest['changes']):
            if change['id'] not in selected:
                continue
            incoming = Block.model_validate(change['new_block'])
            old = self.store.block(doc_id, change['old_block']['id']) if change['old_block'] else None
            original = db.execute('SELECT payload FROM source_blocks WHERE doc_id=? AND block_id=?', (doc_id, old.id)).fetchone() if old else None
            same = bool(original and source_signature(Block.model_validate_json(original[0])) == source_signature(incoming))
            source = manifest['_sources'][change['id']]
            if same:
                block = old.model_copy(update={'page': incoming.page, 'image_path': incoming.image_path, 'section_path': incoming.section_path})
                db.execute('UPDATE blocks SET payload=?,section_path=? WHERE doc_id=? AND id=?', (block.model_dump_json(exclude_none=True), json.dumps(block.section_path), doc_id, old.id))
                updated_source = Block.model_validate_json(original[0]).model_copy(update={'page': incoming.page, 'image_path': incoming.image_path, 'section_path': incoming.section_path})
                db.execute('UPDATE source_blocks SET payload=?,provenance=? WHERE doc_id=? AND block_id=?', (updated_source.model_dump_json(exclude_none=True), json.dumps(source, ensure_ascii=False), doc_id, old.id))
                anchor = old.id
                family = self._family(db, doc_id, anchor)
                moving = [bid for bid in ordered if bid in family]
                ordered = [bid for bid in ordered if bid not in family]
            else:
                changed_source = True
                incoming.id = f'r{manifest["id"]}-{index:04d}'
                incoming.order = next_order
                incoming.doc_id = doc_id
                incoming.ko = incoming.caption_ko = None
                incoming.superseded_by = None
                if incoming.table:
                    for row in incoming.table.header + incoming.table.body:
                        for cell in row:
                            cell.text_ko = None
                if old:
                    old.superseded_by = incoming.id
                    db.execute('UPDATE blocks SET payload=? WHERE doc_id=? AND id=?', (old.model_dump_json(exclude_none=True), doc_id, old.id))
                db.execute('INSERT INTO blocks(id,doc_id,sort_order,type,section_path,payload) VALUES(?,?,?,?,?,?)',
                           (incoming.id, doc_id, next_order, incoming.type, json.dumps(incoming.section_path), incoming.model_dump_json(exclude_none=True)))
                next_order += 1
                db.execute('INSERT INTO source_blocks(doc_id,block_id,payload,provenance) VALUES(?,?,?,?)',
                           (doc_id, incoming.id, incoming.model_dump_json(exclude_none=True), json.dumps(source, ensure_ascii=False)))
                anchor, moving = incoming.id, [incoming.id]
                if old:
                    family=self._family(db,doc_id,old.id)
                    moving=[bid for bid in ordered if bid in family]+moving
                    ordered=[bid for bid in ordered if bid not in family]
                new_ids.add(incoming.id)
            before = [anchors[i] for i in sorted(anchors) if i < index and anchors[i] in ordered]
            after = [anchors[i] for i in sorted(anchors) if i > index and anchors[i] in ordered]
            # Keep the old source and its linked notes immediately before its
            # replacement; those references still describe that original text.
            if before:
                family = self._family(db, doc_id, before[-1])
                position = max(ordered.index(bid) for bid in family if bid in ordered) + 1
            elif after:
                position = ordered.index(after[0])
            else:
                position = len(ordered)
            ordered[position:position] = moving
            anchors[index] = anchor
        # Two-phase order update avoids the UNIQUE(doc_id,sort_order) constraint.
        offset = next_order + len(ordered) + 1
        db.execute('UPDATE blocks SET sort_order=sort_order+? WHERE doc_id=?', (offset, doc_id))
        for order, block_id in enumerate(ordered):
            payload = json.loads(db.execute('SELECT payload FROM blocks WHERE doc_id=? AND id=?', (doc_id, block_id)).fetchone()[0])
            payload['order'] = order
            db.execute('UPDATE blocks SET sort_order=?,payload=? WHERE doc_id=? AND id=?', (order, json.dumps(payload, ensure_ascii=False), doc_id, block_id))
        if manifest['method']=='mixed':
            db.execute("UPDATE documents SET source_kind='mixed' WHERE id=?",(doc_id,))
        elif manifest['method'] in {'pdf','ocr'}:
            db.execute("UPDATE documents SET source_kind='mixed' WHERE id=? AND source_kind='arxiv_html'",(doc_id,))
        elif manifest['method']=='arxiv_html':
            db.execute("UPDATE documents SET source_kind='mixed' WHERE id=? AND source_kind='pdf'",(doc_id,))
        job=db.execute('SELECT checkpoint FROM jobs WHERE doc_id=?',(doc_id,)).fetchone()
        if job:
            checkpoint=json.loads(job['checkpoint'] or '{}')
            checkpoint.update(reanalysis_id=manifest['id'],parser_revision=PARSER_REVISION)
            checkpoint['source_kind'] = db.execute('SELECT source_kind FROM documents WHERE id=?', (doc_id,)).fetchone()[0]
            if manifest['method'] in {'arxiv_html', 'mixed'}:
                checkpoint.pop('source_fallback_reason', None)
            active_sources = db.execute('SELECT s.block_id,s.provenance,b.payload FROM source_blocks s JOIN blocks b '
                                       'ON b.doc_id=s.doc_id AND b.id=s.block_id WHERE s.doc_id=?', (doc_id,)).fetchall()
            checkpoint['visual_fallbacks'] = []
            checkpoint['ocr_pages']=[]
            for saved in active_sources:
                payload = json.loads(saved['payload'])
                provenance = json.loads(saved['provenance'])
                if not payload.get('superseded_by') and provenance.get('visual_fallback_reason'):
                    checkpoint['visual_fallbacks'].append({'block_id': saved['block_id'],
                        'reason': provenance['visual_fallback_reason'], 'page': payload.get('page')})
                if not payload.get('superseded_by') and provenance.get('origin')=='pdf_ocr' and payload.get('page'):
                    checkpoint['ocr_pages'].append(payload['page'])
            checkpoint['ocr_pages']=sorted(set(checkpoint['ocr_pages']))
            if db.execute("SELECT 1 FROM blocks WHERE doc_id=? AND type='note' LIMIT 1",(doc_id,)).fetchone() or db.execute('SELECT 1 FROM annotation_sections WHERE doc_id=? LIMIT 1',(doc_id,)).fetchone():
                checkpoint['preserve_annotations']=True
            db.execute('UPDATE jobs SET checkpoint=? WHERE doc_id=?',(json.dumps(checkpoint,ensure_ascii=False),doc_id))
        if changed_source:
            db.execute('DELETE FROM ai_results WHERE doc_id=?', (doc_id,))
            job = db.execute('SELECT checkpoint FROM jobs WHERE doc_id=?', (doc_id,)).fetchone()
            if job:
                checkpoint = json.loads(job['checkpoint'] or '{}')
                checkpoint.update(error=None, reanalysis_id=manifest['id'])
                checkpoint['completed_stages'] = list(dict.fromkeys(checkpoint.get('completed_stages', []) + ['Ingest', 'Extract', 'Structure']))
                active=[Block.model_validate_json(r[0]) for r in db.execute('SELECT payload FROM blocks WHERE doc_id=?',(doc_id,))]
                source_map=self._sources(db,doc_id)
                targets=[b for b in active if not b.superseded_by and b.id in source_map and not source_map[b.id].get('references')
                         and (b.type in {'sec','sub','ssub','p','li'} or b.caption_en or b.table)]
                pending=any(not translation_complete(b,source_map[b.id]['block']) for b in targets)
                restore=any(b.id in new_ids and b.type in {'tab','eq'} and b.image_path and not (b.table or b.latex) for b in active)
                status='ready_to_translate' if pending or restore else 'complete'
                db.execute("UPDATE jobs SET stage='Translate',status=?,checkpoint=?,updated_at=CURRENT_TIMESTAMP WHERE doc_id=?", (status, json.dumps(checkpoint, ensure_ascii=False), doc_id))
                db.execute('UPDATE documents SET status=? WHERE id=?', (status, doc_id))
        return len(selected)
