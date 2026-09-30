import secrets
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response
from starlette.responses import StreamingResponse
from pydantic import Field, SecretStr

from .models import Model, Note, PipelineOptions, ReaderSettings, ReadingPosition
from .store import Store
from .pipeline import Pipeline
from .provider import AnthropicProvider, ProviderError, WindowsVault
from .adapter import inline
from .validation import validate_block
from .annotations import first_occurrences, validate_note
from .providers import ProviderRegistry
from .ask import AskService, AskInput, AskSettings


class ProviderInput(Model):
    api_key: SecretStr | None = Field(default=None,min_length=16,max_length=2000)
    provider: Literal['anthropic','openai','google']='anthropic'
    mode: Literal['api_key','cli']='api_key'
    cli_path: str | None = Field(default=None,max_length=1000)

class NoteInput(Model):
    message_id: str = Field(max_length=80)
    kind: Literal['key','res','lim','ins','mth','trm']

class OnboardingInput(Model):
    completed: bool=True


class BlockEdit(Model):
    ko: str | None = Field(default=None,max_length=100000)
    caption_ko: str | None = Field(default=None,max_length=20000)
    note: Note | None = None


def create_app(token: str, data_dir: Path, fixture: Path,
               origin: str = "http://tauri.localhost", *, vault=None, provider=None, parser=None) -> FastAPI:
    if len(token) < 32:
        raise ValueError("A strong session token is required")
    store = Store(data_dir, fixture)
    vault = vault or WindowsVault()
    registry=ProviderRegistry(store,vault)
    override=provider
    provider = provider or registry
    pipeline = Pipeline(store, provider, parser)
    ask=AskService(store,registry,override if hasattr(override,'stream') else None)

    @asynccontextmanager
    async def lifespan(_app):
        await pipeline.recover()
        yield
        await pipeline.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.pipeline, app.state.ask = store, pipeline, ask
    app.state.active_writes=0

    @app.exception_handler(ProviderError)
    async def provider_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        # FastAPI's default validation body echoes offending input. Never reflect
        # arbitrary submitted values (including mistakenly supplied secrets).
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.middleware("http")
    async def secure_local_api(request: Request, call_next):
        request_origin = request.headers.get("origin")
        if request_origin is not None and request_origin != origin:
            return JSONResponse({"detail": "Origin forbidden"}, status_code=403)
        # Browser CORS preflight cannot carry a bearer token. This branch returns
        # no application data; every actual request (including /health) is protected.
        if request.method == "OPTIONS":
            if request_origin != origin:
                return JSONResponse({"detail": "Origin required"}, status_code=403)
            method = request.headers.get("access-control-request-method", "")
            requested = {h.strip().lower() for h in request.headers.get("access-control-request-headers", "").split(",") if h.strip()}
            if method not in {"GET", "PUT", "PATCH", "POST", "DELETE"} or not requested <= {"authorization", "content-type"}:
                return JSONResponse({"detail": "Preflight forbidden"}, status_code=403)
            response = Response(status_code=204, headers={
                "Access-Control-Allow-Methods": "GET, PUT, PATCH, POST, DELETE",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "600",
            })
        else:
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            writing=request.method not in {'GET','HEAD'}
            if getattr(app.state,'maintenance',False) and writing and request.url.path!='/settings/storage/cancel':
                return JSONResponse({'detail':'STORAGE_MAINTENANCE'},status_code=409,headers={'Access-Control-Allow-Origin':origin,'Cache-Control':'no-store'})
            if writing:app.state.active_writes+=1
            try:response = await call_next(request)
            finally:
                if writing:app.state.active_writes-=1
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request_origin == origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "milestone": "M4"}

    @app.get("/documents")
    def library():
        return store.library()

    @app.post("/documents", status_code=202)
    async def upload(request: Request):
        if request.headers.get('content-type','').split(';')[0] != 'application/pdf':
            raise HTTPException(415,'PDF_REQUIRED')
        try:
            return await pipeline.ingest(request)
        except ValueError as error:
            known={'INVALID_PDF','UNSUPPORTED_PDF','PDF_TOO_LARGE'}
            code=str(error) if str(error) in known else 'INVALID_PDF'
            raise HTTPException(413 if code=='PDF_TOO_LARGE' else 422,code) from None
        except Exception:
            raise HTTPException(422,'INVALID_PDF') from None

    @app.get('/documents/{doc_id}/progress')
    async def progress(doc_id: str, request: Request):
        if not store.job(doc_id):
            raise HTTPException(404,'Job not found')
        async def events():
            previous=''
            while not await request.is_disconnected():
                job=store.job(doc_id)
                value=json.dumps(job,ensure_ascii=False)
                if value!=previous:
                    yield f'event: progress\ndata: {value}\n\n'
                    previous=value
                else:
                    yield ': keepalive\n\n'
                if job['status'] not in {'queued','running'}:
                    break
                await asyncio.sleep(.4)
        return StreamingResponse(events(),media_type='text/event-stream')

    @app.get('/documents/{doc_id}/job')
    def job(doc_id: str):
        result=store.job(doc_id)
        if not result: raise HTTPException(404,'Job not found')
        return result

    @app.get('/documents/{doc_id}/estimate')
    def estimate(doc_id: str):
        result=pipeline.estimate(doc_id)
        if result is None: raise HTTPException(404,'Document not found')
        return result

    @app.post('/documents/{doc_id}/resume', status_code=202)
    async def resume(doc_id: str, options: PipelineOptions):
        try:
            pipeline.approve(doc_id,options)
        except ValueError:
            raise HTTPException(409,'JOB_NOT_RESUMABLE') from None
        return {'status':'queued'}

    @app.post('/documents/{doc_id}/pause', status_code=204)
    def pause(doc_id: str):
        job=store.job(doc_id)
        if not job: raise HTTPException(404,'Job not found')
        if job['status'] in {'running','queued','awaiting_ai','ready_to_translate'}:
            store.update_job(doc_id,job['stage'],'paused',job['progress'])

    @app.post('/documents/{doc_id}/blocks/{block_id}/regenerate')
    async def regenerate(doc_id: str,block_id: str):
        try:
            return await pipeline.regenerate(doc_id,block_id)
        except ValueError:
            raise HTTPException(409,'BLOCK_NOT_REGENERATABLE') from None

    @app.patch('/documents/{doc_id}/blocks/{block_id}')
    def edit_block(doc_id: str,block_id: str,value: BlockEdit):
        job=store.job(doc_id)
        if not job or job['status'] in {'running','queued'}:
            raise HTTPException(409,'JOB_BUSY')
        doc=store.document(doc_id)
        block=next((b for b in doc.blocks if b.id==block_id),None)
        sources=store.sources(doc_id)
        if not block or (block_id in sources and sources[block_id]['references']):
            raise HTTPException(404,'Block not found')
        if block.note:
            if not value.note or value.note.kind!=block.note.kind:
                raise HTTPException(422,'INVALID_NOTE_EDIT')
            block.note=value.note.model_copy(update={'origin':'user'})
            by_id={b.id:b for b in doc.blocks}
            firsts=first_occurrences([b for b in doc.blocks if b.id in sources and not sources[b.id]['references']],doc.glossary)
            block.qa_flags=validate_note(block,by_id,store.note_anchor(doc_id,block.id),firsts)
            store.save_blocks(doc_id,[block]);pipeline.refresh_review(doc_id)
            return block
        if value.note is not None:raise HTTPException(422,'INVALID_NOTE_EDIT')
        if value.ko is not None: block.ko=inline(value.ko)
        if value.caption_ko is not None: block.caption_ko=inline(value.caption_ko)
        block.qa_flags=sorted(set([f for f in block.qa_flags if f in {'EXTRACT_REVIEW','ANNOTATE_REVIEW'}]+validate_block(block,sources[block_id]['block'],doc.glossary,False,job['checkpoint']['options']['min_ratio'])))
        store.save_blocks(doc_id,[block])
        pipeline.refresh_review(doc_id)
        return block

    @app.get('/documents/{doc_id}/blocks/{block_id}/image')
    def image(doc_id: str,block_id: str):
        doc=store.document(doc_id)
        block=next((b for b in doc.blocks if b.id==block_id),None) if doc else None
        if not block or not block.image_path: raise HTTPException(404,'Image not found')
        path=store.asset_path(doc_id,block.image_path)
        if path is None or path.suffix!='.png':
            raise HTTPException(404,'Image not found')
        # Authenticated JSON avoids tokens in image URLs, persistent storage, or
        # CSP relaxation. The renderer displays only a local PNG data URL.
        return {'data_url':'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode('ascii')}

    @app.get('/settings/providers')
    def provider_settings(provider: Literal['anthropic','openai','google']='anthropic',mode: Literal['api_key','cli']='api_key'):
        from .cli_provider import cli_enabled
        config=store.provider_config(provider)
        common={'provider':provider,'mode':mode,'options':store.pipeline_options(),'cli_enabled':cli_enabled(),'cli_path':config['cli_path']}
        try:
            connected=(override if override and provider=='anthropic' and mode=='api_key' else registry.adapter(provider,mode)).connected()
        except ProviderError:
            return {**common,'configured':False,'keyring_available':False}
        return {**common,'configured':connected,'keyring_available':True}

    @app.put('/settings/providers',status_code=204)
    def save_provider(value: ProviderInput):
        if value.mode=='cli' and value.provider=='google':raise HTTPException(422,'CLI_UNSUPPORTED')
        if value.api_key:
            key=value.api_key.get_secret_value().strip()
            if not key.isascii() or any(c.isspace() for c in key): raise HTTPException(422,'INVALID_KEY_FORMAT')
            registry.vaults[value.provider].set(key)
        if value.cli_path and not Path(value.cli_path).is_absolute(): raise HTTPException(422,'CLI_NOT_FOUND')
        with store.connect() as db:
            db.execute('INSERT INTO provider_settings(provider,mode,cli_path,enabled) VALUES(?,?,?,1) ON CONFLICT(provider) DO UPDATE SET mode=excluded.mode,cli_path=excluded.cli_path,enabled=1',
                (value.provider,value.mode,value.cli_path))

    @app.delete('/settings/providers',status_code=204)
    def remove_provider(provider: Literal['anthropic','openai','google']='anthropic'):
        registry.vaults[provider].delete()

    @app.get('/providers/health')
    async def provider_health(provider: Literal['anthropic','openai','google']='anthropic',mode: Literal['api_key','cli']='api_key'):
        return await (override if override and provider=='anthropic' and mode=='api_key' else registry.adapter(provider,mode)).health_check()

    @app.put('/settings/pipeline',status_code=204)
    def pipeline_settings(value: PipelineOptions):
        if value.provider=='google' and value.mode=='cli': raise HTTPException(422,'CLI_UNSUPPORTED')
        store.save_pipeline_options(value)

    @app.get('/settings/onboarding')
    def onboarding():
        return store.preference('onboarding',{'completed':False})

    @app.put('/settings/onboarding',status_code=204)
    def save_onboarding(value: OnboardingInput):
        store.save_preference('onboarding',value.model_dump())

    @app.get('/settings/ask')
    def ask_settings():return ask.settings()

    @app.put('/settings/ask',status_code=204)
    def save_ask_settings(value: AskSettings):
        if len({p.id for p in value.presets})!=len(value.presets):raise HTTPException(422,'INVALID_PRESETS')
        store.save_preference('ask',json.loads(registry.redact(value.model_dump_json())))

    def prepare_ask(value):
        try:return ask.prepare(value)
        except ValueError as error:raise HTTPException(422,str(error)) from None

    @app.post('/ask/context')
    def preview_context(value: AskInput):
        snapshot,image,estimate=prepare_ask(value)
        return {'snapshot':snapshot,'estimated_tokens':estimate,'image_attached':bool(image)}

    @app.post('/ask')
    async def ask_question(value: AskInput):
        if value.thread_id in ask.active:raise HTTPException(409,'THREAD_BUSY')
        snapshot,image,_estimate=prepare_ask(value)
        async def events():
            async for event in ask.stream(value,snapshot,image):
                yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
        return StreamingResponse(events(),media_type='text/event-stream')

    @app.get('/threads')
    def threads(doc_id: str):return ask.threads(doc_id)

    @app.get('/threads/{tid}')
    def thread(tid: str):
        try:return ask.thread(tid)
        except ValueError:raise HTTPException(404,'THREAD_NOT_FOUND') from None

    @app.post('/threads/{tid}/save-as-note')
    def save_answer(tid: str,value: NoteInput):
        try:
            thread=ask.thread(tid);job=store.job(thread['doc_id'])
            if job and job['status'] in {'running','queued'}:raise HTTPException(409,'JOB_BUSY')
            block=ask.save_note(tid,value.message_id,value.kind)
            if job:pipeline.refresh_review(thread['doc_id'])
            return block
        except ValueError as error:raise HTTPException(422,str(error)) from None

    @app.get('/documents/{doc_id}/pages/{page_number}/image')
    async def pdf_page(doc_id: str,page_number: int):
        with store.connect() as db:
            row=db.execute('SELECT source_path,page_count FROM documents WHERE id=?',(doc_id,)).fetchone()
        if not row or not row['source_path'] or not 1<=page_number<=row['page_count']:raise HTTPException(404,'PAGE_NOT_FOUND')
        path=store.asset_path(doc_id,row['source_path'])
        if path is None or path.suffix!='.pdf':raise HTTPException(404,'PAGE_NOT_FOUND')
        def render():
            import pymupdf as fitz
            from .pdf_parser import PDF_LOCK
            with PDF_LOCK,fitz.open(path) as pdf:
                page=pdf[page_number-1];scale=min(2,1800/max(page.rect.width,page.rect.height))
                return page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')
        return {'data_url':'data:image/png;base64,'+base64.b64encode(await asyncio.to_thread(render)).decode('ascii')}

    @app.get("/documents/{doc_id}")
    def document(doc_id: str):
        doc = store.document(doc_id)
        if doc is None:
            raise HTTPException(404, "Document not found")
        return doc.model_dump(exclude_none=True)

    @app.patch("/documents/{doc_id}/position", status_code=204)
    def position(doc_id: str, value: ReadingPosition):
        try:
            store.position(doc_id, value)
        except ValueError:
            raise HTTPException(404, "Document or block not found") from None

    @app.get("/settings/reader")
    def settings():
        return store.settings()

    @app.put("/settings/reader", status_code=204)
    def save_settings(value: ReaderSettings):
        store.save_settings(value)

    from .m4 import register
    register(app,store,pipeline,ask,registry)
    return app
