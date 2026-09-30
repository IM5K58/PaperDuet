import asyncio
import contextlib
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from pydantic import Field

from .adapter import inline, parse_table, plain
from .models import Model, PipelineOptions
from .pdf_parser import PyMuPDFParser
from .provider import ProviderError
from .validation import number_tokens, numeric_locations, suspicious_table, table_grid, validate_block
from .annotations import AnnotationBatch, first_occurrences, glossary_candidates, make_notes, section_batches, section_window, validate_note
from .table_style import decorate_tables


class GlossaryEntry(Model):
    term: str = Field(min_length=1,max_length=200)
    ko: str = Field(min_length=1,max_length=200)
    keep_english: bool
    definition_ko: str = Field(max_length=2000)


class HeaderTranslation(Model):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text_ko: str = Field(max_length=5000)


class Translation(Model):
    id: str
    ko: str | None = Field(default=None,max_length=100000)
    caption_ko: str | None = Field(default=None,max_length=20000)
    headers: list[HeaderTranslation] = Field(default_factory=list)


class TranslationBatch(Model):
    blocks: list[Translation]


def parse_glossary(result):
    """Keep every usable entry. A short, duplicated or partly malformed glossary
    only weakens consistency; rejecting it would stop the job with the same
    answer on every resume."""
    if isinstance(result,dict):result=next((v for v in result.values() if isinstance(v,list)),[])
    glossary=[];seen=set()
    for item in result if isinstance(result,list) else []:
        try:entry=GlossaryEntry.model_validate(item)
        except ValueError:continue
        if entry.term.casefold() not in seen:seen.add(entry.term.casefold());glossary.append(entry)
    return glossary[:80]


def glossary_for(glossary, texts, definitions=()):
    """Only the glossary entries a batch actually mentions; definitions only for
    terms named in `definitions` (their first use is being annotated)."""
    joined=' '.join(plain(t) for t in texts if t)
    result=[]
    for g in glossary:
        if re.search(r'(?<!\w)'+re.escape(g['term'])+r'(?!\w)',joined,re.I):
            item={'term':g['term'],'ko':g['ko']}
            if g.get('keep_english'):item['keep_english']=True
            if g['term'] in definitions:item['definition_ko']=g['definition_ko']
            result.append(item)
    return result


def compact(b, limit=None):
    """English-only view of a block for annotation prompts; tables as pipe rows,
    paragraphs (the common case) without a type. `limit` trims long evidence
    paragraphs; tables and equations stay whole."""
    en=b.en if not limit or not b.en or len(b.en)<=limit else b.en[:limit].rsplit(' ',1)[0]+' …'
    item={k:v for k,v in {'id':b.id,'type':None if b.type=='p' else b.type,'n':b.n,'en':en,'caption_en':b.caption_en,'latex':b.latex}.items() if v}
    if b.table:
        item['table']='\n'.join(' | '.join(plain(c.text_en) for c in row) for row in b.table.header+b.table.body)
    if b.card:item['card']=b.card.body
    return item


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    os.replace(temp,path)


# Job statuses during which the paper belongs to the pipeline.
BUSY = {'queued', 'running', 'batch_waiting'}
# Answers a work unit handles itself; every other provider error pauses the job.
SOFT = {'AI_INVALID_JSON', 'AI_OUTPUT_LIMIT'}
MISSING = object()


@dataclass
class AIRequest:
    """One model call a work unit needs. The key is stable across restarts, so a
    replayed unit is handed the answers its job already received (and paid for)."""
    tag: str
    stage: str
    model: str
    payload: dict
    image: Path | None = None
    key: str = field(init=False)

    def __post_init__(self):
        material = json.dumps([self.tag, self.stage, self.model, self.payload], ensure_ascii=False, sort_keys=True)
        self.key = 'r' + hashlib.sha256(material.encode()).hexdigest()[:40]


def advance(unit, value=None):
    """The next request of a work unit, or None once it has finished."""
    try:
        return unit.send(value)
    except StopIteration:
        return None


def tracked(unit, done):
    """Run `done` (the unit's checkpoint) once the unit has finished."""
    yield from unit
    done()


def trusted_table(source):
    """The PDF parser's own grid, when it can be believed. A suspicious grid is
    restored from the page image instead and never used as the reference."""
    return bool(source.table) and not suspicious_table(source.table,source.en or '')


class Pipeline:
    def __init__(self, store, provider, parser=None):
        self.store,self.provider=store,provider
        self.parser=parser or PyMuPDFParser()
        self.tasks={}
        self.closing=False
        self.work_lock=asyncio.Lock()
        # Batch polling: first wait, longest wait, and the wait while cancelling (s).
        self.poll=(10,60,5)

    def start(self,doc_id):
        if doc_id in self.tasks and not self.tasks[doc_id].done():
            return
        self.tasks[doc_id]=asyncio.create_task(self.run(doc_id))

    async def discard(self,doc_id):
        """Stop a paper's processing before it is deleted so nothing writes it back."""
        task=self.tasks.pop(doc_id,None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        job=self.store.job(doc_id)
        pending=job and job['checkpoint'].get('pending_batch')
        if pending:
            # Unanswered requests of a cancelled batch are not billed.
            with contextlib.suppress(Exception):
                await self.batch_adapter(pending).cancel_batch(pending['handle'])

    async def recover(self):
        with self.store.connect() as db:
            # Upgrade untouched extraction previews only. Translations, notes and
            # conversations retain their stable IDs and are never overwritten.
            for row in db.execute('SELECT doc_id,checkpoint FROM jobs').fetchall():
                checkpoint=json.loads(row['checkpoint'] or '{}')
                if checkpoint.get('parser_revision',1)>=2 or checkpoint.get('ai_approved'):continue
                payloads=[json.loads(r[0]) for r in db.execute('SELECT payload FROM blocks WHERE doc_id=?',(row['doc_id'],))]
                edited=any(b.get('ko') or b.get('caption_ko') or b.get('note') for b in payloads)
                if edited or db.execute('SELECT 1 FROM threads WHERE doc_id=?',(row['doc_id'],)).fetchone() or db.execute('SELECT 1 FROM presentation_notes WHERE doc_id=?',(row['doc_id'],)).fetchone():continue
                checkpoint.update(parser_revision=2,reextract=True,completed_stages=['Ingest'],extracted_pages=0,translated=[],restored=[])
                db.execute("UPDATE jobs SET status='queued',stage='Extract',progress=0,checkpoint=? WHERE doc_id=?",(json.dumps(checkpoint),row['doc_id']))
            ids=[r[0] for r in db.execute("SELECT doc_id FROM jobs WHERE status IN ('queued','running','batch_waiting')")]
            finished=[r[0] for r in db.execute("SELECT doc_id FROM jobs WHERE status IN ('review','complete')")]
        for doc_id in finished:
            self.flag_suspicious_tables(doc_id)
        for doc_id in ids:
            self.start(doc_id)

    def flag_suspicious_tables(self,doc_id):
        """Papers processed before suspicious grids were detected: mark those tables
        for review (the reader then shows the page image, and regenerating restores
        the table from it). Idempotent."""
        doc=self.store.document(doc_id)
        if not doc:return
        sources=self.store.sources(doc_id)
        marked=[b for b in doc.blocks if b.type=='tab' and b.table and 'V4' not in b.qa_flags and b.id in sources
                and suspicious_table(b.table,sources[b.id]['block'].en or '')]
        for b in marked:b.qa_flags=sorted(set(b.qa_flags+['V4']))
        if marked:
            self.store.save_blocks(doc_id,marked)
            self.refresh_review(doc_id)

    async def close(self):
        self.closing=True
        tasks=list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    def paused(self,doc_id):
        job=self.store.job(doc_id)
        return self.closing or not job or job["status"]=="paused"

    async def ingest(self,request):
        incoming=self.store.data_dir/'incoming'
        incoming.mkdir(exist_ok=True)
        temp=incoming/(uuid.uuid4().hex+'.pdf')
        digest=hashlib.sha256();size=0
        try:
            with temp.open('wb') as output:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>150*1024*1024:
                        raise ValueError('PDF_TOO_LARGE')
                    digest.update(chunk);output.write(chunk)
            with temp.open('rb') as check:
                signature=check.read(5)
            if size<5 or signature!=b'%PDF-':
                raise ValueError('INVALID_PDF')
            file_hash=digest.hexdigest()
            doc_id='pdf-'+file_hash[:24]
            with self.store.connect() as db:
                duplicate=db.execute("SELECT id FROM documents WHERE id=? OR (file_hash=? AND source_kind='pdf')",(doc_id,file_hash)).fetchone()
                if duplicate:
                    return {'doc_id':duplicate[0],'job_id':duplicate[0],'duplicate':True}
            metadata=await asyncio.to_thread(self.parser.metadata,temp)
            directory=self.store.data_dir/'documents'/doc_id
            directory.mkdir(parents=True,exist_ok=True)
            os.replace(temp,directory/'source.pdf')
            atomic_json(directory/'metadata.json',metadata)
            checkpoint={'ai_approved':False,'parser_revision':2,'options':self.store.pipeline_options().model_dump(),'completed_stages':['Ingest'],'extracted_pages':0,'translated':[],'restored':[],'error':None}
            with self.store.connect() as db:
                db.execute('INSERT INTO documents(id,title,arxiv_id,file_hash,source_path,status,page_count,authors) VALUES(?,?,?,?,?,?,?,?)',
                    (doc_id,metadata['title'],metadata['arxiv_id'],file_hash,f'documents/{doc_id}/source.pdf','queued',metadata['page_count'],metadata['authors']))
                db.execute('INSERT INTO jobs(id,doc_id,stage,status,checkpoint) VALUES(?,?,?,?,?)',(doc_id,doc_id,'Extract','queued',json.dumps(checkpoint)))
            self.start(doc_id)
            return {'doc_id':doc_id,'job_id':doc_id,'duplicate':False}
        finally:
            temp.unlink(missing_ok=True)

    def estimate(self,doc_id):
        doc=self.store.document(doc_id)
        if doc is None:
            return None
        chars=sum(len(b.en or '')+len(b.caption_en or '') for b in doc.blocks)
        images=sum(b.type in {'tab','eq'} for b in doc.blocks)
        return {'approximate':True,'input_tokens':int(chars/3*3+images*1600), 'output_tokens':int(chars/3*1.4),
                'restore_regions':images,'source_characters':chars,'options':self.store.pipeline_options().model_dump()}

    def approve(self,doc_id,options):
        job=self.store.job(doc_id)
        if not job:
            raise ValueError('Unknown job')
        if doc_id in self.tasks and not self.tasks[doc_id].done():
            raise ValueError('Job is already running')
        if job['status'] not in {'awaiting_ai','ready_to_translate','paused','failed','review','complete'}:
            raise ValueError('Job is not resumable')
        if options.batch and options.mode!='api_key':
            raise ValueError('Saving mode needs an API key connection')
        # Saving mode is chosen per paper; the stored default stays immediate.
        self.store.save_pipeline_options(options.model_copy(update={'batch':False}))
        self.store.update_job(doc_id,job['stage'],'queued',job['progress'],ai_approved=True,options=options.model_dump(),error=None)
        self.start(doc_id)

    def switch_to_realtime(self,doc_id):
        """'남은 부분 바로 처리': the waiting batch is cancelled, the answers it
        already has are kept, and the rest of the paper runs immediately."""
        job=self.store.job(doc_id)
        if not job or not job['checkpoint'].get('options',{}).get('batch'):
            raise ValueError('Not in saving mode')
        self.store.update_job(doc_id,job['stage'],job['status'],job['progress'],options={**job['checkpoint']['options'],'batch':False})

    def job_options(self,doc_id):
        job=self.store.job(doc_id)
        return PipelineOptions.model_validate(job['checkpoint']['options']) if job else self.store.pipeline_options()

    def adapter_for(self,options):
        return self.provider.adapter(options.provider,options.mode) if hasattr(self.provider,'adapter') else self.provider

    def batch_adapter(self,pending):
        return self.provider.adapter(pending['provider'],'api_key') if hasattr(self.provider,'adapter') else self.provider

    def record_usage(self,doc_id,stage,model,usage,payload_chars,batch=False):
        with self.store.connect() as db:
            db.execute('INSERT INTO ai_usage(doc_id,stage,model,tokens_in,tokens_out,cache_read,cache_write,requests,payload_chars,batch) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (doc_id,stage,model,usage.get('tokens_in',0),usage.get('tokens_out',0),usage.get('cache_read',0),usage.get('cache_write',0),
                 max(1,usage.get('requests',1)),payload_chars,int(batch)))

    async def call(self,doc_id,stage,model,payload,image=None):
        failure=None
        adapter=self.adapter_for(self.job_options(doc_id))
        try:
            value,usage=await adapter.json(stage,model,payload,image)
        except ProviderError as error:
            if not error.usage:raise
            usage=error.usage;failure=error
        self.record_usage(doc_id,stage,model,usage,len(json.dumps(payload,ensure_ascii=False)))
        if failure:raise failure
        return value

    # --- Running work units -------------------------------------------------
    # Each stage is a set of work units: generators that yield AIRequests and
    # receive the answers. Immediately, units run one after another. In saving
    # mode, every unit's next request goes into one provider batch per round,
    # so a paper needs about four rounds (images + glossary, translation,
    # annotation, retries) instead of ~36 separate calls.

    def cached(self,doc_id,request):
        saved=self.store.ai_result(doc_id,request.key)
        return MISSING if saved is None else saved['value']

    async def ask_now(self,doc_id,request,cache=True):
        value=self.cached(doc_id,request) if cache else MISSING
        if value is MISSING:
            try:value=await self.call(doc_id,request.stage,request.model,request.payload,request.image)
            except ProviderError as error:
                if str(error) not in SOFT:raise
                return error
            if cache:self.store.save_ai_result(doc_id,request.key,{'value':value})
        return value

    async def drive(self,doc_id,units,options,*,cache=True,pausable=True):
        """Run work units to completion; False if the job was paused on the way."""
        if not options.batch:
            for unit in units:
                if pausable and self.paused(doc_id):return False
                request=advance(unit)
                while request:request=advance(unit,await self.ask_now(doc_id,request,cache))
            return True
        active=[(unit,request) for unit in units if (request:=advance(unit))]
        while active:
            if self.paused(doc_id):return False
            answers=await self.resolve(doc_id,[request for _,request in active])
            if answers is None:return False
            active=[(unit,following) for unit,request in active if (following:=advance(unit,answers[request.key]))]
        return True

    async def resolve(self,doc_id,requests):
        """Answers for one round: replies the job already has, the rest from a
        provider batch. None when the job was paused while waiting."""
        answers={};todo=[]
        for request in requests:
            value=self.cached(doc_id,request)
            if value is MISSING:todo.append(request)
            else:answers[request.key]=value
        resubmitted=False
        while todo:
            options=self.job_options(doc_id)
            if not options.batch:  # switched to immediate while waiting
                for request in todo:answers[request.key]=await self.ask_now(doc_id,request)
                return answers
            fetched,stopped=await self.batch_round(doc_id,todo,options)
            again=[];fatal=None
            for request in todo:
                value=fetched[request.key]
                code=str(value) if isinstance(value,ProviderError) else None
                if code is None:
                    self.store.save_ai_result(doc_id,request.key,{'value':value});answers[request.key]=value
                elif code=='BATCH_RETRY' or (code=='AI_INVALID_JSON' and not resubmitted and not stopped):
                    again.append(request)  # expired, cancelled or server trouble: ask again
                elif code in SOFT:answers[request.key]=value
                else:fatal=fatal or value
            if fatal:raise fatal
            if stopped:
                if self.paused(doc_id):return None
                todo=again;continue  # switched to immediate: the next pass asks the rest now
            if again and resubmitted:raise ProviderError('BATCH_EXPIRED')
            resubmitted=True;todo=again
        return answers

    async def batch_round(self,doc_id,requests,options):
        """Submit one provider batch (or, after a restart, attach to the one
        already running) and wait for it. Returns ({key: answer or error}, stopped)."""
        adapter=self.adapter_for(options)
        keys=sorted(r.key for r in requests)
        pending=self.store.job(doc_id)['checkpoint'].get('pending_batch')
        if pending and (pending.get('keys')!=keys or pending.get('provider')!=options.provider):
            await self.settle_batch(doc_id,force=True)  # left over from a different plan
            pending=None
        if not pending:
            handle=await adapter.submit_batch([(r.key,r.stage,r.model,r.payload,r.image) for r in requests])
            pending={'provider':options.provider,'keys':keys,'handle':handle,'submitted_at':int(time.time()),'count':len(keys),
                     'requests':{r.key:[r.stage,r.model,len(json.dumps(r.payload,ensure_ascii=False))] for r in requests}}
        job=self.store.job(doc_id)
        self.store.update_job(doc_id,job['stage'],'batch_waiting',job['progress'],pending_batch=pending)
        stopped=False;delay=self.poll[0]
        while not await adapter.batch_done(pending['handle']):
            if not stopped and (self.paused(doc_id) or not self.job_options(doc_id).batch):
                stopped=True;await adapter.cancel_batch(pending['handle'])
            await asyncio.sleep(self.poll[2] if stopped else delay);delay=min(self.poll[1],delay*2)
        answers=await self.collect(doc_id,adapter,pending)
        return answers,stopped

    async def collect(self,doc_id,adapter,pending):
        """Take a finished batch's answers, bill each once, then let the provider
        delete its copy of the paper's text."""
        results=await adapter.batch_results(pending['handle'])
        for key,(_,usage) in results.items():
            stage,model,chars=pending['requests'].get(key,['batch','',0])
            if usage:self.record_usage(doc_id,stage,model,usage,chars,batch=True)
        job=self.store.job(doc_id)
        self.store.update_job(doc_id,job['stage'],'running',job['progress'],pending_batch=None)
        with contextlib.suppress(Exception):
            await adapter.forget_batch(pending['handle'])
        return {key:value for key,(value,_) in results.items()}

    async def settle_batch(self,doc_id,force=False):
        """A batch from saving mode that is no longer wanted (the job now runs
        immediately, or its plan changed): cancel it and keep what it answered."""
        job=self.store.job(doc_id)
        pending=job['checkpoint'].get('pending_batch') if job else None
        if not pending or (job['checkpoint']['options'].get('batch') and not force):return
        adapter=self.batch_adapter(pending)
        await adapter.cancel_batch(pending['handle'])
        for _ in range(60):  # cancelling takes a moment at the provider
            if await adapter.batch_done(pending['handle']):break
            await asyncio.sleep(self.poll[2])
        else:
            self.store.update_job(doc_id,job['stage'],job['status'],job['progress'],pending_batch=None)
            return
        for key,value in (await self.collect(doc_id,adapter,pending)).items():
            if not isinstance(value,ProviderError):self.store.save_ai_result(doc_id,key,{'value':value})

    async def run(self,doc_id):
        try:
            # A saving-mode job may wait hours on the provider; it must not hold
            # up other papers, so only immediate jobs take turns.
            job=self.store.job(doc_id)
            batch=bool(job and (job['checkpoint'].get('options') or {}).get('batch'))
            async with (contextlib.nullcontext() if batch else self.work_lock):
                await self._run(doc_id)
        except asyncio.CancelledError:
            # Keep running/queued persisted. The next process resumes checkpoints.
            raise
        except ProviderError as error:
            job=self.store.job(doc_id)
            status='awaiting_ai' if str(error)=='AI_CONNECTION_REQUIRED' else 'paused'
            self.store.update_job(doc_id,job['stage'],status,job['progress'],error=str(error))
        except Exception:
            job=self.store.job(doc_id)
            self.store.update_job(doc_id,job['stage'],'failed',job['progress'],error='PIPELINE_FAILED')

    async def _run(self,doc_id):
        job=self.store.job(doc_id)
        checkpoint=job['checkpoint']
        directory=self.store.data_dir/'documents'/doc_id
        metadata=json.loads((directory/'metadata.json').read_text(encoding='utf-8'))
        done=[s for s in checkpoint.get('completed_stages',[]) if s not in {'Validate','Render'}]
        if 'Structure' not in done:
            pages=[]
            for i in range(metadata['page_count']):
                if self.paused(doc_id): return
                cache=directory/('pages-v2' if checkpoint.get('parser_revision',1)>=2 else 'pages')/f'{i:04d}.json'
                self.store.update_job(doc_id,'Extract','running',i/metadata['page_count']*.25,error=None)
                if cache.exists():
                    result=json.loads(cache.read_text(encoding='utf-8'))
                else:
                    result=await asyncio.to_thread(self.parser.page,directory/'source.pdf',i,directory/'crops')
                    atomic_json(cache,result)
                pages.append(result)
                self.store.update_job(doc_id,'Extract','running',(i+1)/metadata['page_count']*.25,extracted_pages=i+1)
            self.store.update_job(doc_id,'Structure','running',.27)
            blocks,sources=self.parser.structure(doc_id,pages,metadata)
            if not blocks: raise ValueError('No readable PDF content')
            if checkpoint.get('reextract'):
                self.store.replace_extraction(doc_id,blocks,sources)
            else:self.store.save_blocks(doc_id,blocks,sources)
            decorate_tables(blocks)
            self.store.save_blocks(doc_id,blocks)
            done=list(dict.fromkeys(done+['Extract','Structure']))
            self.store.update_job(doc_id,'Structure','running',.3,completed_stages=done)
        if self.paused(doc_id): return
        if not checkpoint.get('ai_approved'):
            status='ready_to_translate' if self.provider.connected() else 'awaiting_ai'
            self.store.update_job(doc_id,'Glossary',status,.3,error=None)
            return
        options=PipelineOptions.model_validate(checkpoint['options'])
        adapter=self.adapter_for(options)
        if not adapter.connected():
            raise ProviderError('AI_CONNECTION_REQUIRED')
        if options.batch and not getattr(adapter,'supports_batch',False):
            raise ProviderError('BATCH_UNSUPPORTED')
        await self.settle_batch(doc_id)
        doc=self.store.document(doc_id)
        sources=self.store.sources(doc_id)
        # Round 1: table/equation images and the glossary do not depend on each other.
        restored=set(checkpoint.get('restored',[]))
        candidates=[b for b in doc.blocks if b.type in {'tab','eq'} and b.image_path and not sources[b.id]['references']
                    and not (b.type=='tab' and trusted_table(sources[b.id]['block']))]
        def block_restored(block):
            self.store.save_blocks(doc_id,[block]);restored.add(block.id)
            self.store.update_job(doc_id,'Restore','running',.3+.15*len(restored)/max(1,len(candidates)),restored=sorted(restored))
        def glossary_ready():
            done[:]=list(dict.fromkeys(done+['Restore','Glossary']))
            self.store.update_job(doc_id,'Glossary','running',.5,completed_stages=done)
        units=[tracked(self.restore_unit(doc_id,b,sources[b.id]['block'],options),partial(block_restored,b)) for b in candidates if b.id not in restored]
        if 'Glossary' not in done:units.append(tracked(self.glossary_unit(doc_id,doc,sources,options),glossary_ready))
        if units:self.store.update_job(doc_id,'Restore' if len(restored)<len(candidates) else 'Glossary','running',.3+.15*len(restored)/max(1,len(candidates)))
        if not await self.drive(doc_id,units,options) or self.paused(doc_id):return
        doc=self.store.document(doc_id)
        translated=set(checkpoint.get('translated',[]))
        targets=[b for b in doc.blocks if b.id in sources and not sources[b.id]['references'] and (b.type in {'sec','sub','ssub','p','li'} or b.caption_en or b.table)]
        batches=[];batch=[];length=0
        for b in targets:
            if b.id in translated: continue
            size=len(b.en or '')+len(b.caption_en or '')
            if batch and (length+size>12000 or len(batch)>=40):
                batches.append(batch);batch=[];length=0
            batch.append(b);length+=size
        if batch:batches.append(batch)
        def batch_translated(batch):
            translated.update(b.id for b in batch)
            self.store.save_blocks(doc_id,batch)
            self.store.update_job(doc_id,'Translate','running',.5+.28*len(translated)/max(1,len(targets)),translated=sorted(translated),translated_count=len(translated),total_blocks=len(targets))
        if batches:self.store.update_job(doc_id,'Translate','running',.5+.28*len(translated)/max(1,len(targets)),translated_count=len(translated),total_blocks=len(targets))
        units=[tracked(self.translate_unit(doc_id,batch,doc.glossary,sources,options,'translate:'+batch[0].id),partial(batch_translated,batch)) for batch in batches]
        if not await self.drive(doc_id,units,options) or self.paused(doc_id):return
        done=list(dict.fromkeys(done+['Translate']))
        doc=self.store.document(doc_id)
        groups=section_batches(doc.blocks,sources)
        cached=self.store.annotated_sections(doc_id)
        finished=lambda:sum(key in cached for key,_ in groups)
        def section_annotated(key):
            cached.add(key)
            self.store.update_job(doc_id,'Annotate','running',.78+.18*finished()/max(1,len(groups)),annotation_count=finished())
        self.store.update_job(doc_id,'Annotate','running',.78+.18*finished()/max(1,len(groups)),completed_stages=done,annotation_total=len(groups),annotation_count=finished())
        units=[tracked(self.annotate_unit(doc_id,key,batch,options),partial(section_annotated,key)) for key,batch in groups if key not in cached]
        if not await self.drive(doc_id,units,options) or self.paused(doc_id):return
        done=list(dict.fromkeys(done+['Annotate']))
        self.store.update_job(doc_id,'Validate','running',.97)
        doc=self.store.document(doc_id)
        by_id={b.id:b for b in doc.blocks}
        firsts=first_occurrences([b for b in doc.blocks if b.id in sources and not sources[b.id]['references']],doc.glossary)
        for b in doc.blocks:
            if b.note:
                b.qa_flags=sorted(set([f for f in b.qa_flags if f=='ANNOTATE_REVIEW']+validate_note(b,by_id,self.store.note_anchor(doc_id,b.id),firsts)))
                continue
            source=sources[b.id]
            b.qa_flags=sorted(set([f for f in b.qa_flags if f in {'EXTRACT_REVIEW','ANNOTATE_REVIEW'}]+validate_block(b,source['block'],doc.glossary,source['references'],options.min_ratio)))
        decorate_tables(doc.blocks)
        self.store.save_blocks(doc_id,doc.blocks)
        flags=sum(bool(b.qa_flags) for b in doc.blocks)
        done=list(dict.fromkeys(done+['Translate','Validate','Render']))
        self.store.update_job(doc_id,'Render','review' if flags else 'complete',1,completed_stages=done,review_count=flags,error=None)
        self.store.clear_ai_results(doc_id)

    def annotation_context(self,doc,batch,sources,extra_refs=()):
        import re
        eligible=[b for b in doc.blocks if b.id in sources and not sources[b.id]['references']]
        firsts=first_occurrences(eligible,doc.glossary)
        target_ids={b.id for b in batch}
        text=' '.join((b.en or '')+' '+(b.caption_en or '') for b in batch)
        evidence=[b for b in eligible if b.id not in target_ids and (b.id in extra_refs or (b.n and re.search(r'(?<!\w)'+re.escape(b.n)+r'(?!\w)',text)))][:8]
        # Only terms first used here matter to annotation (term notes); the
        # translation stage already enforces the rest of the glossary.
        local={term:bid for term,bid in firsts.items() if bid in target_ids}
        texts=[t for b in batch for t in (b.en,b.caption_en,compact(b).get('table'))]
        targets=[];section=None
        for b in batch:
            item=compact(b)
            if b.section_path!=section:section=b.section_path;item['section']=' > '.join(section)
            targets.append(item)
        return {'title':doc.title,'glossary':[g for g in glossary_for(doc.glossary,texts,local) if g['term'] in local],'first_occurrences':local,
                'target_blocks':targets,'evidence_blocks':[compact(b,700) for b in evidence]},firsts,{b.id:b for b in eligible}

    def annotate_unit(self,doc_id,key,batch,options):
        doc=self.store.document(doc_id);sources=self.store.sources(doc_id)
        payload,firsts,by_id=self.annotation_context(doc,batch,sources)
        notes=[];malformed=False;targets={b.id for b in batch}
        for attempt in range(2):
            value=yield AIRequest(f'annotate:{key}:{attempt}','annotate',options.annotate_model,payload)
            if isinstance(value,ProviderError):
                if str(value)!='AI_INVALID_JSON':raise value
                malformed=True;break  # The transport already asked twice.
            try:
                data=AnnotationBatch.model_validate(value)
                notes=make_notes(doc_id,data.notes,targets,by_id,firsts)
                malformed=False
                break
            except (ValueError,KeyError,TypeError):
                malformed=True
                payload={**payload,'validation_errors':{'section':['Invalid note schema or anchor; return the required JSON structure.']}}
        failing=[(anchor,b) for anchor,b in notes if b.qa_flags]
        if failing and not malformed:
            # Repair only the flagged notes, with just their anchors and evidence.
            anchors={anchor for anchor,_ in failing}
            refs={r for _,b in failing for r in b.note.refs}-anchors
            repair={'title':payload['title'],'glossary':payload['glossary'],
                    'first_occurrences':{t:i for t,i in payload['first_occurrences'].items() if i in anchors},
                    'target_blocks':[t for t in payload['target_blocks'] if t['id'] in anchors],
                    'evidence_blocks':[compact(by_id[r],700) for r in sorted(refs) if r in by_id],
                    'repair_notes':[{'after_block_id':anchor,**b.note.model_dump(exclude={'origin'}),'validation_errors':b.qa_flags} for anchor,b in failing]}
            value=yield AIRequest(f'annotate:{key}:repair','annotate',options.annotate_model,repair)
            if isinstance(value,ProviderError):
                if str(value)!='AI_INVALID_JSON':raise value
            else:
                try:
                    data=AnnotationBatch.model_validate(value)
                    fixed={(anchor,b.note.kind):(anchor,b) for anchor,b in make_notes(doc_id,data.notes,anchors,by_id,firsts)}
                    notes=[fixed.get((anchor,b.note.kind),(anchor,b)) if b.qa_flags else (anchor,b) for anchor,b in notes]
                except (ValueError,KeyError,TypeError):
                    pass  # Keep the flagged originals for manual review.
        for b in batch:b.qa_flags=[f for f in b.qa_flags if f!='ANNOTATE_REVIEW']
        if malformed:batch[0].qa_flags=sorted(set(batch[0].qa_flags+['ANNOTATE_REVIEW']))
        self.store.save_annotations(doc_id,key,notes,batch)

    @staticmethod
    def immediate(options):
        return options.model_copy(update={'batch':False})

    async def annotate_section(self,doc_id,key,batch,options):
        await self.drive(doc_id,[self.annotate_unit(doc_id,key,batch,options)],self.immediate(options),cache=False,pausable=False)

    async def regenerate_note(self,doc,block,options,sources):
        anchor=self.store.note_anchor(doc.id,block.id)
        if block.note.kind=='trm':
            firsts=first_occurrences([b for b in doc.blocks if b.id in sources and not sources[b.id]['references']],doc.glossary)
            anchor=next((bid for term,bid in firsts.items() if term.casefold()==block.note.title.strip().casefold()),anchor)
        original=next((b for b in doc.blocks if b.id==anchor),None)
        if not original:raise ValueError('Unknown note anchor')
        batch=section_window(doc.blocks,sources,anchor)
        payload,firsts,by_id=self.annotation_context(doc,batch,sources,block.note.refs)
        payload['repair_note']={'after_block_id':anchor,**block.note.model_dump(exclude={'origin'})}
        payload['validation_errors']=block.qa_flags
        for _ in range(2):
            try:
                data=AnnotationBatch.model_validate(await self.call(doc.id,'annotate',options.annotate_model,payload))
                notes=make_notes(doc.id,data.notes,{anchor},by_id,firsts)
                if len(notes)!=1 or notes[0][1].note.kind!=block.note.kind:raise ValueError('Repair must preserve identity')
                repaired=notes[0][1];repaired.id=block.id;repaired.order=block.order
                block=repaired
                if not block.qa_flags:break
                payload['validation_errors']=block.qa_flags
                payload['repair_note']={'after_block_id':anchor,**block.note.model_dump(exclude={'origin'})}
            except (ValueError,KeyError,TypeError):
                block.qa_flags=sorted(set(block.qa_flags+['ANNOTATE_REVIEW']))
            except ProviderError as error:
                if str(error)!='AI_INVALID_JSON':raise
                block.qa_flags=sorted(set(block.qa_flags+['ANNOTATE_REVIEW']));break
        # Term repairs may relocate to the first occurrence; the note ID remains
        # stable so bookmarks (and later Ask AI threads) survive.
        self.store.save_repaired_note(doc.id,block,anchor)
        return block

    def restore_unit(self,doc_id,block,source,options):
        for attempt in range(2):
            value=yield AIRequest(f'restore:{block.id}:{attempt}','table' if block.type=='tab' else 'equation',options.restore_model,
                {'source_text':source.en or '', 'caption':source.caption_en or ''},self.store.data_dir/block.image_path)
            try:
                if isinstance(value,ProviderError):raise ValueError(str(value))
                if block.type=='tab':
                    recovered=parse_table(value['html']);table_grid(recovered)
                    if trusted_table(source) and numeric_locations(recovered)!=numeric_locations(source.table):
                        raise ValueError('Numeric cells changed')
                    recovered_text=' '.join(c.text_en for row in recovered.header+recovered.body for c in row)
                    if number_tokens(recovered_text)!=number_tokens(source.en or ''):
                        raise ValueError('Source numeric tokens changed')
                    block.table=recovered
                else:
                    latex=value.get('latex')
                    if not isinstance(latex,str) or not 0<len(latex)<20000:
                        raise ValueError('Invalid equation')
                    block.latex=latex
                block.qa_flags=[f for f in block.qa_flags if f!='EXTRACT_REVIEW']
                return
            except (ValueError,KeyError,TypeError):
                block.qa_flags=list(set(block.qa_flags+['EXTRACT_REVIEW']))

    async def restore_block(self,doc_id,block,source,options):
        await self.drive(doc_id,[self.restore_unit(doc_id,block,source,options)],self.immediate(options),cache=False,pausable=False)

    def glossary_unit(self,doc_id,doc,sources,options):
        eligible=[b for b in doc.blocks if b.id in sources and not sources[b.id]['references']]
        texts=[t for b in eligible for t in (b.en,b.caption_en)]
        excerpt=[];length=0
        for b in eligible:
            if b.type not in {'p','li'} or not b.en:continue
            if length>6000:break
            excerpt.append(plain(b.en));length+=len(excerpt[-1])
        value=yield AIRequest('glossary','glossary',options.glossary_model,
            {'title':doc.title,'excerpt':excerpt,'candidates':glossary_candidates(texts)})
        if isinstance(value,ProviderError):raise value
        glossary=parse_glossary(value)
        with self.store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO glossary VALUES(?,?,?,?,?)',[(doc_id,g.term,g.ko,g.keep_english,g.definition_ko) for g in glossary])

    @staticmethod
    def translation_payload(blocks,glossary):
        texts=[t for b in blocks for t in (b.en,b.caption_en,*(c.text_en for row in (b.table.header if b.table else []) for c in row))]
        payload={'glossary':glossary_for(glossary,texts),'blocks':[]};section=None
        for b in blocks:
            item={k:v for k,v in {'id':b.id,'type':None if b.type=='p' else b.type,'en':b.en,'caption_en':b.caption_en}.items() if v}
            if b.section_path!=section:section=b.section_path;item['section']=' > '.join(section)
            if b.table:
                item['headers']=[{'row':ri,'col':ci,'text_en':c.text_en} for ri,row in enumerate(b.table.header) for ci,c in enumerate(row) if not c.numeric]
            payload['blocks'].append(item)
        return payload

    def translate_unit(self,doc_id,blocks,glossary,sources,options,tag):
        pending=blocks
        for attempt in range(2):
            value=yield AIRequest(f'{tag}:{attempt}','translate',options.translate_model,self.translation_payload(pending,glossary))
            if isinstance(value,ProviderError):
                # A batch whose Korean overflows the output cap (or keeps coming
                # back malformed) would fail identically on every resume. Halve it.
                if len(pending)<2:raise value
                half=len(pending)//2
                yield from self.translate_unit(doc_id,pending[:half],glossary,sources,options,tag+'a')
                yield from self.translate_unit(doc_id,pending[half:],glossary,sources,options,tag+'b')
                return
            try:
                data=TranslationBatch.model_validate(value)
                values={}
                for v in data.blocks:values.setdefault(v.id,v)
                for b in pending:
                    answer=values.get(b.id)
                    if answer is None:continue  # Stays pending; retried below.
                    if answer.ko is not None: b.ko=inline(answer.ko)
                    if answer.caption_ko is not None: b.caption_ko=inline(answer.caption_ko)
                    for h in answer.headers:
                        if b.table and h.row<len(b.table.header) and h.col<len(b.table.header[h.row]) and not b.table.header[h.row][h.col].numeric:
                            b.table.header[h.row][h.col].text_ko=inline(h.text_ko)
            except ValueError:
                pass
            pending=[b for b in pending if validate_block(b,sources[b.id]['block'],glossary,False,options.min_ratio)]
            if not pending: break

    async def translate(self,doc_id,blocks,glossary,sources,options):
        await self.drive(doc_id,[self.translate_unit(doc_id,blocks,glossary,sources,options,'translate:'+blocks[0].id)],self.immediate(options),cache=False,pausable=False)

    async def regenerate(self,doc_id,block_id):
        async with self.work_lock:
            job=self.store.job(doc_id)
            if not job or job['status'] in BUSY:
                raise ValueError('Job is busy')
            doc=self.store.document(doc_id)
            block=next((b for b in doc.blocks if b.id==block_id),None)
            sources=self.store.sources(doc_id)
            if not block or (block_id in sources and sources[block_id]['references']):
                raise ValueError('Block is not translatable')
            options=PipelineOptions.model_validate(job['checkpoint']['options'])
            if block.note:
                block=await self.regenerate_note(doc,block,options,sources)
                self.refresh_review(doc_id)
                return block
            if 'ANNOTATE_REVIEW' in block.qa_flags:
                key,batch=next((key,batch) for key,batch in section_batches(doc.blocks,sources) if any(b.id==block.id for b in batch))
                await self.annotate_section(doc_id,key,batch,options)
                self.refresh_review(doc_id)
                return next(b for b in self.store.document(doc_id).blocks if b.id==block_id)
            if block.type in {'eq','tab'} and block.image_path:
                await self.restore_block(doc_id,block,sources[block_id]['block'],options)
            if block.type!='eq':
                await self.translate(doc_id,[block],doc.glossary,sources,options)
            block.qa_flags=sorted(set([f for f in block.qa_flags if f=='EXTRACT_REVIEW']+validate_block(block,sources[block_id]['block'],doc.glossary,False,options.min_ratio)))
            self.store.save_blocks(doc_id,[block])
            updated=self.store.document(doc_id)
            decorate_tables(updated.blocks)
            self.store.save_blocks(doc_id,[b for b in updated.blocks if b.table])
            self.refresh_review(doc_id)
            return block

    def refresh_review(self,doc_id):
        job=self.store.job(doc_id)
        if job['status'] in {'review','complete'}:
            count=sum(bool(b.qa_flags) for b in self.store.document(doc_id).blocks)
            self.store.update_job(doc_id,'Render','review' if count else 'complete',1,review_count=count)
