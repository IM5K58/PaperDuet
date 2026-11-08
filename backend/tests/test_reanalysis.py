"""Existing-paper reanalysis must never discard a reader's saved evidence."""
import hashlib
import json
import time
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.ask import AskInput,AskSettings,build_context
from paperduet.models import Block,Note,PipelineOptions,ReadingPosition
from paperduet.pdf_parser import PyMuPDFParser
from paperduet.pipeline import PARSER_REVISION
from paperduet.store import Store
from paperduet.summary import evidence_blocks


DOC_ID='reanalysis-paper'
BASE=f'/documents/{DOC_ID}/reanalysis'
TOKEN='r'*64


class OfflineVault:
    def get(self):return None
    def set(self,_value):raise AssertionError('Reanalysis must not need an API key')


class NoAI:
    def __init__(self):self.calls=[]
    def connected(self):return False
    async def json(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        raise AssertionError('Reanalysis must not call AI')


class TranslationOnly:
    def __init__(self):self.calls=[]
    def connected(self):return True
    async def json(self,stage,model,payload,image=None):
        self.calls.append((stage,payload))
        assert stage=='translate','Saved annotations/glossary must not be regenerated'
        return {'blocks':[{'id':block['id'],'ko':'복구한 원문의 새 번역: '+block['en']} for block in payload['blocks']]},{'tokens_in':10,'tokens_out':5}


class RevisedParser(PyMuPDFParser):
    """Use real PDF metadata and block structure, with deterministic new regions."""
    def __init__(self):
        super().__init__();self.page_calls=[]

    def page(self,path,page_number,target):
        self.page_calls.append((Path(path),page_number,Path(target)))
        target.mkdir(parents=True,exist_ok=True)
        with fitz.open(path) as pdf:
            pdf[page_number].get_pixmap(clip=fitz.Rect(10,90,190,190)).save(target/'recovered-figure.png')
        return [
            {'type':'sec','n':'1','en':'Introduction','bbox':[20,20,180,40],'page':1},
            {'type':'p','en':'Original paragraph with result 42.','bbox':[20,45,180,70],'page':1},
            {'type':'fig','n':'Figure 1','caption_en':'Original figure caption.','bbox':[10,90,190,200],
             'region':[10,90,190,190],'crop':'recovered-figure.png','page':1},
            {'type':'p','en':'A newly recovered observation about the experiment.','bbox':[20,210,180,240],'page':1},
            {'type':'p','en':'Damaged text restored with the original experiment result 42.','bbox':[20,275,180,300],'page':1},
        ]


def seed_paper(directory):
    store=Store(directory,directory/'absent-fixture.json')
    folder=directory/'documents'/DOC_ID
    (folder/'crops').mkdir(parents=True)
    with fitz.open() as pdf:
        page=pdf.new_page(width=220,height=330)
        page.insert_text((20,30),'Introduction')
        page.draw_rect(fitz.Rect(10,90,190,190),fill=(.1,.4,.8),color=(.1,.4,.8))
        page.insert_text((20,220),'Recovered original text')
        source=pdf.tobytes()
        page.get_pixmap(clip=fitz.Rect(20,100,90,170)).save(folder/'crops'/'original-figure.png')
    (folder/'source.pdf').write_bytes(source)
    (folder/'metadata.json').write_text(json.dumps({'page_count':1,'title':'Saved title','authors':'Saved author'}),encoding='utf-8')
    source_blocks=[
        Block(id='b0000',doc_id=DOC_ID,order=0,type='sec',n='1',section_path=['1'],en='Introduction',ko='서론',page=1),
        Block(id='b0001',doc_id=DOC_ID,order=1,type='p',section_path=['1'],en='Original paragraph with result 42.',ko='기존에 직접 고친 결과 42의 번역.',page=1),
        Block(id='b0002',doc_id=DOC_ID,order=2,type='fig',n='Figure 1',section_path=['1'],caption_en='Original figure caption.',caption_ko='직접 고친 그림 설명.',image_path=f'documents/{DOC_ID}/crops/original-figure.png',page=1),
        Block(id='b0003',doc_id=DOC_ID,order=3,type='p',section_path=['1'],en='Legacy paragraph that must remain available.',ko='없어지면 안 되는 기존 번역.',page=1),
        Block(id='b0004',doc_id=DOC_ID,order=4,type='p',section_path=['1'],en='Dam aged text restored with the original experiment result 42.',ko='손상된 원문에 대한 기존 번역.',page=1),
    ]
    provenance=[
        {'bbox':[20,20,180,40],'region':None,'references':False},
        {'bbox':[20,45,180,70],'region':None,'references':False},
        {'bbox':[20,100,90,170],'region':[20,100,90,170],'references':False},
        {'bbox':[20,230,180,255],'region':None,'references':False},
        {'bbox':[20,275,180,300],'region':None,'references':False},
    ]
    checkpoint={'parser_revision':PARSER_REVISION,'ai_approved':True,'options':PipelineOptions().model_dump(),
        'completed_stages':['Ingest','Extract','Structure','Restore','Glossary','Translate','Validate','Annotate','Render'],
        'translated':['b0000','b0001','b0002','b0003','b0004'],'restored':[],'extracted_pages':1}
    with store.connect() as db:
        db.execute('INSERT INTO documents(id,title,title_ko,status,page_count,authors,file_hash,source_path) VALUES(?,?,?,?,?,?,?,?)',
            (DOC_ID,'Saved title','기존 제목','complete',1,'Saved author',hashlib.sha256(source).hexdigest(),f'documents/{DOC_ID}/source.pdf'))
        db.execute('INSERT INTO jobs(id,doc_id,stage,status,progress,checkpoint) VALUES(?,?,?,?,?,?)',
            (DOC_ID,DOC_ID,'Render','complete',1,json.dumps(checkpoint)))
        db.execute('INSERT INTO glossary VALUES(?,?,?,?,?)',(DOC_ID,'result','결과',0,'직접 작성한 용어 설명'))
    store.save_blocks(DOC_ID,source_blocks,provenance)
    note=Block(id='saved-note',doc_id=DOC_ID,order=5,type='note',section_path=['1'],
        note=Note(kind='ins',title='기존 주석',body_md='원문을 읽으며 남긴 메모.',claim='interpretation',refs=['b0001','b0004'],origin='user'))
    store.save_annotations(DOC_ID,'["1"]',[('b0001',note)],[])
    with store.connect() as db:
        anchor=json.dumps({'block_id':'b0004','field':'en','start':0,'end':3,'text':'Dam'})
        snapshot=json.dumps({'context':{'block':source_blocks[-1].model_dump(exclude_none=True),'selection':json.loads(anchor)}})
        db.execute('INSERT INTO threads(id,doc_id,block_id,anchor,title) VALUES(?,?,?,?,?)',('saved-thread',DOC_ID,'b0004',anchor,'기존 질문'))
        db.execute('INSERT INTO messages(id,thread_id,role,content_md,provider,mode,model,context_snapshot) VALUES(?,?,?,?,?,?,?,?)',
            ('saved-message','saved-thread','assistant','기존 답변','anthropic','api_key','offline-test',snapshot))
        db.execute('INSERT INTO presentation_notes(id,doc_id,block_id,message_id,title,body_md,sort_order) VALUES(?,?,?,?,?,?,?)',
            ('saved-memo',DOC_ID,'b0004','saved-message','기존 메모','기존 답변에서 남긴 메모.',0))
    store.save_summary(DOC_ID,status='ready',progress=1,payload={'structured':{'problem':[{'text':'기존 요약','refs':['b0004']}]}},edited=True)
    store.position(DOC_ID,ReadingPosition(block_id='b0003',offset=37))
    return store


@pytest.fixture
def api(tmp_path):
    seed_paper(tmp_path)
    parser=RevisedParser();provider=NoAI()
    app=create_app(TOKEN,tmp_path,tmp_path/'absent-fixture.json',vault=OfflineVault(),provider=provider,parser=parser)
    with TestClient(app,headers={'Authorization':'Bearer '+TOKEN}) as client:
        yield client,app.state.store,app,parser,provider


def rows(store,table):
    with store.connect() as db:
        return [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]


def saved_state(store):
    return {table:rows(store,table) for table in ['documents','blocks','source_blocks','jobs','glossary','threads','messages',
        'note_anchors','annotation_sections','paper_summaries','presentation_notes','ai_usage','ai_results']}


def linked_state(store):
    result={table:rows(store,table) for table in ['glossary','threads','messages','note_anchors','annotation_sections',
        'paper_summaries','presentation_notes']}
    note=store.block(DOC_ID,'saved-note').model_dump(exclude_none=True);note.pop('order',None)
    result['user_note']=note
    result['reading_position']=store.document(DOC_ID).reading_position.model_dump()
    return result


def preview(client):
    response=client.post(BASE)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['doc_id']==DOC_ID and value['method']=='pdf' and not value['applied']
    return value


def changed(preview_value,block_id):
    return next(c for c in preview_value['changes'] if c['kind']=='changed' and c['old_block']['id']==block_id)


def apply_changes(client,value,changes):
    return client.post(f"{BASE}/{value['id']}/apply",json={'change_ids':[c['id'] for c in changes]})


def test_preview_is_offline_and_leaves_saved_document_and_assets_untouched(api):
    client,store,_,parser,provider=api
    before=saved_state(store)
    folder=store.data_dir/'documents'/DOC_ID
    pdf=(folder/'source.pdf').read_bytes();crop=(folder/'crops'/'original-figure.png').read_bytes()
    value=preview(client)
    assert saved_state(store)==before
    assert (folder/'source.pdf').read_bytes()==pdf and (folder/'crops'/'original-figure.png').read_bytes()==crop
    assert len(parser.page_calls)==1 and not provider.calls
    assert any(c['kind']=='unchanged' and c['old_block']['id']=='b0001' for c in value['changes'])
    missing=next(c for c in value['changes'] if c['kind']=='missing' and c['old_block']['id']=='b0003')
    assert not missing['selectable']
    assert str(store.data_dir) not in json.dumps(value)
    fetched=client.get(f"{BASE}/{value['id']}")
    assert fetched.status_code==200 and fetched.json()==value
    assert saved_state(store)==before  # Closing a preview without applying has no edits to undo.


def test_preview_image_comparison_serves_old_and_staged_crops(api):
    client,store,_,_,_=api
    value=preview(client);change=changed(value,'b0002')
    urls=[f"{BASE}/{value['id']}/changes/{change['id']}/image?side={side}" for side in ['before','after']]
    images=[client.get(url) for url in urls]
    assert all(response.status_code==200 for response in images)
    assert all(response.json()['data_url'].startswith('data:image/png;base64,') for response in images)
    assert images[0].json()!=images[1].json()
    assert store.block(DOC_ID,'b0002').image_path.endswith('original-figure.png')


def test_selecting_only_figure_improvement_keeps_translation_ids_and_linked_evidence(api):
    client,store,_,_,provider=api
    before=linked_state(store)
    original={b.id:b.model_dump(exclude_none=True) for b in store.document(DOC_ID).blocks}
    folder=store.data_dir/'documents'/DOC_ID
    old_crop=(folder/'crops'/'original-figure.png').read_bytes();pdf=(folder/'source.pdf').read_bytes()
    value=preview(client);change=changed(value,'b0002')
    assert change['selectable'] and change['translation_kept'] and not change['requires_translation']
    response=apply_changes(client,value,[change])
    assert response.status_code==200,response.text
    assert response.json()['applied']==1
    doc=store.document(DOC_ID)
    assert {b.id for b in doc.blocks}==set(original)
    assert linked_state(store)==before and not provider.calls
    figure=store.block(DOC_ID,'b0002')
    assert figure.caption_en==original['b0002']['caption_en'] and figure.caption_ko==original['b0002']['caption_ko']
    assert figure.image_path!=original['b0002']['image_path'] and store.asset_path(DOC_ID,figure.image_path)
    assert (folder/'crops'/'original-figure.png').read_bytes()==old_crop and (folder/'source.pdf').read_bytes()==pdf
    assert store.block(DOC_ID,'b0004').model_dump(exclude_none=True)==original['b0004']
    assert client.get(f"{BASE}/{value['id']}").json()['applied']


def test_changed_source_gets_new_id_while_old_translation_and_references_remain(api):
    client,store,_,_,provider=api
    before=linked_state(store)
    old=store.block(DOC_ID,'b0004').model_dump(exclude_none=True)
    old_source=store.sources(DOC_ID)['b0004']['block'].model_dump(exclude_none=True)
    original_ids={b.id for b in store.document(DOC_ID).blocks}
    old_figure=store.block(DOC_ID,'b0002').image_path
    value=preview(client);change=changed(value,'b0004')
    assert change['selectable'] and change['requires_translation'] and change['translation_kept']
    response=apply_changes(client,value,[change])
    assert response.status_code==200,response.text
    saved=store.block(DOC_ID,'b0004')
    for field in ['en','ko','type','section_path']:
        assert getattr(saved,field)==old[field]
    replacement_id=saved.superseded_by
    assert replacement_id and replacement_id not in original_ids
    replacement=store.block(DOC_ID,replacement_id)
    assert replacement.en=='Damaged text restored with the original experiment result 42.' and not replacement.ko
    assert store.sources(DOC_ID)['b0004']['block'].en==old_source['en']
    assert store.sources(DOC_ID)['b0004']['block'].ko==old_source['ko']
    assert store.sources(DOC_ID)[replacement_id]['block'].en==replacement.en
    assert linked_state(store)==before and not provider.calls
    assert store.block(DOC_ID,'b0002').image_path==old_figure  # An unselected improvement is not applied.
    assert store.block(DOC_ID,'b0003').ko=='없어지면 안 되는 기존 번역.'
    assert store.document(DOC_ID).untranslated_count==1  # The old Korean text cannot complete the new source.


def test_newly_found_block_never_reuses_a_missing_legacy_id(api):
    client,store,_,_,_=api
    before=linked_state(store)
    original_ids={b.id for b in store.document(DOC_ID).blocks}
    value=preview(client)
    addition=next(c for c in value['changes'] if c['kind']=='added' and c['new_block'].get('en')=='A newly recovered observation about the experiment.')
    response=apply_changes(client,value,[addition])
    assert response.status_code==200,response.text
    added=[b for b in store.document(DOC_ID).blocks if b.id not in original_ids]
    assert len(added)==1 and added[0].id!='b0003' and not added[0].ko
    assert store.block(DOC_ID,'b0003').en=='Legacy paragraph that must remain available.'
    assert linked_state(store)==before


@pytest.mark.parametrize('selection',['empty','unknown','duplicate','missing','unchanged'])
def test_invalid_or_nonselectable_changes_do_not_mutate_document(api,selection):
    client,store,_,_,_=api
    value=preview(client)
    selectable=next(c['id'] for c in value['changes'] if c['selectable'])
    ids={'empty':[],'unknown':['unknown-change'],'duplicate':[selectable,selectable],
        'missing':[next(c['id'] for c in value['changes'] if c['kind']=='missing')],
        'unchanged':[next(c['id'] for c in value['changes'] if c['kind']=='unchanged')]}
    before=saved_state(store)
    response=client.post(f"{BASE}/{value['id']}/apply",json={'change_ids':ids[selection]})
    assert response.status_code==422,response.text
    assert saved_state(store)==before


def test_concurrent_translation_edit_rejects_stale_preview_without_overwriting_edit(api):
    client,store,_,_,_=api
    value=preview(client);change=changed(value,'b0002')
    edited=client.patch(f'/documents/{DOC_ID}/blocks/b0001',json={'ko':'미리보기 뒤 새로 고친 번역.'})
    assert edited.status_code==200
    before=saved_state(store)
    response=apply_changes(client,value,[change])
    assert response.status_code==409 and response.json()['detail']=='REANALYSIS_STALE'
    assert saved_state(store)==before and store.block(DOC_ID,'b0001').ko=='미리보기 뒤 새로 고친 번역.'


def test_changed_source_pdf_rejects_stale_preview(api):
    client,store,_,_,_=api
    value=preview(client);change=changed(value,'b0002')
    path=store.data_dir/'documents'/DOC_ID/'source.pdf'
    path.write_bytes(path.read_bytes()+b'\n% Source changed after preview\n')
    before=saved_state(store)
    response=apply_changes(client,value,[change])
    assert response.status_code==409 and response.json()['detail']=='REANALYSIS_STALE'
    assert saved_state(store)==before and path.read_bytes().endswith(b'% Source changed after preview\n')


@pytest.mark.parametrize('during',['preview','apply'])
def test_processing_job_cannot_be_reanalyzed_or_applied_concurrently(api,during):
    client,store,_,parser,_=api
    value=preview(client) if during=='apply' else None
    store.update_job(DOC_ID,'Translate','running',.6)
    before=saved_state(store);calls=len(parser.page_calls)
    response=apply_changes(client,value,[changed(value,'b0002')]) if value else client.post(BASE)
    assert response.status_code==409 and response.json()['detail']=='JOB_BUSY'
    assert saved_state(store)==before and len(parser.page_calls)==calls


def test_reanalysis_routes_require_document_and_session_authorization(api):
    client,store,_,parser,_=api
    before=saved_state(store)
    assert client.post('/documents/missing/reanalysis').status_code==404
    assert client.post(BASE,headers={'Authorization':''}).status_code==401
    assert saved_state(store)==before and not parser.page_calls


def test_successful_apply_is_idempotent_and_cannot_apply_a_different_selection(api):
    client,store,_,_,_=api
    value=preview(client);change=changed(value,'b0002')
    first=apply_changes(client,value,[change])
    assert first.status_code==200,first.text
    saved=saved_state(store)
    second=apply_changes(client,value,[change])
    assert second.status_code==200 and second.json()['applied']==first.json()['applied']==1
    assert saved_state(store)==saved
    different=changed(value,'b0004')
    rejected=apply_changes(client,value,[different])
    assert rejected.status_code==409 and rejected.json()['detail']=='REANALYSIS_STALE'
    assert saved_state(store)==saved


def test_saved_arxiv_html_is_reanalyzed_locally_without_network_or_ai(api,monkeypatch):
    client,store,_,parser,provider=api
    html_path=store.data_dir/'documents'/DOC_ID/'source.html'
    html_path.write_text('''<article class="ltx_document"><h1 class="ltx_title ltx_title_document">Saved title</h1><section><h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1</span>Introduction</h2><p>Original paragraph with result 42.</p><p>A locally recovered HTML paragraph.</p></section></article>''',encoding='utf-8')
    with store.connect() as db:db.execute("UPDATE documents SET source_kind='arxiv_html',source_url='https://arxiv.org/abs/2603.07952v1' WHERE id=?",(DOC_ID,))
    def forbidden_network(*_args,**_kwargs):raise AssertionError('Saved-source reanalysis must stay offline')
    monkeypatch.setattr('httpx.AsyncClient',forbidden_network)
    before=saved_state(store)
    response=client.post(BASE)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['method']=='arxiv_html'
    assert any(c['kind']=='added' and c['new_block'].get('en')=='A locally recovered HTML paragraph.' for c in value['changes'])
    assert saved_state(store)==before and not parser.page_calls and not provider.calls


def test_failed_apply_rolls_back_blocks_references_and_apply_marker_and_can_retry(api):
    client,store,app,_,_=api
    value=preview(client);change=changed(value,'b0004')
    before=saved_state(store);records=rows(store,'document_reanalyses')
    with store.connect() as db:
        db.execute('''CREATE TRIGGER fail_reanalysis_source_insert BEFORE INSERT ON source_blocks
            WHEN NEW.doc_id='reanalysis-paper' AND NEW.block_id NOT IN ('b0000','b0001','b0002','b0003','b0004')
            BEGIN SELECT RAISE(ABORT,'Injected reanalysis write failure'); END''')
    failure_client=TestClient(app,headers={'Authorization':'Bearer '+TOKEN},raise_server_exceptions=False)
    try:
        response=apply_changes(failure_client,value,[change])
        assert response.status_code==422 and response.json()['detail']=='REANALYSIS_FAILED',response.text
        assert saved_state(store)==before
        assert rows(store,'document_reanalyses')==records
    finally:
        failure_client.close()
        with store.connect() as db:db.execute('DROP TRIGGER fail_reanalysis_source_insert')
    retried=apply_changes(client,value,[change])
    assert retried.status_code==200,retried.text
    assert store.block(DOC_ID,'b0004').superseded_by


@pytest.mark.parametrize('damage',['deleted','modified'])
def test_staged_image_changed_after_preview_cannot_replace_existing_crop(api,damage):
    client,store,_,_,_=api
    value=preview(client);change=changed(value,'b0002')
    staged=store.asset_path(DOC_ID,change['new_block']['image_path'])
    assert staged
    if damage=='deleted':staged.unlink()
    else:staged.write_bytes(staged.read_bytes()+b'changed after preview')
    before=saved_state(store)
    response=apply_changes(client,value,[change])
    assert response.status_code==409 and response.json()['detail']=='REANALYSIS_STALE'
    assert saved_state(store)==before
    assert store.block(DOC_ID,'b0002').image_path.endswith('original-figure.png')


def test_new_extraction_without_picture_cannot_remove_an_existing_figure_image(api,monkeypatch):
    client,store,_,parser,_=api
    original_page=parser.page
    def no_picture(*args,**kwargs):
        result=original_page(*args,**kwargs)
        next(item for item in result if item['type']=='fig').pop('crop')
        return result
    monkeypatch.setattr(parser,'page',no_picture)
    original=store.block(DOC_ID,'b0002').image_path
    value=preview(client)
    change=next(c for c in value['changes'] if c['old_block'] and c['old_block']['id']=='b0002')
    if change['selectable']:
        response=apply_changes(client,value,[change])
        assert response.status_code==200,response.text
    figure=store.block(DOC_ID,'b0002')
    assert figure.image_path==original and store.asset_path(DOC_ID,figure.image_path)
    assert figure.caption_ko=='직접 고친 그림 설명.'


def test_multiple_selected_changes_preserve_linked_data_and_do_not_reappear_on_next_preview(api):
    client,store,_,_,_=api
    before=linked_state(store)
    originals={b.id for b in store.document(DOC_ID).blocks}
    value=preview(client)
    selected=[change for change in value['changes'] if change['selectable']]
    response=apply_changes(client,value,selected)
    assert response.status_code==200,response.text
    assert response.json()['applied']==len(selected)
    doc=store.document(DOC_ID)
    assert originals<={b.id for b in doc.blocks}
    assert len(doc.blocks)==len({b.id for b in doc.blocks})
    assert linked_state(store)==before
    assert [b.order for b in doc.blocks]==list(range(len(doc.blocks)))
    assert store.block(DOC_ID,'b0004').superseded_by
    next_preview=preview(client)
    assert not any(c['selectable'] for c in next_preview['changes'])
    assert next_preview['counts']['missing']==1  # The absent legacy paragraph remains saved.


def test_reading_position_can_change_while_reviewing_without_invalidating_preview(api):
    client,store,_,_,_=api
    value=preview(client);change=changed(value,'b0002')
    store.position(DOC_ID,ReadingPosition(block_id='b0001',offset=91))
    response=apply_changes(client,value,[change])
    assert response.status_code==200,response.text
    assert store.document(DOC_ID).reading_position==ReadingPosition(block_id='b0001',offset=91)


def test_manual_selection_is_not_replaced_by_a_later_automatic_parser_upgrade(api,monkeypatch):
    import asyncio
    import paperduet.pipeline as module
    client,store,app,_,_=api
    with store.connect() as db:
        for table in ['presentation_notes','threads','note_anchors','annotation_sections','paper_summaries']:
            db.execute(f'DELETE FROM {table} WHERE doc_id=?',(DOC_ID,))
        db.execute("DELETE FROM blocks WHERE doc_id=? AND type='note'",(DOC_ID,))
        for row in db.execute('SELECT id,payload FROM blocks WHERE doc_id=?',(DOC_ID,)).fetchall():
            block=json.loads(row['payload']);block.pop('ko',None);block.pop('caption_ko',None)
            db.execute('UPDATE blocks SET payload=? WHERE doc_id=? AND id=?',(json.dumps(block),DOC_ID,row['id']))
        row=db.execute('SELECT checkpoint FROM jobs WHERE doc_id=?',(DOC_ID,)).fetchone()
        checkpoint=json.loads(row[0]);checkpoint.update(ai_approved=False,parser_revision=1)
        db.execute('UPDATE jobs SET checkpoint=? WHERE doc_id=?',(json.dumps(checkpoint),DOC_ID))
    value=preview(client)
    response=apply_changes(client,value,[changed(value,'b0002')])
    assert response.status_code==200,response.text
    assert store.job(DOC_ID)['checkpoint']['reanalysis_id']==value['id']
    expected=store.block(DOC_ID,'b0002').image_path
    started=[]
    monkeypatch.setattr(app.state.pipeline,'start',started.append)
    monkeypatch.setattr(module,'PARSER_REVISION',PARSER_REVISION+1)
    asyncio.run(app.state.pipeline.recover())
    assert DOC_ID not in started
    assert store.block(DOC_ID,'b0002').image_path==expected


def test_resuming_after_reanalysis_translates_only_new_source_and_preserves_all_saved_notes(api):
    client,store,app,_,_=api
    generated=Block(id='saved-generated-note',doc_id=DOC_ID,order=6,type='note',section_path=['1'],
        note=Note(kind='res',title='기존 자동 주석',body_md='원문에서 확인한 기존 결과 42.',claim='stated',refs=['b0004'],origin='generated'))
    store.save_annotations(DOC_ID,'["1"]',[('b0004',generated)],[])
    # A missing completion marker must not cause old generated notes to be
    # replaced when the user only approved recovered-source translation.
    with store.connect() as db:db.execute('DELETE FROM annotation_sections WHERE doc_id=?',(DOC_ID,))
    before=linked_state(store)
    generated_before=store.block(DOC_ID,generated.id).model_dump(exclude_none=True);generated_before.pop('order')
    old=store.block(DOC_ID,'b0004')
    source_before=store.sources(DOC_ID)['b0004']['block'].model_dump(exclude_none=True)
    value=preview(client)
    applied=apply_changes(client,value,[changed(value,'b0004')])
    assert applied.status_code==200,applied.text
    replacement_id=store.block(DOC_ID,'b0004').superseded_by
    assert replacement_id and not store.block(DOC_ID,replacement_id).ko
    translator=TranslationOnly();app.state.pipeline.provider=translator
    resumed=client.post(f'/documents/{DOC_ID}/resume',json=PipelineOptions().model_dump())
    assert resumed.status_code==202,resumed.text
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        job=client.get(f'/documents/{DOC_ID}/job').json()
        if job['status'] not in {'queued','running','batch_waiting'}:break
        time.sleep(.01)
    assert job['status']=='complete',job
    assert [stage for stage,_ in translator.calls]==['translate']
    assert [block['id'] for _,payload in translator.calls for block in payload['blocks']]==[replacement_id]
    assert store.block(DOC_ID,replacement_id).ko.startswith('복구한 원문의 새 번역: ')
    assert store.block(DOC_ID,'b0004').en==old.en and store.block(DOC_ID,'b0004').ko==old.ko
    assert store.sources(DOC_ID)['b0004']['block'].model_dump(exclude_none=True)==source_before
    assert linked_state(store)==before
    generated_after=store.block(DOC_ID,generated.id).model_dump(exclude_none=True);generated_after.pop('order')
    assert generated_after==generated_before
    assert store.document(DOC_ID).untranslated_count==0


def test_ask_uses_active_paper_but_still_allows_questions_about_old_source(api):
    client,store,_,_,_=api
    value=preview(client)
    assert apply_changes(client,value,[changed(value,'b0004')]).status_code==200
    old=store.block(DOC_ID,'b0004');replacement_id=old.superseded_by
    settings=AskSettings(neighbors=8)
    whole,_=build_context(store,AskInput(doc_id=DOC_ID,anchor={'block_id':old.id,'field':'document'},question='논문 전체를 설명해 줘'),settings)
    assert whole['block'] is None
    assert old.id not in {b['id'] for b in whole['full_paper']}
    assert replacement_id in {b['id'] for b in whole['full_paper']}
    current,_=build_context(store,AskInput(doc_id=DOC_ID,anchor={'block_id':replacement_id},question='복구한 문장을 설명해 줘',full_context=True),settings)
    assert old.id not in {b['id'] for b in current['neighbors']}
    assert old.id not in {b['id'] for b in current['full_paper']}
    historical,_=build_context(store,AskInput(doc_id=DOC_ID,anchor={'block_id':old.id,'field':'en','start':0,'end':3,'text':'Dam'},question='기존 번역을 설명해 줘',full_context=True),settings)
    assert historical['block']['id']==old.id and historical['block']['en']==old.en and historical['block']['ko']==old.ko
    assert old.id not in {b['id'] for b in historical['full_paper']}
    assert replacement_id in {b['id'] for b in historical['full_paper']}


def test_new_summary_evidence_excludes_history_and_markdown_labels_saved_history(api):
    client,store,_,_,_=api
    summary_before=rows(store,'paper_summaries')
    value=preview(client)
    assert apply_changes(client,value,[changed(value,'b0004')]).status_code==200
    old=store.block(DOC_ID,'b0004');replacement=store.block(DOC_ID,old.superseded_by)
    evidence=evidence_blocks(store.document(DOC_ID),store.sources(DOC_ID))
    assert old.id not in evidence and replacement.id in evidence
    assert rows(store,'paper_summaries')==summary_before  # Existing citations still describe the old source.
    exported=client.get(f'/documents/{DOC_ID}/export?format=md')
    assert exported.status_code==200,exported.text
    assert '**이전 원문 · 기존 번역**' in exported.text and '새 원문으로 이동' in exported.text
    assert old.en in exported.text and old.ko in exported.text and replacement.en in exported.text
    assert f'<a id="{old.id}"></a>' in exported.text and f'<a id="{replacement.id}"></a>' in exported.text
