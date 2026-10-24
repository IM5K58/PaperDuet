import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.ask import AskInput, AskSettings, build_context
from paperduet.cli_provider import CLIProvider, execute, resolve_cli
from paperduet.provider import ProviderError
from paperduet.providers import APIProvider, Redactor
from paperduet.store import Store
from m2_sample import FIXTURE, seed

class Vault:
    def __init__(self,key=None):self.key=key
    def get(self):return self.key
    def set(self,value):self.key=value
    def delete(self):self.key=None

class ChatDouble:
    supports_images=True
    def __init__(self):self.calls=[];self.fail=False
    def connected(self):return True
    async def health_check(self):return {'status':'ok','models':[{'id':'mock','name':'Test'}]}
    async def stream(self,model,system,messages,image=None):
        self.calls.append((model,system,messages,image))
        if self.fail:raise ProviderError('AI_RATE_LIMIT')
        for text in ['**논문 명시**: 결과는 ','42.0입니다.\n\n','$$x=42.0$$\n\n','해석은 근거와 구분합니다.']:
            yield {'text':text};await asyncio.sleep(.005)
        yield {'usage':{'tokens_in':123,'tokens_out':45}}

def body(**extra):
    return {'doc_id':'m2-paper','anchor':{'block_id':'b0001','field':'en','start':0,'end':2,'text':'We'},'question':'쉽게 설명해 줘','model':'mock',**extra}

def events(response):return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]

@pytest.fixture
def api(tmp_path):
    seed(tmp_path);provider=ChatDouble();vault=Vault('test-key-secret-value')
    app=create_app('x'*64,tmp_path,FIXTURE,vault=vault,provider=provider)
    with TestClient(app,headers={'Authorization':'Bearer '+'x'*64}) as client:yield client,provider,app.state.store,vault

def test_ac7_context_exact_span_neighbors_references_abstract_and_full_paper(api):
    client,_,store,_=api
    response=client.post('/ask/context',json=body());assert response.status_code==200
    snapshot=response.json()['snapshot'];context=snapshot['context']
    assert context['selection']['text']=='We' and context['block']['ko']
    assert [b['id'] for b in context['references']]==['b0003']
    assert [b['id'] for b in context['neighbors']]==['b0000','b0002','b0003']
    assert context['abstract'] and context['glossary'][0]['term']=='Nova'
    full=client.post('/ask/context',json=body(full_context=True)).json()
    assert len(full['snapshot']['context']['full_paper'])==8
    assert full['estimated_tokens']>response.json()['estimated_tokens']
    assert client.post('/ask/context',json=body(anchor={'block_id':'b0001','text':'fake','start':0,'end':4})).status_code==422
    image=client.post('/ask/context',json=body(anchor={'block_id':'b0005'})).json()
    assert image['image_attached'] and image['snapshot']['context']['image']['block_id']=='b0005'
    assert 'data:image' not in json.dumps(image)

def test_context_is_source_only_and_full_paper_is_a_cacheable_prefix(api):
    client,_,_,_=api
    context=client.post('/ask/context',json=body()).json()['snapshot']['context']
    assert context['block']['ko'] and all('ko' not in b and 'qa_flags' not in b for b in context['neighbors'])
    full=client.post('/ask/context',json=body(full_context=True,preset='summary')).json()['snapshot']
    paper,question=full['messages'][0]['content']
    assert paper['cache'] and 'full_paper' in paper['text'] and 'full_paper' not in question['text']
    # The preset rides with the question so the system prompt never varies.
    assert '3문장' not in full['system'] and '3문장' in question['text']
    _,request=APIProvider('anthropic',None,None).body('claude-sonnet-5',full['system'],full['messages'],None)
    assert request['messages'][0]['content'][0]['cache_control']=={'type':'ephemeral'}
    assert 'cache_control' not in request['messages'][0]['content'][1]
    _,request=APIProvider('openai',None,None).body('gpt-5.4',full['system'],full['messages'],None)
    assert [p['text'] for p in request['input'][0]['content']]==[paper['text'],question['text']]

def test_ac7_equation_and_figure_reference_resolution_and_utf16(tmp_path):
    store,_=seed(tmp_path);doc=store.document('m2-paper');b=doc.blocks[1]
    b.en='😀 See Figure 1 and Eq. (1). Table 1.';store.save_blocks(doc.id,[b])
    req=AskInput(**body(anchor={'block_id':'b0001','field':'en','start':3,'end':6,'text':'See'}))
    context,_=build_context(store,req,AskSettings())
    assert {b['id'] for b in context['references']}=={'b0003','b0004','b0005'}

def test_compact_thread_keeps_message_evidence_available_on_demand(api):
    client,_,_,_=api
    result=events(client.post('/ask',json=body(full_context=True)))
    tid=result[0]['thread_id'];mid=result[-1]['message_id']
    full=client.get('/threads/'+tid).json()
    compact=client.get('/threads/'+tid+'?compact=true').json()
    assert [m['content_md'] for m in compact['messages']]==[m['content_md'] for m in full['messages']]
    assert compact['messages'][-1]['context_snapshot']['context']['selection']['text']=='We'
    assert 'messages' not in compact['messages'][-1]['context_snapshot']
    assert client.get(f'/threads/{tid}/messages/{mid}/context').json()==full['messages'][-1]['context_snapshot']
    assert client.get(f'/threads/wrong/messages/{mid}/context').status_code==404
    assert client.post('/ask',json=body(thread_id=tid,regenerate=True)).status_code==200


def test_ac8_stream_followup_regeneration_usage_persist_and_save_note(api):
    client,provider,store,_=api
    result=events(client.post('/ask',json=body()));tid=result[0]['thread_id'];mid=result[-1]['message_id']
    assert result[-1]['status']=='complete' and result[-1]['usage']=={'tokens_in':123,'tokens_out':45}
    assert ''.join(e.get('text','') for e in result).startswith('**논문 명시**')
    thread=client.get('/threads/'+tid).json()
    assert len(thread['messages'])==2 and thread['anchor']['text']=='We'
    assert thread['messages'][1]['context_snapshot']['messages']==provider.calls[0][2]
    client.post('/ask',json=body(thread_id=tid,question='다시 설명'))
    assert [m['role'] for m in provider.calls[-1][2]]==['user','assistant','user']
    previous=json.loads(provider.calls[-1][2][0]['content'])
    assert previous['question']=='쉽게 설명해 줘' and previous['selection']['text']=='We'
    client.post('/ask',json=body(thread_id=tid,regenerate=True))
    assert len(client.get('/threads/'+tid).json()['messages'])==5
    saved=client.post(f'/threads/{tid}/save-as-note',json={'message_id':mid,'kind':'ins'})
    assert saved.status_code==200 and saved.json()['note']['origin']=='ai_answer'
    doc=store.document('m2-paper');ids=[b.id for b in doc.blocks]
    assert ids.index(saved.json()['id'])==ids.index('b0001')+1
    client.post(f'/threads/{tid}/save-as-note',json={'message_id':mid,'kind':'key'})
    assert sum(b.id==saved.json()['id'] for b in store.document(doc.id).blocks)==1

def test_errors_and_recovery_do_not_look_complete(api):
    client,provider,store,_=api;provider.fail=True
    result=events(client.post('/ask',json=body()));tid=result[0]['thread_id']
    assert result[-1]['status']=='failed' and result[-2]['code']=='AI_RATE_LIMIT'
    record=client.get('/threads/'+tid).json()['messages'][-1]
    assert record['status']=='failed' and record['tokens_in'] is None
    assert client.post('/threads/'+tid+'/save-as-note',json={'message_id':record['id'],'kind':'key'}).status_code==422
    with store.connect() as db:db.execute("UPDATE messages SET status='running' WHERE id=?",(record['id'],))
    from paperduet.ask import AskService
    from paperduet.providers import ProviderRegistry
    AskService(store,ProviderRegistry(store,Vault()))
    assert client.get('/threads/'+tid).json()['messages'][-1]['status']=='interrupted'


def test_paper_questions_and_changing_selections_keep_one_conversation(api):
    client,provider,store,_=api
    paper={'block_id':'b0000','field':'document'}
    context=client.post('/ask/context',json=body(anchor=paper)).json()['snapshot']['context']
    assert context['block'] is None and context['neighbors']==[] and context['image'] is None
    assert len(context['full_paper'])==8 and context['selection']['field']=='document'
    first=events(client.post('/ask',json=body()))
    tid=first[0]['thread_id'];first_mid=first[-1]['message_id']
    second=events(client.post('/ask',json=body(thread_id=tid,anchor={'block_id':'b0005'},question='이번에는 그림을 설명해 줘')))
    assert second[0]['thread_id']==tid and second[0]['context']['context']['image']['attached']
    assert second[0]['context']['context']['selection']['block_id']=='b0005'
    assert len(provider.calls[-1][2])==3
    # Switching the current selection must not change the source of a regeneration.
    regenerated=events(client.post('/ask',json=body(thread_id=tid,anchor=paper,regenerate=True)))
    assert regenerated[0]['context']['context']['selection']['block_id']=='b0005'
    assert regenerated[0]['context']['context']['image']['attached']
    assert json.loads(provider.calls[-1][2][-1]['content'])['question']=='이번에는 그림을 설명해 줘'
    later=events(client.post('/ask',json=body(thread_id=tid,anchor=paper,question='논문 전체의 기여는?')))
    assert later[0]['context']['context']['block'] is None
    assert len(client.get('/threads?doc_id=m2-paper').json())==1
    # Earlier answers still attach to their own source, not the latest selection.
    note=client.post(f'/threads/{tid}/save-as-note',json={'message_id':first_mid,'kind':'ins'}).json()
    assert note['note']['refs'][0]=='b0001'
    memo=client.post('/documents/m2-paper/memos',json={'message_id':second[-1]['message_id'],'title':'그림 답변'}).json()
    assert memo['block_id']=='b0005'
    memo=client.post('/documents/m2-paper/memos',json={'message_id':later[-1]['message_id'],'title':'논문 답변'}).json()
    assert memo['block_id'] is None
    assert client.post('/ask/context',json=body(thread_id=tid,doc_id='rex-omni')).status_code==422
    assert client.post('/ask/context',json=body(thread_id=tid,anchor={'block_id':'b0001','text':'fake','end':4})).status_code==422


def test_thread_titles_rename_persist_validate_and_preserve_existing_conversations(api):
    client,_,store,vault=api
    tid=events(client.post('/ask',json=body(thread_title='처음 이름')))[0]['thread_id']
    assert client.get('/threads/'+tid).json()['title']=='처음 이름'
    assert client.patch('/threads/'+tid,json={'title':'  방법 비교  '}).json()['title']=='방법 비교'
    assert client.get('/threads?doc_id=m2-paper').json()[0]['title']=='방법 비교'
    assert len(client.get('/threads/'+tid).json()['messages'])==2
    assert client.patch('/threads/'+tid,json={'title':'   '}).status_code==422
    assert client.patch('/threads/'+tid,json={'title':'x'*121}).status_code==422
    assert client.patch('/threads/missing',json={'title':'제목'}).status_code==404
    client.patch('/threads/'+tid,json={'title':'비밀 '+vault.key})
    assert vault.key not in client.get('/threads/'+tid).text
    # Simulate an existing version-8 database and keep its conversation on upgrade.
    with store.connect() as db:
        db.execute('ALTER TABLE threads DROP COLUMN title')
        db.execute('DELETE FROM schema_migrations WHERE version=9')
        db.execute('PRAGMA user_version=8')
    upgraded=Store(store.data_dir,FIXTURE)
    with upgraded.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==9
        assert db.execute('SELECT title FROM threads WHERE id=?',(tid,)).fetchone()[0]==''
        assert db.execute('SELECT COUNT(*) FROM messages WHERE thread_id=?',(tid,)).fetchone()[0]==2


def test_independent_threads_generate_and_rename_concurrently(api):
    client,_,_,_=api
    gates={name:threading.Event() for name in ('A','B')}
    class Gated(ChatDouble):
        async def stream(self,model,system,messages,image=None):
            yield {'text':model+' 시작'}
            while not gates[model].is_set():await asyncio.sleep(.01)
            yield {'text':model+' 완료'}
            yield {'usage':{'tokens_in':1,'tokens_out':2}}
    client.app.state.ask.override=Gated()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.post,'/ask',json=body(model='A',thread_title='대화 A'))
        second=pool.submit(client.post,'/ask',json=body(model='B',thread_title='대화 B'))
        try:
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                rows=client.get('/threads?doc_id=m2-paper').json()
                if len(rows)==2 and all(t['running'] for t in rows):break
                time.sleep(.01)
            assert len(rows)==2 and all(t['running'] for t in rows)
            a=next(t['id'] for t in rows if t['title']=='대화 A')
            assert client.patch('/threads/'+a,json={'title':'생성 중 이름 변경'}).status_code==200
            assert client.post('/ask',json=body(thread_id=a)).status_code==409
            gates['B'].set();assert events(second.result(timeout=5))[-1]['status']=='complete'
            assert not first.done() and client.get('/threads/'+a).json()['title']=='생성 중 이름 변경'
            gates['A'].set();assert events(first.result(timeout=5))[-1]['status']=='complete'
            assert all(not t['running'] for t in client.get('/threads?doc_id=m2-paper').json())
        finally:
            for gate in gates.values():gate.set()

def test_presets_settings_and_known_secret_redaction(api):
    client,_,store,vault=api
    settings=client.get('/settings/ask').json();assert len(settings['presets'])==9
    settings['presets'].append({'id':'custom','label':'내 질문','instruction':'근거부터 설명해 주세요.'})
    settings['system']+=' '+vault.key
    assert client.put('/settings/ask',json=settings).status_code==204
    result=events(client.post('/ask',json=body(question='질문 '+vault.key,preset='custom')))
    tid=result[0]['thread_id'];thread=client.get('/threads/'+tid)
    assert vault.key not in thread.text and vault.key not in client.get('/settings/ask').text
    assert '근거부터 설명' in thread.text
    with store.connect() as db:
        for row in db.iterdump():assert vault.key not in row

def test_stream_redactor_handles_every_secret_split():
    key='sk-abcdefgh123456789'
    for split in range(1,len(key)):
        r=Redactor([key]);out=r.push('before '+key[:split])+r.push(key[split:]+' after')+r.push(final=True)
        assert key not in out and out=='before [REDACTED] after'

@pytest.mark.parametrize('provider',['anthropic','openai','google'])
def test_three_api_transports_stream_models_and_headers(provider):
    key='credential-is-private-123';seen=[]
    def handler(req):
        seen.append(req)
        assert key not in str(req.url)
        header={'anthropic':'x-api-key','openai':'authorization','google':'x-goog-api-key'}[provider]
        assert key in req.headers[header]
        if req.method=='GET':
            data={'data':[{'id':'gpt-5.4'}]} if provider=='openai' else {'models':[{'name':'models/gemini-test','supportedGenerationMethods':['generateContent']}]} if provider=='google' else {'data':[{'id':'claude-test'}]}
            return httpx.Response(200,json=data)
        body=json.loads(req.content);assert 'tools' not in body
        if provider=='anthropic':items=[{'type':'message_start','message':{'usage':{'input_tokens':5,'cache_read_input_tokens':4,'cache_creation_input_tokens':2}}},{'type':'content_block_delta','delta':{'type':'text_delta','text':'답변 '+key}},{'type':'message_delta','usage':{'output_tokens':7}},{'type':'message_stop'}]
        elif provider=='openai':
            assert body['store'] is False
            items=[{'type':'response.output_text.delta','delta':'답변 '+key},{'type':'response.completed','response':{'usage':{'input_tokens':11,'output_tokens':7,'input_tokens_details':{'cached_tokens':4}}}}]
        else:items=[{'candidates':[{'content':{'parts':[{'text':'답변 '+key}]},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':11,'candidatesTokenCount':5,'thoughtsTokenCount':2,'cachedContentTokenCount':4}}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(v)+'\n\n' for v in items),headers={'content-type':'text/event-stream'})
    adapter=APIProvider(provider,Vault(key),httpx.MockTransport(handler))
    async def check():
        assert (await adapter.health_check())['models']
        parts=[c async for c in adapter.stream('test-model','rules',[{'role':'user','content':'질문'}])]
        assert ''.join(c.get('text','') for c in parts)=='답변 [REDACTED]'
        # Input includes cached tokens; Gemini thinking counts as output.
        assert parts[-1]['usage']=={'tokens_in':11,'tokens_out':7,'cache_read':4,'cache_write':2 if provider=='anthropic' else 0}
    asyncio.run(check());assert len(seen)==2

def test_api_transport_error_never_echoes_key():
    key='secret-credential-value'
    adapter=APIProvider('openai',Vault(key),httpx.MockTransport(lambda req:httpx.Response(401,text=key)))
    with pytest.raises(ProviderError,match='AI_AUTH_FAILED'):
        asyncio.run(adapter.list_models())

def test_cli_command_isolated_stdin_utf8_and_stream_parsing(monkeypatch,tmp_path):
    import paperduet.cli_provider as cli
    calls=[]
    monkeypatch.setattr(cli,'resolve_cli',lambda *args:['official.exe'])
    async def fake(command,data,cwd,timeout=600):
        calls.append((command,data,cwd))
        if '--help' in command:
            yield '--tools --strict-mcp-config --setting-sources --no-session-persistence --ignore-user-config --ignore-rules --ephemeral';return
        assert '논문 한글' not in ' '.join(command) and '논문 한글' in data
        assert not list(Path(cwd).iterdir())
        if '-p' in command:
            assert command[command.index('--tools')+1]=='' and '--strict-mcp-config' in command
            yield json.dumps({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'안녕하세요'}}})
            yield json.dumps({'type':'result','result':'안녕하세요','usage':{'input_tokens':2,'output_tokens':3}})
        else:
            assert command[command.index('--sandbox')+1]=='read-only'
            yield json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'안녕하세요'}})
            yield json.dumps({'type':'turn.completed','usage':{'input_tokens':2,'output_tokens':3}})
    monkeypatch.setattr(cli,'execute',fake)
    async def check():
        for provider in ['anthropic','openai']:
            result=[c async for c in CLIProvider(provider).stream('model','rules',[{'role':'user','content':'논문 한글 & $(bad)'}])]
            assert ''.join(c.get('text','') for c in result)=='안녕하세요'
    asyncio.run(check());assert all(not Path(cwd).exists() for _,_,cwd in calls)

@pytest.mark.skipif(sys.platform!='win32',reason='Windows process jobs')
def test_cli_real_child_utf8_and_timeout_no_orphans(tmp_path):
    script=tmp_path/'프로세스 확인.py';marker=tmp_path/'child.pid'
    script.write_text("import sys,subprocess,os,time\nprint(sys.stdin.read(),flush=True)\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(str(p.pid))\ntime.sleep(60)\n",encoding='utf-8')
    result=[]
    async def check():
        with pytest.raises(ProviderError,match='CLI_TIMEOUT'):
            async for line in execute([sys.executable,str(script),str(marker)],'한국어 & " stdin',tmp_path,2):result.append(line)
    asyncio.run(check());assert result==['한국어 & " stdin']
    import ctypes
    kernel=ctypes.WinDLL('kernel32');kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,int(marker.read_text()))
    if handle:
        code=ctypes.c_ulong();kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.c_void_p];kernel.GetExitCodeProcess(handle,ctypes.byref(code));kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle)
        assert code.value!=259

def test_cli_npm_shim_resolution_does_not_execute_or_read_credentials(tmp_path,monkeypatch):
    import paperduet.cli_provider as cli
    monkeypatch.setattr(cli,'cli_enabled',lambda:True)
    shim=tmp_path/'claude.cmd';shim.write_text('untrusted shim commands')
    exe=tmp_path/'node_modules/@anthropic-ai/claude-code/bin/claude.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'not executed')
    assert resolve_cli('anthropic',str(shim))==[str(exe)]
    monkeypatch.setattr(cli,'cli_enabled',lambda:False)
    with pytest.raises(ProviderError,match='CLI_DISABLED'):resolve_cli('anthropic',str(shim))

def test_cli_error_closes_generator_before_temp_cleanup(monkeypatch):
    import paperduet.cli_provider as cli
    closed=[]
    monkeypatch.setattr(cli,'resolve_cli',lambda *args:['official.exe'])
    async def fake(command,data,cwd,timeout=600):
        if '--help' in command:
            yield '--ignore-user-config --ignore-rules --ephemeral';return
        try:yield '{"type":"turn.failed"}'
        finally:closed.append(Path(cwd).is_dir())
    monkeypatch.setattr(cli,'execute',fake)
    async def check():
        with pytest.raises(ProviderError,match='CLI_REQUEST_FAILED'):
            async for _ in CLIProvider('openai').stream('default','test',[{'role':'user','content':'질문'}]):pass
    asyncio.run(check());assert closed==[True]

def test_original_page_is_authenticated_and_bounded(api,tmp_path):
    import pymupdf
    client,_,store,_=api
    path=tmp_path/'documents/m2-paper/source.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,50),'Original page');pdf.save(path)
    with store.connect() as db:db.execute("UPDATE documents SET source_path='documents/m2-paper/source.pdf' WHERE id='m2-paper'")
    image=client.get('/documents/m2-paper/pages/1/image');assert image.status_code==200
    assert image.json()['data_url'].startswith('data:image/png;base64,')
    assert client.get('/documents/m2-paper/pages/0/image').status_code==404
    assert client.get('/documents/m2-paper/pages/2/image').status_code==404
    assert client.get('/documents/m2-paper/pages/1/image',headers={'Authorization':''}).status_code==401

def test_block_image_does_not_load_the_whole_document(api,monkeypatch):
    client,_,store,_=api
    def unexpected(*args,**kwargs):raise AssertionError('Image lookup loaded the entire paper')
    monkeypatch.setattr(store,'document',unexpected)
    image=client.get('/documents/m2-paper/blocks/b0005/image')
    assert image.status_code==200 and image.json()['data_url'].startswith('data:image/png;base64,')
    assert client.get('/documents/m2-paper/blocks/missing/image').status_code==404

def test_cli_sources_never_read_credentials():
    root=Path(__file__).resolve().parents[1]/'paperduet'
    source='\n'.join(p.read_text(encoding='utf-8') for p in root.glob('*.py'))
    assert 'shell=True' not in source
    for forbidden in ['auth.json','.credentials.json','credentials.json','access_token','refresh_token','oauth/token']:
        assert forbidden not in source


def test_document_assets_with_windows_virtualization_and_escape_guards(api,tmp_path,monkeypatch):
    import shutil
    import pymupdf
    client,_,store,_=api
    logical=tmp_path/'documents/m2-paper'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,50),'Virtualized original');pdf.save(logical/'source.pdf')
    with store.connect() as db:
        db.execute("UPDATE documents SET source_path='documents/m2-paper/source.pdf' WHERE id='m2-paper'")
    physical=tmp_path/'virtualized/document'
    shutil.copytree(logical,physical)
    original_resolve=Path.resolve
    outside=tmp_path/'outside.png';outside.write_bytes(b'outside')
    def redirected(path,*args,**kwargs):
        if path==logical/'escape.png':return original_resolve(outside)
        if path.is_relative_to(logical):return original_resolve(physical/path.relative_to(logical))
        return original_resolve(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',redirected)
    assert client.get('/documents/m2-paper/pages/1/image').status_code==200
    assert client.get('/documents/m2-paper/blocks/b0005/image').status_code==200
    assert client.post('/ask/context',json=body(anchor={'block_id':'b0005'})).json()['image_attached']
    for path in ['documents/other/source.pdf','documents/m2-paper/../other/source.pdf',str(outside),'documents/m2-paper/escape.png']:
        assert store.asset_path('m2-paper',path) is None
    with store.connect() as db:
        db.execute("UPDATE documents SET source_path=? WHERE id='m2-paper'",(str(outside),))
    assert client.get('/documents/m2-paper/pages/1/image').status_code==404

def test_reextract_upgrade_protects_edited_documents(tmp_path):
    from paperduet.pipeline import Pipeline
    store,doc_id=seed(tmp_path)
    with store.connect() as db:
        row=db.execute('SELECT checkpoint FROM jobs WHERE doc_id=?',(doc_id,)).fetchone();checkpoint=json.loads(row[0]);checkpoint['ai_approved']=False
        db.execute('UPDATE jobs SET checkpoint=? WHERE doc_id=?',(json.dumps(checkpoint),doc_id))
    pipeline=Pipeline(store,ChatDouble());asyncio.run(pipeline.recover())
    assert not pipeline.tasks and store.document(doc_id).blocks[1].ko


def test_pause_wins_over_late_checkpoint_until_explicit_resume(api):
    client,_,store,_=api
    store.update_job('m2-paper','Extract','running',.2)
    assert client.post('/documents/m2-paper/pause').status_code==204
    store.update_job('m2-paper','Glossary','awaiting_ai',.3,extracted_pages=1)
    assert store.job('m2-paper')['status']=='paused'
    store.update_job('m2-paper','Glossary','queued',.3)
    assert store.job('m2-paper')['status']=='queued'

def test_onboarding_and_new_provider_settings_do_not_return_keys(api):
    client,_,_,_=api
    assert client.get('/settings/onboarding').json()=={'completed':False}
    assert client.put('/settings/onboarding',json={'completed':True}).status_code==204
    assert client.get('/settings/onboarding').json()['completed']
    response=client.get('/settings/providers');assert 'api_key' not in response.json().keys()
    assert client.put('/settings/providers',json={'provider':'google','mode':'cli'}).status_code==422

def test_visualad_regression_if_available(tmp_path):
    from paperduet.pdf_parser import PyMuPDFParser, overlap
    path=Path(os.environ.get('PAPERDUET_VISUALAD_PDF',Path(__file__).resolve().parents[2]/'artifacts/m3/visualad.pdf'))
    if not path.is_file():pytest.skip('VisualAD PDF unavailable')
    parser=PyMuPDFParser();meta=parser.metadata(path)
    blocks,sources=parser.structure('visualad',[parser.page(path,i,tmp_path) for i in range(meta['page_count'])],meta)
    assert meta['page_count']==11
    assert [b.n for b in blocks if b.type=='fig']==[f'Figure {n}' for n in range(1,7)]
    assert len([b for b in blocks if b.type=='tab'])==5
    assert {b.n for b in blocks if b.type=='eq'}=={f'({i})' for i in range(1,14)}
    first=next(s for b,s in zip(blocks,sources) if b.n=='Figure 1')
    assert all(not overlap(s['bbox'],first['region']) for b,s in zip(blocks,sources) if b.page==1 and b.type=='p')
    for b,s in zip(blocks,sources):
        if b.type=='eq':assert s['region'][2]-s['region'][0]<300  # Never span both columns.

def test_busy_provider_is_retried_before_the_first_chunk_and_reported_as_busy():
    # Gemini answers 503 "high demand" under load; that is not a rejected request.
    answer='data: '+json.dumps({'candidates':[{'content':{'parts':[{'text':'{"ok": 1}'}]},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':3,'candidatesTokenCount':2}})+'\n\n'
    calls=[]
    def recovering(request):
        calls.append(request);return httpx.Response(503) if len(calls)<3 else httpx.Response(200,text=answer)
    def provider(handler):
        p=APIProvider('google',Vault('test-key-secret-value'),httpx.MockTransport(handler));p.RETRY_DELAYS=(0,0,0);p.TRANSIENT_DELAYS=(0,0,0);return p
    value,usage=asyncio.run(provider(recovering).json('translate','gemini-3.8-flash',{'blocks':[]}))
    assert value=={'ok':1} and len(calls)==3 and usage['tokens_in']==3
    with pytest.raises(ProviderError,match='AI_OVERLOADED'):
        asyncio.run(provider(lambda r:httpx.Response(503)).json('translate','gemini-3.8-flash',{}))
    with pytest.raises(ProviderError,match='AI_MODEL_NOT_FOUND'):
        asyncio.run(provider(lambda r:httpx.Response(404)).json('translate','gemini-9-nope',{}))

def test_busy_error_in_the_middle_of_an_answer_is_retried_for_the_pipeline():
    # Observed from Gemini: HTTP 200, a few chunks of the answer, then a 503 event.
    usage={'promptTokenCount':3,'candidatesTokenCount':1}
    chunk=lambda text,**extra:'data: '+json.dumps({'candidates':[{'content':{'parts':[{'text':text}]},**extra}],'usageMetadata':usage})+'\n\n'
    broken=chunk('{"blocks":[{"id":"b0000","ko":"초록"},{"id":"b0001","ko":"본 논문은')+'data: '+json.dumps({'error':{'code':503,'message':'This model is currently experiencing high demand.','status':'UNAVAILABLE'}})+'\n\n'
    calls=[]
    def flaky(request):
        calls.append(request);return httpx.Response(200,text=broken if len(calls)<3 else chunk('{"ok": 1}',finishReason='STOP'))
    p=APIProvider('google',Vault('test-key-secret-value'),httpx.MockTransport(flaky));p.RETRY_DELAYS=(0,0,0);p.TRANSIENT_DELAYS=(0,0,0)
    value,total=asyncio.run(p.json('translate','gemini-3.8-flash',{'blocks':[]}))
    assert value=={'ok':1} and len(calls)==3
    assert total['tokens_in']==9 and total['requests']==3  # the broken attempts are billed too
    # Ask streams to the reader, so it is not resent mid-answer, but says the server is busy.
    async def ask():
        return [e async for e in APIProvider('google',Vault('test-key-secret-value'),httpx.MockTransport(lambda r:httpx.Response(200,text=broken))).stream('gemini-3.8-flash','s',[{'role':'user','content':'q'}])]
    with pytest.raises(ProviderError,match='AI_OVERLOADED'):asyncio.run(ask())
    assert APIProvider.stream_error({'error':{'code':429,'status':'RESOURCE_EXHAUSTED'}})=='AI_RATE_LIMIT'
    assert APIProvider.stream_error({'type':'error','error':{'type':'overloaded_error'}})=='AI_OVERLOADED'
    assert APIProvider.stream_error({'error':{'code':400,'status':'INVALID_ARGUMENT'}})=='AI_REQUEST_FAILED'
