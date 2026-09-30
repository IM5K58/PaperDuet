import asyncio
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from pydantic import Field

from .adapter import inline, parse_table, plain
from .models import Model, PipelineOptions
from .pdf_parser import PyMuPDFParser
from .provider import ProviderError
from .validation import number_tokens, numeric_locations, table_grid, validate_block
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


class Pipeline:
    def __init__(self, store, provider, parser=None):
        self.store,self.provider=store,provider
        self.parser=parser or PyMuPDFParser()
        self.tasks={}
        self.closing=False
        self.work_lock=asyncio.Lock()

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
            ids=[r[0] for r in db.execute("SELECT doc_id FROM jobs WHERE status IN ('queued','running')")]
        for doc_id in ids:
            self.start(doc_id)

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
        self.store.save_pipeline_options(options)
        self.store.update_job(doc_id,job['stage'],'queued',job['progress'],ai_approved=True,options=options.model_dump(),error=None)
        self.start(doc_id)

    async def call(self,doc_id,stage,model,payload,image=None):
        failure=None
        job=self.store.job(doc_id)
        options=PipelineOptions.model_validate(job['checkpoint']['options']) if job else self.store.pipeline_options()
        adapter=self.provider.adapter(options.provider,options.mode) if hasattr(self.provider,'adapter') else self.provider
        try:
            value,usage=await adapter.json(stage,model,payload,image)
        except ProviderError as error:
            if not error.usage:raise
            usage=error.usage;failure=error
        with self.store.connect() as db:
            db.execute('INSERT INTO ai_usage(doc_id,stage,model,tokens_in,tokens_out,cache_read,cache_write,requests,payload_chars) VALUES(?,?,?,?,?,?,?,?,?)',
                (doc_id,stage,model,usage.get('tokens_in',0),usage.get('tokens_out',0),usage.get('cache_read',0),usage.get('cache_write',0),
                 max(1,usage.get('requests',1)),len(json.dumps(payload,ensure_ascii=False))))
        if failure:raise failure
        return value

    async def run(self,doc_id):
        try:
            async with self.work_lock:
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
        adapter=self.provider.adapter(options.provider,options.mode) if hasattr(self.provider,'adapter') else self.provider
        if not adapter.connected():
            raise ProviderError('AI_CONNECTION_REQUIRED')
        doc=self.store.document(doc_id)
        sources=self.store.sources(doc_id)
        restored=set(checkpoint.get('restored',[]))
        candidates=[b for b in doc.blocks if b.type in {'tab','eq'} and b.image_path and not sources[b.id]['references']
                    and not (b.type=='tab' and sources[b.id]['block'].table)]
        for i,block in enumerate(candidates):
            if self.paused(doc_id): return
            if block.id in restored: continue
            self.store.update_job(doc_id,'Restore','running',.3+.15*i/max(1,len(candidates)))
            await self.restore_block(doc_id,block,sources[block.id]['block'],options)
            self.store.save_blocks(doc_id,[block])
            restored.add(block.id)
            self.store.update_job(doc_id,'Restore','running',.45,restored=sorted(restored))
        if 'Glossary' not in done:
            if self.paused(doc_id): return
            self.store.update_job(doc_id,'Glossary','running',.45)
            eligible=[b for b in doc.blocks if b.id in sources and not sources[b.id]['references']]
            texts=[t for b in eligible for t in (b.en,b.caption_en)]
            excerpt=[];length=0
            for b in eligible:
                if b.type not in {'p','li'} or not b.en:continue
                if length>6000:break
                excerpt.append(plain(b.en));length+=len(excerpt[-1])
            result=await self.call(doc_id,'glossary',options.glossary_model,
                {'title':doc.title,'excerpt':excerpt,'candidates':glossary_candidates(texts)})
            glossary=parse_glossary(result)
            with self.store.connect() as db:
                db.executemany('INSERT OR REPLACE INTO glossary VALUES(?,?,?,?,?)',[(doc_id,g.term,g.ko,g.keep_english,g.definition_ko) for g in glossary])
            done=list(dict.fromkeys(done+['Restore','Glossary']))
            self.store.update_job(doc_id,'Glossary','running',.5,completed_stages=done)
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
        for batch in batches:
            if self.paused(doc_id): return
            self.store.update_job(doc_id,'Translate','running',.5+.28*len(translated)/max(1,len(targets)))
            await self.translate(doc_id,batch,doc.glossary,sources,options)
            translated.update(b.id for b in batch)
            self.store.save_blocks(doc_id,batch)
            self.store.update_job(doc_id,'Translate','running',.5+.28*len(translated)/max(1,len(targets)),translated=sorted(translated),translated_count=len(translated),total_blocks=len(targets))
        done=list(dict.fromkeys(done+['Translate']))
        doc=self.store.document(doc_id)
        groups=section_batches(doc.blocks,sources)
        cached=self.store.annotated_sections(doc_id)
        for index,(key,batch) in enumerate(groups):
            if self.paused(doc_id):return
            if key in cached:continue
            self.store.update_job(doc_id,'Annotate','running',.78+.18*index/max(1,len(groups)),completed_stages=done,annotation_total=len(groups),annotation_count=len(cached))
            await self.annotate_section(doc_id,key,batch,options)
            cached.add(key)
            self.store.update_job(doc_id,'Annotate','running',.78+.18*(index+1)/max(1,len(groups)),annotation_count=len(cached))
        if self.paused(doc_id):return
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

    async def annotate_section(self,doc_id,key,batch,options):
        doc=self.store.document(doc_id);sources=self.store.sources(doc_id)
        payload,firsts,by_id=self.annotation_context(doc,batch,sources)
        notes=[];malformed=False;targets={b.id for b in batch}
        for attempt in range(2):
            try:
                data=AnnotationBatch.model_validate(await self.call(doc_id,'annotate',options.annotate_model,payload))
                notes=make_notes(doc_id,data.notes,targets,by_id,firsts)
                malformed=False
                break
            except (ValueError,KeyError,TypeError):
                malformed=True
                payload['validation_errors']={'section':['Invalid note schema or anchor; return the required JSON structure.']}
            except ProviderError as error:
                if str(error)!='AI_INVALID_JSON':raise
                malformed=True;break  # Transport already retried JSON once.
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
            try:
                data=AnnotationBatch.model_validate(await self.call(doc_id,'annotate',options.annotate_model,repair))
                fixed={(anchor,b.note.kind):(anchor,b) for anchor,b in make_notes(doc_id,data.notes,anchors,by_id,firsts)}
                notes=[fixed.get((anchor,b.note.kind),(anchor,b)) if b.qa_flags else (anchor,b) for anchor,b in notes]
            except (ValueError,KeyError,TypeError):
                pass  # Keep the flagged originals for manual review.
            except ProviderError as error:
                if str(error)!='AI_INVALID_JSON':raise
        for b in batch:b.qa_flags=[f for f in b.qa_flags if f!='ANNOTATE_REVIEW']
        if malformed:batch[0].qa_flags=sorted(set(batch[0].qa_flags+['ANNOTATE_REVIEW']))
        self.store.save_annotations(doc_id,key,notes,batch)

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

    async def restore_block(self,doc_id,block,source,options):
        for attempt in range(2):
            try:
                value=await self.call(doc_id,'table' if block.type=='tab' else 'equation',options.restore_model,
                    {'source_text':source.en or '', 'caption':source.caption_en or ''}, self.store.data_dir/block.image_path)
                if block.type=='tab':
                    recovered=parse_table(value['html']);table_grid(recovered)
                    if source.table and numeric_locations(recovered)!=numeric_locations(source.table):
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

    async def translate(self,doc_id,blocks,glossary,sources,options):
        pending=blocks
        for _ in range(2):
            texts=[t for b in pending for t in (b.en,b.caption_en,*(c.text_en for row in (b.table.header if b.table else []) for c in row))]
            payload={'glossary':glossary_for(glossary,texts),'blocks':[]};section=None
            for b in pending:
                item={k:v for k,v in {'id':b.id,'type':None if b.type=='p' else b.type,'en':b.en,'caption_en':b.caption_en}.items() if v}
                if b.section_path!=section:section=b.section_path;item['section']=' > '.join(section)
                if b.table:
                    item['headers']=[{'row':ri,'col':ci,'text_en':c.text_en} for ri,row in enumerate(b.table.header) for ci,c in enumerate(row) if not c.numeric]
                payload['blocks'].append(item)
            try:
                try:
                    data=TranslationBatch.model_validate(await self.call(doc_id,'translate',options.translate_model,payload))
                except ProviderError as error:
                    # A batch whose Korean overflows the output cap (or keeps coming
                    # back malformed) would fail identically on every resume. Halve it.
                    if str(error) not in {'AI_OUTPUT_LIMIT','AI_INVALID_JSON'} or len(pending)<2:raise
                    half=len(pending)//2
                    await self.translate(doc_id,pending[:half],glossary,sources,options)
                    await self.translate(doc_id,pending[half:],glossary,sources,options)
                    return
                values={}
                for v in data.blocks:values.setdefault(v.id,v)
                for b in pending:
                    value=values.get(b.id)
                    if value is None:continue  # Stays pending; retried below.
                    if value.ko is not None: b.ko=inline(value.ko)
                    if value.caption_ko is not None: b.caption_ko=inline(value.caption_ko)
                    for h in value.headers:
                        if b.table and h.row<len(b.table.header) and h.col<len(b.table.header[h.row]) and not b.table.header[h.row][h.col].numeric:
                            b.table.header[h.row][h.col].text_ko=inline(h.text_ko)
            except ValueError:
                pass
            pending=[b for b in pending if validate_block(b,sources[b.id]['block'],glossary,False,options.min_ratio)]
            if not pending: break

    async def regenerate(self,doc_id,block_id):
        async with self.work_lock:
            job=self.store.job(doc_id)
            if not job or job['status'] in {'queued','running'}:
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
