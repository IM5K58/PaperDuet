import asyncio
import json
import sqlite3
from pathlib import Path
import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from paperduet.app import create_app
from paperduet.arxiv import arxiv_id,parse_html,ArxivImporter
from paperduet.store import Store
from paperduet.storage import copy_storage
from test_m3 import Vault,ChatDouble,body,events
from m2_sample import seed,FIXTURE

HTML='''<article class="ltx_document"><h1 class="ltx_title ltx_title_document">Synthetic Paper</h1><div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6><p>We compare 42.0 in Table 1.</p></div><section><h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1</span>Method</h2><p>An inline <math alttext="x^2"><mi>x</mi></math> and original text.</p><table class="ltx_equation"><tr class="ltx_eqn_row"><td><math alttext="x=42.0"></math></td><td><span class="ltx_tag_equation">(1)</span></td></tr></table><figure class="ltx_table"><figcaption><span class="ltx_tag_table">Table 1:</span> Results</figcaption><table class="ltx_tabular"><tr><th rowspan="2">Model</th><th colspan="2">Score</th></tr><tr><th>A</th><th>B</th></tr><tr><td>Ours</td><td>42.0</td><td>43.1</td></tr></table></figure><figure class="ltx_figure"><img class="ltx_graphics" src="2603.07952v1/fig.png"><figcaption><span class="ltx_tag_figure">Figure 1:</span> Illustration</figcaption></figure></section><section><h2 class="ltx_title ltx_title_bibliography">References</h2><div class="ltx_bibitem">Reference 1</div></section></article>'''

@pytest.fixture
def api(tmp_path):
    seed(tmp_path);app=create_app('x'*64,tmp_path,FIXTURE,vault=Vault('test-key-secret-value'),provider=ChatDouble())
    with TestClient(app,headers={'Authorization':'Bearer '+'x'*64}) as client:yield client,app.state.store,app

@pytest.mark.sample
def test_export_content_math_spans_images_and_no_private_settings(api):
    client,store,_=api
    doc=store.document('m2-paper');b=doc.blocks[1];b.en+=' </script><script>window.pwned=true</script>';store.save_blocks(doc.id,[b])
    result=client.get('/documents/m2-paper/export?format=html');assert result.status_code==200
    soup=BeautifulSoup(result.text,'html.parser');payload=json.loads(soup.find(id='paperduet-data').string)
    assert len(payload['document']['blocks'])==len(doc.blocks)
    assert payload['images']['b0005'].startswith('data:image/png;base64,')
    assert 'image_path' in payload['document']['blocks'][5]
    assert len(soup.find_all('script'))==2 and not soup.select('script[src],link[href]')
    assert 'test-key-secret-value' not in result.text and 'context_snapshot' not in payload
    assert 'connect-src \'none\'' in result.text
    md=client.get('/documents/rex-omni/export?format=md').text
    assert 'rowspan="' in md and 'colspan="' in md and '$$' in md and 'b0284' in md
    assert client.get('/documents/rex-omni/export?format=html',headers={'Authorization':''}).status_code==401
    assert client.get('/documents/rex-omni/export?format=exe').status_code==422

def test_presentation_curate_edit_reorder_export_persist_and_secret_redaction(api):
    client,store,_=api;base='/documents/m2-paper/presentation'
    answer=events(client.post('/ask',json=body()))[-1]['message_id']
    first=client.post(base,json={'message_id':answer,'title':'AI 요약'}).json()
    assert client.post(base,json={'message_id':answer}).json()['id']==first['id']
    second=client.post(base,json={'block_id':'b0001','title':'근거'}).json()
    assert len(client.get(base).json())==2
    assert client.put(base+'/'+second['id'],json={'title':'수정','body_md':'검토 test-key-secret-value'}).status_code==204
    assert client.put(base+'/order',json={'ids':[second['id'],first['id']]}).status_code==204
    assert client.get(base).json()[0]['title']=='수정'
    assert client.put(base+'/order',json={'ids':[first['id']]*2}).status_code==422
    output=client.get('/documents/m2-paper/export?format=presentation').text
    assert output.index('수정')<output.index('AI 요약') and 'test-key-secret-value' not in output and 'b0001' in output
    reopened=Store(store.data_dir,FIXTURE)
    with reopened.connect() as db:assert db.execute('SELECT COUNT(*) FROM presentation_notes').fetchone()[0]==2
    assert client.delete(base+'/'+first['id']).status_code==204
    assert len(client.get(base).json())==1

@pytest.mark.parametrize('value',['http://arxiv.org/abs/2603.07952','https://evil.test/2603.07952','https://arxiv.org@evil.test/abs/2603.07952','../../etc','https://arxiv.org:8080/abs/2603.07952','2603.07952?url=http://localhost'])
def test_arxiv_rejects_untrusted_locations(value):
    with pytest.raises(ValueError):arxiv_id(value)

def test_arxiv_html_preserves_equations_tables_and_reference_boundaries():
    assert arxiv_id('https://arxiv.org/pdf/2603.07952v1.pdf')=='2603.07952v1'
    assert arxiv_id('hep-th/9901001')=='hep-th/9901001'
    _,blocks,sources,images=parse_html('arxiv-test',HTML)
    table=next(b.table for b in blocks if b.table)
    assert table.header[0][0].rowspan==2 and table.header[0][1].colspan==2
    assert [c.text_en for c in table.body[0]]==['Ours','42.0','43.1']
    assert next(b.latex for b in blocks if b.latex)=='x=42.0'
    assert any(r'\(x^2\)' in (b.en or '') for b in blocks)
    assert sources[-1]['references'] and len(images)==1

def test_arxiv_download_html_first_fallback_and_duplicate(api):
    import pymupdf
    client,store,app=api
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40,50),'Synthetic original paper');pdfbytes=pdf.tobytes()
    calls=[]
    image_path=store.asset_path('m2-paper',store.document('m2-paper').blocks[5].image_path)
    def handler(req):
        calls.append(str(req.url))
        if '/html/' in req.url.path and req.url.path.endswith('.png'):return httpx.Response(200,content=image_path.read_bytes())
        if '/html/' in req.url.path:return httpx.Response(200,text=HTML)
        return httpx.Response(200,content=pdfbytes)
    app.state.arxiv.transport=httpx.MockTransport(handler)
    response=client.post('/documents/arxiv',json={'value':'2603.07952v1'})
    assert response.status_code==202,response.text
    result=response.json();doc=store.document(result['doc_id'])
    assert doc.source_kind=='arxiv_html' and doc.page_count==1 and doc.arxiv_id=='2603.07952v1'
    assert client.get(f'/documents/{doc.id}/pages/1/image').status_code==200
    assert next(b for b in doc.blocks if b.type=='fig').image_path
    assert calls[0].endswith('/html/2603.07952v1') and client.post('/documents/arxiv',json={'value':'2603.07952v1'}).json()['duplicate']
    importer=ArxivImporter(store,app.state.pipeline,transport=httpx.MockTransport(lambda r:httpx.Response(404) if '/html/' in r.url.path else httpx.Response(200,content=pdfbytes)))
    result=asyncio.run(importer.ingest('2603.07953v1'));assert result['fallback']

def test_arxiv_redirect_cannot_leave_fixed_hosts(api):
    _,store,app=api
    importer=ArxivImporter(store,app.state.pipeline,transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/private'})))
    with pytest.raises(ValueError,match='ARXIV_DOWNLOAD_FAILED'):asyncio.run(importer.ingest('2603.07954v1'))

def test_storage_copy_verifies_database_assets_and_retains_original(api,tmp_path):
    client,store,app=api
    destination=tmp_path.parent/(tmp_path.name+' 새 저장 폴더')
    note=client.post('/documents/m2-paper/presentation',json={'title':'보존 메모','body_md':'한글 내용'}).json()
    response=client.post('/settings/storage/migrate',json={'path':str(destination)})
    assert response.status_code==200,response.text
    assert response.json()['verified'] and store.path.is_file()
    copied=Store(destination,FIXTURE)
    with copied.connect() as db:assert db.execute('SELECT body_md FROM presentation_notes WHERE id=?',(note['id'],)).fetchone()[0]=='한글 내용'
    image=next(b for b in copied.document('m2-paper').blocks if b.image_path)
    assert copied.asset_path('m2-paper',image.image_path).read_bytes()==store.asset_path('m2-paper',image.image_path).read_bytes()
    assert client.post('/documents/m2-paper/presentation',json={'title':'막음','body_md':'x'}).status_code==409
    assert client.post('/settings/storage/cancel').status_code==204
    assert client.post('/documents/m2-paper/presentation',json={'title':'허용','body_md':'x'}).status_code==200

def test_storage_rejects_nonempty_overlap_and_active_work(api,tmp_path):
    client,store,app=api
    for path in [tmp_path,tmp_path/'inside',tmp_path.parent]:
        assert client.post('/settings/storage/migrate',json={'path':str(path)}).status_code==422
    app.state.ask.active.add('running')
    assert client.post('/settings/storage/migrate',json={'path':str(tmp_path.parent/'new')}).status_code==409
    app.state.ask.active.clear()
    assert store.path.is_file()

def test_real_visualad_html_if_available():
    path=Path('artifacts/m4/visualad-source.html')
    if not path.exists():pytest.skip('Real arXiv HTML not downloaded')
    _,blocks,_,_=parse_html('visualad-html',path.read_text(encoding='utf-8'))
    assert [b.n for b in blocks if b.type=='fig']==[f'Figure {i}' for i in range(1,7)]
    assert [b.n for b in blocks if b.type=='tab']==[f'Table {i}' for i in range(1,6)]
    assert {b.n for b in blocks if b.type=='eq'}=={f'({i})' for i in range(1,14)}
    assert [b.n for b in blocks if b.type=='sec']==['Abstract','1','2','3','4','5','6','References']
    title=next(b for b in blocks if b.en=='<b>Effect of Different Components.</b>')
    assert title.type=='p' and title.section_path==['4','4.3']
    title=next(b for b in blocks if b.en=='<b>Influence of spatial positional modeling.</b>')
    assert title.type=='p' and title.section_path==['4','4.4']
    assert any(r'\(F_{1}\)' in c.text_en for b in blocks if b.table for row in b.table.body for c in row)


def test_delete_document_removes_everything(api,tmp_path):
    client,store,_=api
    events(client.post('/ask',json=body()))
    assert client.post('/documents/m2-paper/presentation',json={'title':'메모','body_md':'x'}).status_code<300
    folder=tmp_path/'documents'/'m2-paper';assert folder.exists()
    assert client.delete('/documents/m2-paper').status_code==204
    assert client.get('/documents/m2-paper').status_code==404 and not folder.exists()
    assert 'm2-paper' not in [d['id'] for d in client.get('/documents').json()]
    with store.connect() as db:
        for table in ['blocks','source_blocks','glossary','jobs','ai_usage','threads','note_anchors','annotation_sections','presentation_notes']:
            assert not db.execute(f'SELECT 1 FROM {table} WHERE doc_id=?',('m2-paper',)).fetchone(),table
        assert not db.execute('SELECT 1 FROM messages').fetchone()
    assert client.delete('/documents/m2-paper').status_code==404

@pytest.mark.sample
def test_a_deleted_sample_stays_gone(api,tmp_path):
    client,_,_=api
    assert client.delete('/documents/rex-omni').status_code==204
    assert Store(tmp_path,FIXTURE).document('rex-omni') is None  # not re-seeded on the next start

def test_delete_stops_the_papers_running_job(tmp_path):
    from paperduet.pipeline import Pipeline
    store,doc_id=seed(tmp_path);pipeline=Pipeline(store,None)
    async def run():
        async def forever(_doc_id):await asyncio.Event().wait()
        pipeline.run=forever;pipeline.start(doc_id);await asyncio.sleep(0)
        task=pipeline.tasks[doc_id];await pipeline.discard(doc_id)
        return task.cancelled(),doc_id in pipeline.tasks
    assert asyncio.run(run())==(True,False)

def test_arxiv_integer_scores_are_body_cells():
    _,blocks,_,_=parse_html('integers',HTML.replace('42.0','42').replace('43.1','43'))
    table=next(b.table for b in blocks if b.table)
    assert [c.text_en for c in table.body[0]]==['Ours','42','43']
