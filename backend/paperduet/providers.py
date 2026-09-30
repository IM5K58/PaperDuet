"""Common streaming adapters for pipeline and Ask; never execute model tools."""
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


class StructuredAdapter:
    async def json(self, stage, model, payload, image=None):
        prompt = (Path(__file__).with_name('prompts') / f'{stage}.md').read_text(encoding='utf-8')
        total = {'tokens_in': 0, 'tokens_out': 0, 'cache_read': 0, 'cache_write': 0, 'requests': 0}
        for attempt in range(2):
            answer = ''
            total['requests'] += 1
            async for event in self.stream(model, prompt, [{'role':'user','content':json.dumps(payload,ensure_ascii=False)}], image, effort=STAGE_EFFORT.get(stage)):
                answer += event.get('text','')
                for key, value in event.get('usage',{}).items():
                    total[key] = total.get(key, 0) + value
            answer = answer.strip()
            if answer.startswith('```'):
                answer = answer.split('\n',1)[-1].rsplit('```',1)[0].strip()
            try:
                return json.loads(answer), total
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
            prepared=[{'role':m['role'],'content':[{'type':'text','text':m['content']}]} for m in messages]
            if data: prepared[-1]['content'].insert(0,{'type':'image','source':{'type':'base64','media_type':'image/png','data':data}})
            body={'model':model,'system':system,'messages':prepared,'max_tokens':16000,'stream':True}
            if effort and ANTHROPIC_EFFORT.match(model): body['output_config']={'effort':effort}
            return '/v1/messages',body
        if self.id=='openai':
            prepared=[{'role':m['role'],'content':[{'type':'output_text' if m['role']=='assistant' else 'input_text','text':m['content']}]} for m in messages]
            if data: prepared[-1]['content'].append({'type':'input_image','image_url':'data:image/png;base64,'+data})
            body={'model':model,'instructions':system,'input':prepared,'max_output_tokens':16000,'stream':True,'store':False}
            if effort and OPENAI_REASONING.match(model): body['reasoning']={'effort':effort}
            return '/v1/responses',body
        prepared=[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':m['content']}]} for m in messages]
        if data: prepared[-1]['parts'].append({'inlineData':{'mimeType':'image/png','data':data}})
        config={'maxOutputTokens':16000}
        if effort and re.match(r'gemini-[3-9]',model): config['thinkingConfig']={'thinkingLevel':effort}
        return f'/v1beta/models/{model}:streamGenerateContent?alt=sse',{'systemInstruction':{'parts':[{'text':system}]},'contents':prepared,'generationConfig':config}

    async def stream(self, model, system, messages, image=None, effort=None):
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
                        if kind=='error' or data.get('error'): raise ProviderError('AI_REQUEST_FAILED',usage=usage)
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
