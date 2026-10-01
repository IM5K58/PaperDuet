"""Common streaming adapters for pipeline and Ask; never execute model tools."""
import asyncio
import base64
import json
import re
from pathlib import Path
from urllib.parse import quote

import httpx

from .provider import AnthropicProvider, ProviderError, WindowsVault


class Redactor:
    """Retain a secret-length tail so a key split across chunks cannot escape."""
    def __init__(self, secrets):
        self.secrets = [s for s in secrets if s]
        self.tail = ''
        self.keep = max(map(len, self.secrets), default=1) - 1

    def clean(self, value):
        for secret in self.secrets:
            value = value.replace(secret, '[REDACTED]')
        return value

    def push(self, value='', final=False):
        self.tail += value
        # Replace complete occurrences before choosing a safe output boundary.
        self.tail = self.clean(self.tail)
        cut = len(self.tail) if final else max(0, len(self.tail)-self.keep)
        result, self.tail = self.tail[:cut], self.tail[cut:]
        return result


def parts(content):
    """Message content is a string or a list of text parts; a part marked
    `cache` ends a stable prefix (Anthropic caches it explicitly, the other
    providers cache identical prefixes on their own)."""
    return content if isinstance(content, list) else [{'type': 'text', 'text': content}]


def flat(content):
    return '\n\n'.join(p['text'] for p in parts(content))


def image_content(image):
    raw = image.read_bytes()
    if len(raw) > 5_000_000:
        raise ProviderError('AI_IMAGE_TOO_LARGE')
    return base64.b64encode(raw).decode('ascii')


# Reasoning depth per pipeline stage. Mechanical stages stay shallow; only
# annotation benefits from deeper thinking. Ask AI keeps the model default.
STAGE_EFFORT = {'glossary': 'low', 'translate': 'low', 'table': 'low', 'equation': 'low', 'annotate': 'medium'}
ANTHROPIC_EFFORT = re.compile(r'claude-(opus-(4-[5-9]|5)|sonnet-(4-6|5)|fable|mythos)')
OPENAI_REASONING = re.compile(r'(gpt-5|gpt-6|o\d)')


def prompt_for(stage):
    return (Path(__file__).with_name('prompts') / f'{stage}.md').read_text(encoding='utf-8')


def decode_answer(answer):
    answer = answer.strip()
    if answer.startswith('```'):
        answer = answer.split('\n',1)[-1].rsplit('```',1)[0].strip()
    return json.loads(answer)


class StructuredAdapter:
    async def json(self, stage, model, payload, image=None):
        prompt = prompt_for(stage)
        total = {'tokens_in': 0, 'tokens_out': 0, 'cache_read': 0, 'cache_write': 0, 'requests': 0}
        for attempt in range(2):
            answer = ''
            total['requests'] += 1
            async for event in self.stream(model, prompt, [{'role':'user','content':json.dumps(payload,ensure_ascii=False)}], image, effort=STAGE_EFFORT.get(stage)):
                answer += event.get('text','')
                for key, value in event.get('usage',{}).items():
                    total[key] = total.get(key, 0) + value
            try:
                return decode_answer(answer), total
            except (ValueError,TypeError):
                if attempt:
                    raise ProviderError('AI_INVALID_JSON', usage=total) from None

    async def estimate_tokens(self, messages):
        return max(1, len(json.dumps(messages,ensure_ascii=False))//3)


class APIProvider(StructuredAdapter):
    mode = 'api_key'
    supports_images = True
    stream_granularity = 'token'
    BASES = {'anthropic':'https://api.anthropic.com','openai':'https://api.openai.com',
             'google':'https://generativelanguage.googleapis.com'}

    def __init__(self, provider, vault, transport=None):
        self.id,self.vault,self.transport = provider,vault,transport

    def connected(self):
        return bool(self.vault.get())

    def headers(self, key):
        if self.id == 'anthropic': return {'x-api-key':key,'anthropic-version':'2023-06-01'}
        if self.id == 'openai': return {'Authorization':f'Bearer {key}'}
        return {'x-goog-api-key':key}

    @staticmethod
    def check(response):
        if response.status_code in (401,403): raise ProviderError('AI_AUTH_FAILED')
        if response.status_code == 429: raise ProviderError('AI_RATE_LIMIT')
        if response.status_code == 404: raise ProviderError('AI_MODEL_NOT_FOUND')
        # 5xx and Anthropic's 529: the provider is busy or down, not refusing the request.
        if response.status_code >= 500: raise ProviderError('AI_OVERLOADED')
        if response.status_code >= 400: raise ProviderError('AI_REQUEST_FAILED')

    def client(self):
        return httpx.AsyncClient(base_url=self.BASES[self.id],transport=self.transport,
            timeout=httpx.Timeout(180,connect=15),trust_env=False,follow_redirects=False)

    async def list_models(self):
        if self.id == 'anthropic':
            return await AnthropicProvider(self.vault,self.transport).list_models()
        key=self.vault.get()
        if not key: raise ProviderError('AI_CONNECTION_REQUIRED')
        try:
            async with self.client() as client:
                result=[];path='/v1/models' if self.id=='openai' else '/v1beta/models?pageSize=1000'
                for _ in range(10):
                    response=await client.get(path,headers=self.headers(key));self.check(response)
                    data=json.loads(response.text.replace(key,'[REDACTED]'))
                    if self.id=='openai':
                        return sorted([{'id':m['id'],'name':m['id']} for m in data.get('data',[]) if m['id'].startswith(('gpt-','o1','o3','o4'))],key=lambda m:m['id'])
                    result.extend({'id':m['name'].removeprefix('models/'),'name':m.get('displayName',m['name'])}
                        for m in data.get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[]))
                    if not data.get('nextPageToken'): break
                    path='/v1beta/models?pageSize=1000&pageToken='+quote(data['nextPageToken'],safe='')
                return result
        except ProviderError: raise
        except Exception: raise ProviderError('AI_NETWORK_ERROR') from None

    async def health_check(self):
        return {'status':'ok','models':await self.list_models(),'supports_images':True,'stream_granularity':'token'}

    def body(self,model,system,messages,image,effort=None):
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}',model): raise ProviderError('AI_REQUEST_FAILED')
        data=image_content(image) if image else None
        if self.id=='anthropic':
            prepared=[{'role':m['role'],'content':[{'type':'text','text':p['text'],**({'cache_control':{'type':'ephemeral'}} if p.get('cache') else {})}
                for p in parts(m['content'])]} for m in messages]
            if data: prepared[-1]['content'].insert(0,{'type':'image','source':{'type':'base64','media_type':'image/png','data':data}})
            body={'model':model,'system':system,'messages':prepared,'max_tokens':16000,'stream':True}
            if effort and ANTHROPIC_EFFORT.match(model): body['output_config']={'effort':effort}
            return '/v1/messages',body
        if self.id=='openai':
            prepared=[{'role':m['role'],'content':[{'type':'output_text' if m['role']=='assistant' else 'input_text','text':p['text']} for p in parts(m['content'])]} for m in messages]
            if data: prepared[-1]['content'].append({'type':'input_image','image_url':'data:image/png;base64,'+data})
            body={'model':model,'instructions':system,'input':prepared,'max_output_tokens':16000,'stream':True,'store':False}
            if effort and OPENAI_REASONING.match(model): body['reasoning']={'effort':effort}
            return '/v1/responses',body
        prepared=[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':p['text']} for p in parts(m['content'])]} for m in messages]
        if data: prepared[-1]['parts'].append({'inlineData':{'mimeType':'image/png','data':data}})
        config={'maxOutputTokens':16000}
        if effort and re.match(r'gemini-[3-9]',model): config['thinkingConfig']={'thinkingLevel':effort}
        return f'/v1beta/models/{model}:streamGenerateContent?alt=sse',{'systemInstruction':{'parts':[{'text':system}]},'contents':prepared,'generationConfig':config}

    # Busy providers (Gemini "high demand", Anthropic "overloaded") usually recover in
    # seconds. Retry only before the first chunk, so an answer is never sent twice.
    RETRY_DELAYS=(2,5,10)

    async def stream(self, model, system, messages, image=None, effort=None):
        for delay in (*self.RETRY_DELAYS,None):
            started=False
            try:
                async for event in self.stream_once(model,system,messages,image,effort):
                    started=True
                    yield event
                return
            except ProviderError as error:
                if str(error)!='AI_OVERLOADED' or started or delay is None: raise
            await asyncio.sleep(delay)

    async def stream_once(self, model, system, messages, image=None, effort=None):
        key=self.vault.get()
        if not key: raise ProviderError('AI_CONNECTION_REQUIRED')
        path,body=self.body(model,system,messages,image,effort)
        redact=Redactor([key]);usage={'tokens_in':0,'tokens_out':0,'cache_read':0,'cache_write':0};completed=False
        try:
            async with self.client() as client:
                async with client.stream('POST',path,json=body,headers=self.headers(key)) as response:
                    self.check(response)
                    async for line in response.aiter_lines():
                        if not line.startswith('data:'): continue
                        value=line[5:].strip()
                        if value=='[DONE]': continue
                        data=json.loads(value);kind=data.get('type');delta=''
                        if kind=='error' or data.get('error'): raise ProviderError('AI_OVERLOADED' if 'overloaded' in json.dumps(data.get('error')).lower() else 'AI_REQUEST_FAILED',usage=usage)
                        if self.id=='anthropic':
                            if kind=='content_block_delta' and data.get('delta',{}).get('type')=='text_delta': delta=data['delta']['text']
                            if kind=='message_start':
                                u=data.get('message',{}).get('usage',{});read,write=u.get('cache_read_input_tokens',0),u.get('cache_creation_input_tokens',0)
                                usage.update(tokens_in=u.get('input_tokens',0)+read+write,cache_read=read,cache_write=write)
                            if kind=='message_delta':
                                usage['tokens_out']=data.get('usage',{}).get('output_tokens',0)
                                if data.get('delta',{}).get('stop_reason')=='max_tokens': raise ProviderError('AI_OUTPUT_LIMIT',usage=usage)
                            if kind=='message_stop': completed=True
                        elif self.id=='openai':
                            if kind=='response.output_text.delta': delta=data.get('delta','')
                            if kind in {'response.failed','response.incomplete'}: raise ProviderError('AI_OUTPUT_LIMIT' if kind.endswith('incomplete') else 'AI_REQUEST_FAILED',usage=usage)
                            if kind=='response.completed':
                                u=data.get('response',{}).get('usage',{});usage={'tokens_in':u.get('input_tokens',0),'tokens_out':u.get('output_tokens',0),'cache_read':(u.get('input_tokens_details') or {}).get('cached_tokens',0),'cache_write':0};completed=True
                        else:
                            for candidate in data.get('candidates',[]):
                                delta+=''.join(p.get('text','') for p in candidate.get('content',{}).get('parts',[]) if not p.get('thought'))
                                if candidate.get('finishReason'):
                                    if candidate['finishReason']!='STOP': raise ProviderError('AI_OUTPUT_LIMIT',usage=usage)
                                    completed=True
                            # Gemini bills thinking separately from candidates; count both as output.
                            u=data.get('usageMetadata',{})
                            if u:usage={'tokens_in':u.get('promptTokenCount',usage['tokens_in']),'tokens_out':u.get('candidatesTokenCount',0)+u.get('thoughtsTokenCount',0),'cache_read':u.get('cachedContentTokenCount',0),'cache_write':0}
                        safe=redact.push(delta)
                        if safe: yield {'text':safe}
            if not completed: raise ProviderError('AI_STREAM_INTERRUPTED',usage=usage)
            safe=redact.push(final=True)
            if safe: yield {'text':safe}
            yield {'usage':usage}
        except ProviderError: raise
        except Exception: raise ProviderError('AI_NETWORK_ERROR',usage=usage) from None

    # Saving mode: the providers' batch APIs run the same requests at half price,
    # usually within an hour and at most 24 hours later. A handle is plain JSON so
    # the pipeline can persist it and pick the batch up again after a restart.
    supports_batch = True
    GEMINI_BATCH_BYTES = 18_000_000  # inline batch requests must stay under 20 MB
    SAFE_ID = re.compile(r'[A-Za-z0-9_./-]{1,200}')

    def safe(self, value):
        if not isinstance(value, str) or not self.SAFE_ID.fullmatch(value) or '..' in value:
            raise ProviderError('AI_REQUEST_FAILED')
        return value

    async def batch_call(self, method, path, **kwargs):
        key=self.vault.get()
        if not key: raise ProviderError('AI_CONNECTION_REQUIRED')
        try:
            async with self.client() as client:
                response=await client.request(method,path,headers=self.headers(key),**kwargs)
        except Exception: raise ProviderError('AI_NETWORK_ERROR') from None
        self.check(response)
        return response

    def batch_body(self, stage, model, payload, image):
        _,body=self.body(model,prompt_for(stage),[{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],image,STAGE_EFFORT.get(stage))
        body.pop('stream',None)
        return body

    def chunks(self, items):
        chunk=[];size=0
        for key,body in items:
            n=len(json.dumps(body,ensure_ascii=False).encode())+200
            if chunk and size+n>self.GEMINI_BATCH_BYTES:
                yield chunk;chunk=[];size=0
            chunk.append((key,body));size+=n
        if chunk: yield chunk

    async def submit_batch(self, requests):
        """Submit [(key, stage, model, payload, image)]. OpenAI and Gemini take one
        model per batch, Gemini also caps the size, so one call may start several."""
        groups={}
        for key,stage,model,payload,image in requests:
            groups.setdefault('' if self.id=='anthropic' else model,[]).append((key,self.batch_body(stage,model,payload,image)))
        handle={'provider':self.id,'jobs':[]}
        try:
            for model,items in groups.items():
                if self.id=='anthropic':
                    data=(await self.batch_call('POST','/v1/messages/batches',json={'requests':[{'custom_id':k,'params':b} for k,b in items]})).json()
                    handle['jobs'].append({'id':self.safe(data.get('id')),'keys':[k for k,_ in items]})
                elif self.id=='openai':
                    lines=''.join(json.dumps({'custom_id':k,'method':'POST','url':'/v1/responses','body':b},ensure_ascii=False)+'\n' for k,b in items).encode()
                    upload=(await self.batch_call('POST','/v1/files',data={'purpose':'batch'},files={'file':('paperduet.jsonl',lines,'application/jsonl')})).json()
                    data=(await self.batch_call('POST','/v1/batches',json={'input_file_id':self.safe(upload.get('id')),'endpoint':'/v1/responses','completion_window':'24h'})).json()
                    handle['jobs'].append({'id':self.safe(data.get('id')),'file':upload['id'],'keys':[k for k,_ in items]})
                else:
                    for chunk in self.chunks(items):
                        body={'batch':{'display_name':'paperduet','input_config':{'requests':{'requests':[{'request':b,'metadata':{'key':k}} for k,b in chunk]}}}}
                        data=(await self.batch_call('POST',f'/v1beta/models/{model}:batchGenerateContent',json=body)).json()
                        handle['jobs'].append({'id':self.safe(data.get('name')),'keys':[k for k,_ in chunk]})
        except Exception:
            await self.cancel_batch(handle)  # never leave half a submission running and billing
            raise
        return handle

    async def batch_done(self, handle):
        for job in handle['jobs']:
            if job.get('ended'): continue
            if self.id=='anthropic':
                data=(await self.batch_call('GET',f"/v1/messages/batches/{job['id']}")).json()
                job['ended']=data.get('processing_status')=='ended'
            elif self.id=='openai':
                data=(await self.batch_call('GET',f"/v1/batches/{job['id']}")).json()
                job['ended']=data.get('status') in {'completed','failed','expired','cancelled'}
                job.update(output=data.get('output_file_id'),errors=data.get('error_file_id'),failed=data.get('status')=='failed')
            else:
                data=(await self.batch_call('GET',f"/v1beta/{job['id']}")).json()
                state=str((data.get('metadata') or {}).get('state',''))
                job['ended']=bool(data.get('done')) or state.endswith(('SUCCEEDED','FAILED','CANCELLED','EXPIRED'))
                job['failed']=state.endswith('FAILED')
        return all(job.get('ended') for job in handle['jobs'])

    def read_message(self, data):
        """One finished request, in the provider's non-streaming shape."""
        if self.id=='anthropic':
            u=data.get('usage') or {};read,write=u.get('cache_read_input_tokens',0),u.get('cache_creation_input_tokens',0)
            usage={'tokens_in':u.get('input_tokens',0)+read+write,'tokens_out':u.get('output_tokens',0),'cache_read':read,'cache_write':write}
            text=''.join(c.get('text','') for c in data.get('content',[]) if c.get('type')=='text')
            limit=data.get('stop_reason')=='max_tokens'
        elif self.id=='openai':
            u=data.get('usage') or {}
            usage={'tokens_in':u.get('input_tokens',0),'tokens_out':u.get('output_tokens',0),'cache_read':(u.get('input_tokens_details') or {}).get('cached_tokens',0),'cache_write':0}
            text=''.join(c.get('text','') for item in data.get('output',[]) if item.get('type')=='message' for c in item.get('content',[]) if c.get('type')=='output_text')
            limit=data.get('status')=='incomplete'
        else:
            u=data.get('usageMetadata') or {}
            usage={'tokens_in':u.get('promptTokenCount',0),'tokens_out':u.get('candidatesTokenCount',0)+u.get('thoughtsTokenCount',0),'cache_read':u.get('cachedContentTokenCount',0),'cache_write':0}
            candidate=(data.get('candidates') or [{}])[0]
            text=''.join(p.get('text','') for p in (candidate.get('content') or {}).get('parts',[]) if not p.get('thought'))
            limit=candidate.get('finishReason') not in (None,'STOP')
        if limit: return ProviderError('AI_OUTPUT_LIMIT'),usage
        try: return decode_answer(text),usage
        except (ValueError,TypeError): return ProviderError('AI_INVALID_JSON'),usage

    @staticmethod
    def batch_error(error, status=None):
        text=json.dumps(error or {}).lower()
        if status in (401,403) or 'authentication' in text or 'permission' in text: return ProviderError('AI_AUTH_FAILED')
        if status==400 or 'invalid_request' in text or 'invalid_argument' in text: return ProviderError('AI_REQUEST_FAILED')
        return ProviderError('BATCH_RETRY')  # overloaded or server-side trouble: safe to resubmit

    async def batch_results(self, handle):
        """{key: (answer or ProviderError, usage)}. A request with no answer
        (expired, cancelled, server error) comes back as BATCH_RETRY."""
        results={}
        for job in handle['jobs']:
            if self.id=='anthropic':
                text=(await self.batch_call('GET',f"/v1/messages/batches/{job['id']}/results")).text
                for line in filter(str.strip,text.splitlines()):
                    item=json.loads(line);result=item.get('result') or {}
                    if result.get('type')=='succeeded':results[item.get('custom_id')]=self.read_message(result.get('message') or {})
                    elif result.get('type')=='errored':results[item.get('custom_id')]=(self.batch_error(result.get('error')),{})
            elif self.id=='openai':
                for name in ('output','errors'):
                    if not job.get(name):continue
                    text=(await self.batch_call('GET',f"/v1/files/{self.safe(job[name])}/content")).text
                    for line in filter(str.strip,text.splitlines()):
                        item=json.loads(line);response=item.get('response') or {}
                        results[item.get('custom_id')]=self.read_message(response.get('body') or {}) if response.get('status_code')==200 else \
                            (self.batch_error(item.get('error') or (response.get('body') or {}).get('error'),response.get('status_code')),{})
            else:
                data=(await self.batch_call('GET',f"/v1beta/{job['id']}")).json()
                inlined=(data.get('response') or {}).get('inlinedResponses') or {}
                items=inlined.get('inlinedResponses',[]) if isinstance(inlined,dict) else inlined
                for i,item in enumerate(items):
                    key=(item.get('metadata') or {}).get('key') or (job['keys'][i] if i<len(job['keys']) else None)
                    results[key]=self.read_message(item['response']) if item.get('response') else (self.batch_error(item.get('error')),{})
            for key in job['keys']:
                results.setdefault(key,(ProviderError('AI_REQUEST_FAILED' if job.get('failed') else 'BATCH_RETRY'),{}))
        return {k:v for k,v in results.items() if k in {key for job in handle['jobs'] for key in job['keys']}}

    async def cancel_batch(self, handle):
        for job in handle['jobs']:
            if job.get('ended'):continue
            path={'anthropic':f"/v1/messages/batches/{job['id']}/cancel",'openai':f"/v1/batches/{job['id']}/cancel"}.get(self.id,f"/v1beta/{job['id']}:cancel")
            try:await self.batch_call('POST',path)
            except ProviderError:pass  # already finished or gone

    async def forget_batch(self, handle):
        """Delete finished batches and their files from the provider once the
        answers are stored locally; the paper's text has no reason to stay there."""
        for job in handle['jobs']:
            ids=[job['id']] if self.id!='openai' else [job[n] for n in ('file','output','errors') if job.get(n)]
            for value in ids:
                try:
                    path={'anthropic':f'/v1/messages/batches/{self.safe(value)}','openai':f'/v1/files/{self.safe(value)}'}.get(self.id,f'/v1beta/{self.safe(value)}')
                    await self.batch_call('DELETE',path)
                except ProviderError:pass


class ProviderRegistry:
    def __init__(self,store,vault=None):
        self.store=store
        self.vaults={p: vault if p=='anthropic' and vault else WindowsVault(provider=p) for p in APIProvider.BASES}

    def adapter(self,provider='anthropic',mode='api_key'):
        if provider not in self.vaults or mode not in {'api_key','cli'}: raise ProviderError('AI_REQUEST_FAILED')
        if mode=='api_key': return APIProvider(provider,self.vaults[provider])
        from .cli_provider import CLIProvider
        return CLIProvider(provider,self.store.provider_config(provider).get('cli_path'))

    def connected(self):
        options=self.store.pipeline_options()
        return self.adapter(options.provider,options.mode).connected()

    def redact(self,value):
        keys=[]
        for vault in self.vaults.values():
            try: keys.append(vault.get())
            except ProviderError: pass
        return Redactor(keys).clean(value)
