import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.models import Block, Cell, Table, PipelineOptions
from paperduet.pdf_parser import PyMuPDFParser
from paperduet.provider import AnthropicProvider, ProviderError
from paperduet.store import Store
from paperduet.validation import table_grid, validate_block

FIXTURE=Path(__file__).resolve().parents[2]/'fixtures/rex-omni.blocks.json'


class MemoryVault:
    def __init__(self): self.value=None
    def get(self): return self.value
    def set(self,value): self.value=value
    def delete(self): self.value=None


class DeterministicProvider:
    """Deliberate test double, never included in production source or bundle."""
    def __init__(self,vault): self.vault=vault;self.calls=[];self.fail_after=None
    def connected(self): return bool(self.vault.get())
    async def health_check(self): return {'status':'ok','models':[]}
    async def json(self,stage,model,payload,image=None):
        if not self.connected():raise ProviderError('AI_CONNECTION_REQUIRED')
        self.calls.append((stage,payload))
        if self.fail_after is not None and len(self.calls)>=self.fail_after:
            raise ProviderError('AI_RATE_LIMIT')
        if stage=='glossary':
            return [{'term':'model','ko':'모델','keep_english':False,'definition_ko':'모델이다.'}],{'tokens_in':12,'tokens_out':6}
        if stage=='translate':
            return {'blocks':[{'id':b['id'],'ko':'번역된 모델 문장이다. '+(b.get('en') or ''),'caption_ko':'캡션 '+(b.get('caption_en') or ''),
                'headers':[dict(row=h['row'],col=h['col'],text_ko='헤더 '+h['text_en']) for h in b.get('headers',[])]} for b in payload['blocks']]}, {'tokens_in':20,'tokens_out':10}
        if stage=='annotate':
            return {'notes':[]},{'tokens_in':10,'tokens_out':1}
        raise AssertionError('Unexpected call')


def make_pdf(pages=2):
    pdf=fitz.open()
    pdf.set_metadata({'title':'Two Column Study','author':'Example Author'})
    for i in range(pages):
        page=pdf.new_page(width=595,height=842)
        page.insert_text((50,80),'Two Column Study',fontsize=16,fontname='hebo')
        page.insert_text((50,120),f'{i+1}. Introduction',fontsize=13,fontname='hebo')
        for y,text in [(160,'Left first model paragraph 42.'),(210,'Left second model paragraph 7.')]:
            page.insert_text((50,y),text,fontsize=10)
        for y,text in [(160,'Right first model paragraph 91.'),(210,'Right second model paragraph 8.')]:
            page.insert_text((320,y),text,fontsize=10)
        if i==pages-1:
            page.insert_text((50,320),'References',fontsize=13,fontname='hebo')
            page.insert_text((50,350),'[1] Original reference must remain English. 2025.',fontsize=10)
        page.insert_text((290,812),str(i+1),fontsize=9)
    raw=pdf.tobytes();pdf.close();return raw


def wait_job(client,doc_id,statuses,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        job=client.get(f'/documents/{doc_id}/job').json()
        if job['status'] in statuses:return job
        time.sleep(.03)
    raise AssertionError(job)


def client_app(tmp_path,vault=None,provider=None):
    vault=vault or MemoryVault()
    provider=provider or DeterministicProvider(vault)
    token=secrets.token_hex(32)
    app=create_app(token,tmp_path,FIXTURE,vault=vault,provider=provider)
    return app,TestClient(app,headers={'Authorization':'Bearer '+token}),vault,provider


def test_two_columns_metadata_footnotes_and_stable_ids(tmp_path):
    path=tmp_path/'한글 파일.pdf';path.write_bytes(make_pdf())
    parser=PyMuPDFParser();meta=parser.metadata(path)
    pages=[parser.page(path,i,tmp_path/'crops') for i in range(2)]
    blocks,sources=parser.structure('doc',pages,meta)
    texts=[b.en for b in blocks]
    positions=[next(i for i,t in enumerate(texts) if needle in t) for needle in ['Left first','Left second','Right first','Right second']]
    assert positions==sorted(positions)
    assert meta['title']=='Two Column Study' and meta['page_count']==2
    assert all(b.id==f'b{i:04d}' for i,b in enumerate(blocks))
    assert sources[-1]['references']
    assert all('bbox' in source for source in sources)


def test_paragraphs_cut_by_breaks_figures_and_footnotes_are_rejoined():
    from paperduet.pdf_parser import rejoin_paragraphs
    p=lambda page,text,bbox=(50,600,290,700),**extra:{'type':'p','en':text,'bbox':list(bbox),'page':page,**extra}
    items=[p(1,'Anomaly detection plays a role in domains such as industrial inspection'),
           p(1,'* Corresponding author.',(70,705,150,713)),
           {'type':'fig','n':'Figure 1','caption_en':'Overview of the self- attention path.','bbox':[310,280,550,660],'region':[310,280,550,640],'page':1},
           p(1,'and medical diagnosis. It flags devi- ations at the image or pixel',(310,690,550,713)),
           p(2,'level, from <i>to-</i> <i>kens</i> to zero- shot and pre- and post-training.',(50,70,290,270)),
           p(2,'The objective differs.',(50,280,290,300)),
           p(3,'(3) After SAF',(400,225,440,234)),
           {'type':'fig','n':'Figure 2','bbox':[50,80,550,290],'region':[50,80,550,260],'page':3},
           p(3,'we compare encodings, and',(50,600,290,700)),
           {'type':'sec','n':'5','en':'Conclusion','bbox':[310,60,400,70],'page':3},
           p(3,'which starts lowercase but follows a heading.',(310,80,550,200)),
           p(4,'We conclude.',(50,70,290,90)),p(4,'zero-shot appears again',(50,100,290,120)),
           {'type':'sec','n':'References','en':'References','bbox':[50,130,200,140],'page':4},
           p(4,'[1] A. Author. Visual anomaly segmenta-',(50,150,290,170)),p(4,'tion, 2023.',(310,150,550,170))]
    out=rejoin_paragraphs(items)
    texts=[i.get('en') for i in out if i['type']=='p']
    # Footnote, figure and the next page are skipped over; the line-end hyphen is rejoined.
    assert texts[0]=='Anomaly detection plays a role in domains such as industrial inspection and medical diagnosis. '\
        'It flags deviations at the image or pixel level, from <i>tokens</i> to zero-shot and pre- and post-training.'
    assert out[0]['page']==1 and out[0]['bbox']==items[0]['bbox']
    assert texts[1:4]==['* Corresponding author.','The objective differs.','(3) After SAF']  # label inside the figure stays apart
    assert 'we compare encodings, and' in texts and 'which starts lowercase but follows a heading.' in texts  # never across headings
    assert texts[-2:]==['[1] A. Author. Visual anomaly segmenta-','tion, 2023.']  # references are left as extracted
    assert next(i for i in out if i.get('n')=='Figure 1')['caption_en']=='Overview of the self-attention path.'


def test_offline_ingest_sse_duplicate_resume_translation_and_persistence(tmp_path):
    data=tmp_path/'사용자 폴더 한글'
    app,client,vault,provider=client_app(data)
    with client:
        response=client.post('/documents',content=make_pdf(),headers={'Content-Type':'application/pdf'})
        assert response.status_code==202,response.text
        doc_id=response.json()['doc_id']
        job=wait_job(client,doc_id,{'awaiting_ai','failed'})
        assert job['status']=='awaiting_ai',job
        assert job['checkpoint']['completed_stages']==['Ingest','Extract','Structure']
        doc=client.get('/documents/'+doc_id).json()
        assert len(doc['blocks'])>=10 and all(not b.get('ko') for b in doc['blocks'])
        events=client.get(f'/documents/{doc_id}/progress')
        assert events.headers['content-type'].startswith('text/event-stream')
        assert 'event: progress' in events.text and 'awaiting_ai' in events.text
        assert not provider.calls
        assert client.post('/documents',content=make_pdf(),headers={'Content-Type':'application/pdf'}).status_code==202
        original=(data/'documents'/doc_id/'source.pdf').read_bytes()
        duplicate=client.post('/documents',content=original,headers={'Content-Type':'application/pdf'}).json()
        assert duplicate['duplicate'] and duplicate['doc_id']==doc_id
        vault.set('test-api-key-for-tests-only')
        result=client.post(f'/documents/{doc_id}/resume',json=PipelineOptions().model_dump())
        assert result.status_code==202,result.text
        job=wait_job(client,doc_id,{'complete','review','failed','paused'})
        assert job['status']=='complete',job
        doc=client.get('/documents/'+doc_id).json()
        references=[b for b in doc['blocks'] if 'References' in b['section_path']]
        assert references and all(not b.get('ko') for b in references)
        assert all(b.get('ko') for b in doc['blocks'] if b not in references)
        assert sum(u['tokens_in'] for u in job['usage'])>0
    # Open the actual SQLite files in a new app instance; no in-memory cache.
    _,again,_,_=client_app(data,vault,provider)
    with again:
        assert again.get('/documents/'+doc_id).json()['blocks']==doc['blocks']


def test_failed_stage_resumes_cached_extraction_without_retranslating(tmp_path):
    app,client,vault,provider=client_app(tmp_path)
    with client:
        doc_id=client.post('/documents',content=make_pdf(3),headers={'Content-Type':'application/pdf'}).json()['doc_id']
        wait_job(client,doc_id,{'awaiting_ai'})
        cache=tmp_path/'documents'/doc_id/'pages-v2'/'0000.json'
        before=cache.stat().st_mtime_ns
        vault.set('test-key-for-resume')
        provider.fail_after=3
        client.post(f'/documents/{doc_id}/resume',json=PipelineOptions().model_dump())
        paused=wait_job(client,doc_id,{'paused'})
        assert paused['checkpoint']['error']=='AI_RATE_LIMIT'
        assert paused['checkpoint']['translated']
        completed=set(paused['checkpoint']['translated'])
    provider.fail_after=None;provider.calls=[]
    _,again,_,_=client_app(tmp_path,vault,provider)
    with again:
        assert again.post(f'/documents/{doc_id}/resume',json=PipelineOptions().model_dump()).status_code==202
        assert wait_job(again,doc_id,{'complete','failed'})['status']=='complete'
        assert cache.stat().st_mtime_ns==before
        assert not any(stage=='glossary' for stage,_ in provider.calls)
        assert not any(b['id'] in completed for stage,p in provider.calls if stage=='translate' for b in p['blocks'])


def test_running_job_recovers_on_process_start(tmp_path):
    app,client,vault,provider=client_app(tmp_path)
    with client:
        doc_id=client.post('/documents',content=make_pdf(),headers={'Content-Type':'application/pdf'}).json()['doc_id']
        wait_job(client,doc_id,{'awaiting_ai'})
    store=Store(tmp_path,FIXTURE)
    store.update_job(doc_id,'Glossary','running',.3,ai_approved=True)
    vault.set('resume-test-key')
    _,again,_,_=client_app(tmp_path,vault,provider)
    with again:
        assert wait_job(again,doc_id,{'complete','failed'})['status']=='complete'


def test_key_only_in_vault_and_no_echo_in_errors(tmp_path,caplog):
    app,client,vault,_=client_app(tmp_path)
    secret='sk-ant-private-should-never-escape-12345'
    with client:
        result=client.put('/settings/providers',json={'api_key':secret})
        assert result.status_code==204 and vault.get()==secret
        for path in ['/settings/providers','/documents','/health','/providers/health']:
            assert secret not in client.get(path).text
        assert secret not in client.put('/settings/providers',json={'api_key':secret,'unknown':secret}).text
        assert client.delete('/settings/providers').status_code==204 and vault.get() is None
    assert secret not in caplog.text
    assert all(secret.encode() not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())


def test_anthropic_transport_auth_json_retry_and_redaction():
    vault=MemoryVault();vault.set('sk-ant-test-transport-secret')
    requests=[]
    def handle(request):
        requests.append(request)
        assert request.headers['x-api-key']==vault.get()
        if request.url.path=='/v1/models':return httpx.Response(200,json={'data':[{'id':'model-test','display_name':'Test'}]})
        text='not json' if len(requests)==2 else json.dumps({'value':vault.get()})
        return httpx.Response(200,json={'content':[{'type':'text','text':text}],'usage':{'input_tokens':3,'output_tokens':2},'stop_reason':'end_turn'})
    provider=AnthropicProvider(vault,httpx.MockTransport(handle))
    assert asyncio.run(provider.list_models())==[{'id':'model-test','name':'Test'}]
    data,usage=asyncio.run(provider.json('glossary','model-test',{'paper':'source text'}))
    assert data=={'value':'[REDACTED]'} and usage=={'tokens_in':6,'tokens_out':4,'cache_read':0,'cache_write':0,'requests':2}
    assert all(vault.get().encode() not in r.content for r in requests)


def test_validation_coverage_numbers_length_glossary_and_merged_tables():
    source=Block(id='b0',doc_id='d',order=0,type='p',section_path=[],en='The model achieves 42.0 percent accuracy in this controlled study.')
    block=source.model_copy(update={'ko':''})
    assert validate_block(block,source,[])==['V1']
    block.ko='짧다'
    assert validate_block(block,source,[{'term':'model','ko':'모델'}])==['V2','V3','V6']
    assert validate_block(block,source,[],references=True)==[]
    table=Table(header=[[Cell(text_en='Model',rowspan=2),Cell(text_en='Scores',colspan=2)],[Cell(text_en='A'),Cell(text_en='B')]],body=[[Cell(text_en='X'),Cell(text_en='1',numeric=True),Cell(text_en='2',numeric=True)]])
    assert table_grid(table)[1]==3
    source_table=Block(id='t',doc_id='d',order=0,type='tab',section_path=[],table=table.model_copy(deep=True))
    swapped=source_table.model_copy(deep=True)
    swapped.table.body[0][1].text_en='2';swapped.table.body[0][2].text_en='1'
    assert 'V4' in validate_block(swapped,source_table,[])
    table.body[0].pop()
    with pytest.raises(ValueError):table_grid(table)


def test_upload_auth_origin_invalid_files_and_path_isolation(tmp_path):
    _,client,_,_=client_app(tmp_path)
    with client:
        assert client.post('/documents',content=b'%PDF-broken',headers={'Content-Type':'application/pdf','Authorization':''}).status_code==401
        assert client.post('/documents',content=b'%PDF-broken',headers={'Content-Type':'application/pdf','Origin':'https://evil.example'}).status_code==403
        assert client.post('/documents',content=b'not PDF',headers={'Content-Type':'application/pdf'}).status_code==422
        assert client.post('/documents',content=b'not PDF',headers={'Content-Type':'text/plain'}).status_code==415
        assert client.get('/documents/rex-omni/blocks/b0000/image').status_code==404
        assert client.get('/documents/rex-omni/blocks/..%2F..%2Fsecrets/image').status_code==404


@pytest.mark.sample
def test_migration_from_m0_preserves_fixture_and_reader_settings(tmp_path):
    import sqlite3
    db=sqlite3.connect(tmp_path/'paperduet.sqlite3')
    db.executescript((Path(__file__).resolve().parents[1]/'paperduet/schema.sql').read_text())
    db.execute("INSERT INTO reader_settings VALUES(1,?)",(json.dumps({'theme':'dark','font_size':19}),));db.commit();db.close()
    store=Store(tmp_path,FIXTURE)
    assert store.settings().theme=='dark' and store.settings().font_size==19
    assert len(store.document('rex-omni').blocks)==285
    with store.connect() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==7


def test_multimodal_request_and_upstream_error_are_secret_safe(tmp_path):
    vault=MemoryVault();vault.set('sk-ant-multimodal-test-secret')
    image=tmp_path/'수식 이미지.png';image.write_bytes(b'test-image-bytes')
    calls=[]
    def handle(request):
        body=json.loads(request.content);calls.append(body)
        assert body['messages'][0]['content'][0]['source']['type']=='base64'
        assert body['messages'][0]['content'][0]['source']['media_type']=='image/png'
        assert vault.get() not in request.content.decode()
        if len(calls)==1:
            return httpx.Response(200,json={'content':[{'type':'text','text':'{"latex":"x=1"}'}],'usage':{'input_tokens':5,'output_tokens':2}})
        return httpx.Response(429,json={'error':vault.get()})
    provider=AnthropicProvider(vault,httpx.MockTransport(handle))
    result,_=asyncio.run(provider.json('equation','model-test',{'source':'x=1'},image))
    assert result=={'latex':'x=1'}
    with pytest.raises(ProviderError,match='^AI_RATE_LIMIT$'):
        asyncio.run(provider.json('equation','model-test',{},image))


@pytest.mark.sample
def test_restore_rejects_swapped_numbers_and_retains_original(tmp_path):
    from paperduet.pipeline import Pipeline
    store=Store(tmp_path,FIXTURE)
    class SwappingProvider:
        def __init__(self):self.calls=0
        async def json(self,*args):
            self.calls+=1
            return {'html':'<table><thead><tr><th>Model</th><th>A</th><th>B</th></tr></thead><tbody><tr><td>X</td><td>2</td><td>1</td></tr></tbody></table>'},{'tokens_in':2,'tokens_out':3}
    provider=SwappingProvider();pipeline=Pipeline(store,provider)
    source=Block(id='test',doc_id='rex-omni',order=0,type='tab',section_path=[],en='Model A B X 1 2',image_path='test.png',
        table=Table(header=[[Cell(text_en='Model'),Cell(text_en='A'),Cell(text_en='B')]],body=[[Cell(text_en='X'),Cell(text_en='1',numeric=True),Cell(text_en='2',numeric=True)]]))
    block=source.model_copy(deep=True)
    asyncio.run(pipeline.restore_block('rex-omni',block,source,PipelineOptions()))
    assert provider.calls==2 and block.table==source.table and block.qa_flags==['EXTRACT_REVIEW']
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM ai_usage').fetchone()[0]==2


def test_user_pause_survives_app_restart_until_explicit_resume(tmp_path):
    _,client,vault,provider=client_app(tmp_path)
    with client:
        doc_id=client.post('/documents',content=make_pdf(40),headers={'Content-Type':'application/pdf'}).json()['doc_id']
        end=time.monotonic()+10
        while time.monotonic()<end:
            job=client.get(f'/documents/{doc_id}/job').json()
            if 1<=job['checkpoint']['extracted_pages']<40:break
            time.sleep(.005)
        assert job['checkpoint']['extracted_pages']<40
        assert client.post(f'/documents/{doc_id}/pause').status_code==204
        wait_job(client,doc_id,{'paused'})
        time.sleep(.1)
        assert client.get(f'/documents/{doc_id}/job').json()['status']=='paused'
    _,again,_,_=client_app(tmp_path,vault,provider)
    with again:
        assert again.get(f'/documents/{doc_id}/job').json()['status']=='paused'
        assert again.post(f'/documents/{doc_id}/resume',json=PipelineOptions().model_dump()).status_code==202
        assert wait_job(again,doc_id,{'awaiting_ai'})['checkpoint']['extracted_pages']==40
