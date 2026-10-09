import asyncio
import uuid
from typing import Literal
from urllib.parse import quote
from fastapi import HTTPException
from starlette.responses import Response
from pydantic import Field
from .models import Model
from .exports import html_export,markdown_export,presentation_export,block_markdown
from .arxiv import ArxivImporter
from .storage import copy_storage

class ArxivInput(Model):value:str=Field(min_length=1,max_length=300)
class NoteInput(Model):
    title:str=Field(default='발표 메모',min_length=1,max_length=300)
    body_md:str=Field(default='',max_length=30000)
    block_id:str|None=None
    message_id:str|None=None
class NoteOrder(Model):ids:list[str]=Field(max_length=1000)
class StorageInput(Model):path:str=Field(min_length=3,max_length=1000)

def register(app,store,pipeline,ask,registry):
    importer=ArxivImporter(store,pipeline)
    app.state.arxiv=importer;app.state.maintenance=False
    storage_lock=asyncio.Lock()
    def document(doc_id):
        doc=store.document(doc_id)
        if not doc:raise HTTPException(404,'DOCUMENT_NOT_FOUND')
        return doc
    def busy():
        return bool(ask.active or pipeline.work_lock.locked() or importer.lock.locked() or any(not t.done() for t in [*pipeline.tasks.values(),*pipeline.summary_tasks.values()]))

    @app.post('/documents/arxiv',status_code=202)
    async def import_arxiv(value:ArxivInput):
        try:return await importer.ingest(value.value)
        except ValueError as e:raise HTTPException(422,str(e) if str(e).startswith('ARXIV_') else 'ARXIV_DOWNLOAD_FAILED') from None

    @app.get('/documents/{doc_id}/export')
    def export(doc_id:str,format:Literal['html','md','presentation']='html'):
        doc=document(doc_id)
        if format=='html':
            try:body=html_export(store,doc,registry.redact)
            except FileNotFoundError:raise HTTPException(503,'EXPORT_NOT_BUILT') from None
        else:body=registry.redact(presentation_export(store,doc) if format=='presentation' else markdown_export(store,doc))
        filename=doc_id+('-presentation' if format=='presentation' else '')+('.html' if format=='html' else '.md')
        return Response(body,media_type='text/html' if format=='html' else 'text/markdown',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'X-Content-Type-Options':'nosniff'})

    @app.get('/documents/{doc_id}/presentation')
    def notes(doc_id:str):
        document(doc_id)
        with store.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM presentation_notes WHERE doc_id=? ORDER BY sort_order,id',(doc_id,))]

    @app.post('/documents/{doc_id}/presentation')
    def add_note(doc_id:str,value:NoteInput):
        doc=document(doc_id);block=None
        if value.message_id:
            with store.connect() as db:
                row=db.execute("SELECT m.content_md,t.block_id FROM messages m JOIN threads t ON t.id=m.thread_id WHERE m.id=? AND t.doc_id=? AND m.role='assistant' AND m.status='complete'",(value.message_id,doc_id)).fetchone()
                existing=db.execute('SELECT * FROM presentation_notes WHERE doc_id=? AND message_id=?',(doc_id,value.message_id)).fetchone()
                if existing:return dict(existing)
            if not row:raise HTTPException(422,'ANSWER_NOT_COMPLETE')
            value.body_md=row[0];value.block_id=row[1]
        if value.block_id:
            block=next((b for b in doc.blocks if b.id==value.block_id),None)
            if not block:raise HTTPException(422,'BLOCK_NOT_FOUND')
            if not value.body_md:value.body_md=block_markdown(block)
        if not value.body_md.strip():raise HTTPException(422,'NOTE_EMPTY')
        id=uuid.uuid4().hex
        with store.connect() as db:
            order=db.execute('SELECT COALESCE(MAX(sort_order),-1)+1 FROM presentation_notes WHERE doc_id=?',(doc_id,)).fetchone()[0]
            db.execute('INSERT INTO presentation_notes(id,doc_id,block_id,message_id,title,body_md,sort_order) VALUES(?,?,?,?,?,?,?)',(id,doc_id,value.block_id,value.message_id,registry.redact(value.title),registry.redact(value.body_md),order))
            return dict(db.execute('SELECT * FROM presentation_notes WHERE id=?',(id,)).fetchone())

    @app.put('/documents/{doc_id}/presentation/order',status_code=204)
    def order_notes(doc_id:str,value:NoteOrder):
        with store.connect() as db:
            ids={r[0] for r in db.execute('SELECT id FROM presentation_notes WHERE doc_id=?',(doc_id,))}
            if len(value.ids)!=len(ids) or set(value.ids)!=ids:raise HTTPException(422,'NOTE_ORDER_INVALID')
            db.executemany('UPDATE presentation_notes SET sort_order=? WHERE id=? AND doc_id=?',[(i,id,doc_id) for i,id in enumerate(value.ids)])

    @app.put('/documents/{doc_id}/presentation/{note_id}',status_code=204)
    def edit_note(doc_id:str,note_id:str,value:NoteInput):
        if not value.body_md.strip():raise HTTPException(422,'NOTE_EMPTY')
        with store.connect() as db:
            if not db.execute('SELECT 1 FROM presentation_notes WHERE doc_id=? AND id=?',(doc_id,note_id)).fetchone():raise HTTPException(404,'NOTE_NOT_FOUND')
            db.execute('UPDATE presentation_notes SET title=?,body_md=?,updated_at=CURRENT_TIMESTAMP WHERE doc_id=? AND id=?',(registry.redact(value.title),registry.redact(value.body_md),doc_id,note_id))

    @app.delete('/documents/{doc_id}/presentation/{note_id}',status_code=204)
    def delete_note(doc_id:str,note_id:str):
        with store.connect() as db:db.execute('DELETE FROM presentation_notes WHERE doc_id=? AND id=?',(doc_id,note_id))

    @app.get('/settings/storage')
    def storage():return {'path':str(store.data_dir),'busy':busy(),'maintenance':app.state.maintenance}

    @app.post('/settings/storage/migrate')
    async def migrate(value:StorageInput):
        if storage_lock.locked() or busy() or app.state.active_writes>1:raise HTTPException(409,'STORAGE_BUSY')
        async with storage_lock:
            app.state.maintenance=True
            try:return await asyncio.to_thread(copy_storage,store,value.path)
            except ValueError as e:app.state.maintenance=False;raise HTTPException(422,str(e)) from None
            except Exception:app.state.maintenance=False;raise HTTPException(500,'STORAGE_COPY_FAILED') from None

    @app.post('/settings/storage/cancel',status_code=204)
    def cancel_storage():
        if storage_lock.locked():raise HTTPException(409,'STORAGE_BUSY')
        app.state.maintenance=False
