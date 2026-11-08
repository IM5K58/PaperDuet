"""Selective OCR uses source-preserving previews and never starts paid AI."""
import copy
import hashlib
import json
import time

import pymupdf as fitz
import pytest

from paperduet.models import PipelineOptions
from test_reanalysis import api,DOC_ID,BASE,TranslationOnly,rows,saved_state,linked_state


OCR=f'/documents/{DOC_ID}/ocr'


def response(page,text='Recovered OCR sentence with result 42.'):
    return {'page':page,'page_size':{'width':220,'height':330},'language':'eng','dpi':300,'engine':'tesseract',
        'has_native_text':False,'items':[{'type':'p','en':text,'bbox':[20,30,190,65],'page':page}]}


@pytest.fixture
def ocr_api(api,monkeypatch):
    client,store,app,parser,provider=api
    path=store.data_dir/'documents'/DOC_ID/'source.pdf'
    with fitz.open(path) as original:scan=original[0].get_pixmap().tobytes('png')
    with fitz.open() as pdf:
        page=pdf.new_page(width=220,height=330);page.insert_image(page.rect,stream=scan)
        page=pdf.new_page(width=220,height=330);page.draw_rect(fitz.Rect(20,20,190,300),fill=(.8,.8,.8))
        page=pdf.new_page(width=220,height=330);page.insert_text((20,50),'Native text must remain native.')
        data=pdf.tobytes()
    path.write_bytes(data)
    with store.connect() as db:db.execute('UPDATE documents SET page_count=3,file_hash=? WHERE id=?',(hashlib.sha256(data).hexdigest(),DOC_ID))
    answers={0:response(1),1:response(2,'Second scanned page has a result 43.')};calls=[]
    def recognize(path,index,language='eng',dpi=300):
        calls.append((index,language,dpi))
        value=answers[index]
        if isinstance(value,Exception):raise value
        if callable(value):value=value()
        value=copy.deepcopy(value);value['language']=language
        return value
    monkeypatch.setattr('paperduet.ocr_engine.ocr_availability',lambda:{'available':True,'languages':['eng','kor','eng+kor'],'reason':None})
    monkeypatch.setattr('paperduet.ocr_engine.recognize_page',recognize)
    return client,store,app,parser,provider,answers,calls


def create(client,pages=(1,),language='eng'):
    result=client.post(OCR,json={'pages':list(pages),'language':language})
    assert result.status_code==200,result.text
    value=result.json()
    assert value['method']=='ocr' and not value['applied']
    return value


def apply(client,value):
    selected=[change['id'] for change in value['changes'] if change['selectable']]
    result=client.post(f"{BASE}/{value['id']}/apply",json={'change_ids':selected})
    assert result.status_code==200,result.text
    return result.json()


def test_candidates_and_preview_are_read_only_offline_and_do_not_touch_native_cache(ocr_api):
    client,store,_,parser,provider,_,calls=ocr_api
    cache=store.data_dir/'documents'/DOC_ID/'pages-v4'/'0000.json'
    cache.parent.mkdir();cache.write_text('{"keep":"native cache"}',encoding='utf-8')
    original=(store.data_dir/'documents'/DOC_ID/'source.pdf').read_bytes()
    before=saved_state(store)
    candidates=client.get(OCR)
    assert candidates.status_code==200
    info=candidates.json()
    assert info['available'] and info['languages']==['eng','kor','eng+kor'] and info['max_pages']==20
    assert [page['eligible'] for page in info['pages']]==[True,True,False]
    assert all(page['recognized_blocks']==0 for page in info['pages'])
    value=create(client,[2,1])
    assert value['ocr']=={'pages':[1,2],'language':'eng','results':[
        {'page':1,'status':'recognized','blocks':1},{'page':2,'status':'recognized','blocks':1}]}
    additions=[c for c in value['changes'] if c['kind']=='added']
    assert len(additions)==2 and all(c['new_block']['ocr'] for c in additions)
    assert saved_state(store)==before and not provider.calls and not parser.page_calls
    assert cache.read_text(encoding='utf-8')=='{"keep":"native cache"}'
    assert (store.data_dir/'documents'/DOC_ID/'source.pdf').read_bytes()==original
    assert calls==[(0,'eng',300),(1,'eng',300)]


@pytest.mark.parametrize('pages,code',[
    ([1,1],'OCR_SELECTION_INVALID'),([0],'OCR_PAGE_INVALID'),([-1],'OCR_PAGE_INVALID'),
    ([4],'OCR_PAGE_INVALID'),([3],'OCR_PAGE_HAS_TEXT'),([],None),([True],None),(['1'],None),
    (list(range(1,22)),None),
])
def test_invalid_or_native_page_selection_never_calls_ocr(ocr_api,pages,code):
    client,store,_,_,_,_,calls=ocr_api
    before=saved_state(store)
    result=client.post(OCR,json={'pages':pages,'language':'eng'})
    assert result.status_code==422,result.text
    if code:assert result.json()['detail']==code
    assert saved_state(store)==before and not calls


def test_unavailable_engine_and_unavailable_language_are_explicit(ocr_api,monkeypatch):
    client,_,_,_,_,_,calls=ocr_api
    monkeypatch.setattr('paperduet.ocr_engine.ocr_availability',lambda:{'available':False,'languages':[],'reason':'OCR_UNAVAILABLE'})
    assert not client.get(OCR).json()['available']
    assert client.post(OCR,json={'pages':[1]}).json()['detail']=='OCR_UNAVAILABLE'
    monkeypatch.setattr('paperduet.ocr_engine.ocr_availability',lambda:{'available':True,'languages':['eng'],'reason':None})
    result=client.post(OCR,json={'pages':[1],'language':'kor'})
    assert result.status_code==422 and result.json()['detail']=='OCR_LANGUAGE_UNAVAILABLE'
    assert not calls


@pytest.mark.parametrize('first',['failed','empty'])
def test_empty_or_transiently_failed_results_can_be_retried_with_identical_selection(ocr_api,first):
    client,_,_,_,_,answers,calls=ocr_api
    answers[0]=ValueError('OCR_FAILED') if first=='failed' else {**response(1),'items':[]}
    value=create(client)
    assert value['ocr']['results'][0]['status']==first
    assert not any(c['selectable'] for c in value['changes'])
    answers[0]=response(1)
    retried=create(client)
    assert retried['id']!=value['id'] and retried['ocr']['results'][0]['status']=='recognized'
    assert len(calls)==2


def test_successful_cache_is_separate_for_ocr_language_pages_and_normal_reanalysis(ocr_api):
    client,_,_,_,_,_,calls=ocr_api
    native=client.post(BASE);assert native.status_code==200,native.text
    first=create(client);same=create(client)
    assert same['id']==first['id'] and len(calls)==1
    korean=create(client,language='kor')
    other=create(client,[2])
    assert len({first['id'],korean['id'],other['id'],native.json()['id']})==4
    assert client.post(BASE).json()['id']==native.json()['id']
    assert len(calls)==3


def test_page_failure_preserves_previous_ocr_while_other_selected_page_succeeds(ocr_api):
    client,store,_,_,_,answers,_=ocr_api
    first=create(client,[2]);apply(client,first)
    old=next(b for b in store.document(DOC_ID).blocks if b.ocr)
    old.ko='이전에 고친 OCR 번역.';store.save_blocks(DOC_ID,[old])
    answers[1]=ValueError('OCR_FAILED')
    before=saved_state(store)
    value=create(client,[1,2])
    assert [r['status'] for r in value['ocr']['results']]==['recognized','failed']
    prior=next(c for c in value['changes'] if c['old_block'] and c['old_block']['id']==old.id)
    assert prior['kind']=='unchanged' and not prior['selectable']
    assert len([c for c in value['changes'] if c['kind']=='added'])==1
    assert saved_state(store)==before


def test_applied_ocr_keeps_existing_evidence_and_waits_for_explicit_translation_start(ocr_api):
    client,store,app,_,provider,_,_=ocr_api
    before=linked_state(store)
    value=create(client);result=apply(client,value)
    added=next(b for b in store.document(DOC_ID).blocks if b.ocr)
    source=store.sources(DOC_ID)[added.id]
    assert source['origin']=='pdf_ocr' and source['ocr_language']=='eng' and source['ocr_engine']=='tesseract'
    assert source['bbox']==source['region']==[20,30,190,65]
    assert source['ocr_source_hash']==hashlib.sha256((store.data_dir/'documents'/DOC_ID/'source.pdf').read_bytes()).hexdigest()
    assert result['document']['status']=='ready_to_translate' and not added.ko
    assert linked_state(store)==before and not provider.calls and not app.state.pipeline.tasks
    assert store.job(DOC_ID)['checkpoint']['ocr_pages']==[1]
    assert client.get(OCR).json()['pages'][0]['recognized_blocks']==1
    translator=TranslationOnly();app.state.pipeline.provider=translator
    result=client.post(f'/documents/{DOC_ID}/resume',json=PipelineOptions().model_dump())
    assert result.status_code==202
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        job=client.get(f'/documents/{DOC_ID}/job').json()
        if job['status'] not in {'queued','running','batch_waiting'}:break
        time.sleep(.01)
    assert job['status']=='complete',job
    assert [block['id'] for _,payload in translator.calls for block in payload['blocks']]==[added.id]
    assert linked_state(store)==before


def test_repeated_ocr_same_text_does_not_duplicate_approved_blocks_and_changed_text_keeps_old_translation(ocr_api):
    client,store,_,_,_,answers,_=ocr_api
    first=create(client);apply(client,first)
    old=next(b for b in store.document(DOC_ID).blocks if b.ocr)
    old.ko='보존할 기존 OCR 번역.';store.save_blocks(DOC_ID,[old])
    same=create(client)
    assert not any(c['selectable'] for c in same['changes'])
    answers[0]=response(1,'Corrected OCR sentence with result 42.')
    changed=create(client,language='kor')
    replacement=next(c for c in changed['changes'] if c['old_block'] and c['old_block']['id']==old.id)
    assert replacement['kind']=='changed' and replacement['translation_kept']
    apply(client,changed)
    historical=store.block(DOC_ID,old.id)
    assert historical.ko=='보존할 기존 OCR 번역.' and historical.superseded_by
    assert store.block(DOC_ID,historical.superseded_by).ocr


def test_concurrent_edit_during_recognition_rejects_stale_preview_without_overwriting_edit(ocr_api):
    client,store,_,_,_,answers,_=ocr_api
    def edit_during_ocr():
        edited=store.block(DOC_ID,'b0001');edited.ko='OCR 진행 중 새로 고친 번역.';store.save_blocks(DOC_ID,[edited])
        return response(1)
    answers[0]=edit_during_ocr
    result=client.post(OCR,json={'pages':[1],'language':'eng'})
    assert result.status_code==409 and result.json()['detail']=='REANALYSIS_STALE'
    assert store.block(DOC_ID,'b0001').ko=='OCR 진행 중 새로 고친 번역.'
    assert rows(store,'document_reanalyses')==[]


def clear_extraction(store,pure_scan=False):
    if pure_scan:
        path=store.data_dir/'documents'/DOC_ID/'source.pdf'
        with fitz.open(path) as pdf:pdf.delete_page(2);data=pdf.tobytes()
        path.write_bytes(data)
    with store.connect() as db:
        db.execute('DELETE FROM blocks WHERE doc_id=?',(DOC_ID,))
        for table in ['presentation_notes','paper_summaries','annotation_sections','glossary']:
            db.execute(f'DELETE FROM {table} WHERE doc_id=?',(DOC_ID,))
        row=db.execute('SELECT checkpoint FROM jobs WHERE doc_id=?',(DOC_ID,)).fetchone()
        checkpoint=json.loads(row[0]);checkpoint.update(completed_stages=['Ingest'],ai_approved=False)
        db.execute("UPDATE jobs SET stage='Extract',status='failed',checkpoint=? WHERE doc_id=?",(json.dumps(checkpoint),DOC_ID))
        db.execute("UPDATE documents SET status='failed',last_block_id=NULL,last_offset=0 WHERE id=?",(DOC_ID,))


def test_pure_scan_without_old_blocks_can_apply_selected_ocr_and_finish_structure(ocr_api):
    client,store,app,_,provider,_,_=ocr_api
    clear_extraction(store,pure_scan=True)
    value=create(client);apply(client,value)
    assert len(store.document(DOC_ID).blocks)==1 and store.document(DOC_ID).blocks[0].ocr
    job=store.job(DOC_ID)
    assert job['status']=='ready_to_translate' and not job['checkpoint']['ai_approved']
    assert {'Ingest','Extract','Structure'}<=set(job['checkpoint']['completed_stages'])
    assert not app.state.pipeline.tasks and not provider.calls


def test_missing_native_extraction_must_be_retried_before_ocr_can_mark_structure_complete(ocr_api):
    client,store,_,_,_,_,calls=ocr_api
    clear_extraction(store)
    info=client.get(OCR).json()
    assert info['source_extraction_required']
    result=client.post(OCR,json={'pages':[1]})
    assert result.status_code==422 and result.json()['detail']=='OCR_EXTRACTION_REQUIRED'
    assert not store.document(DOC_ID).blocks and not calls


def test_running_job_and_unauthorized_requests_do_not_start_recognition(ocr_api):
    client,store,_,_,_,_,calls=ocr_api
    assert client.get(OCR,headers={'Authorization':''}).status_code==401
    assert client.post(OCR,json={'pages':[1]},headers={'Authorization':''}).status_code==401
    store.update_job(DOC_ID,'Translate','running',.6)
    assert client.get(OCR).json()['busy']
    result=client.post(OCR,json={'pages':[1]})
    assert result.status_code==409 and result.json()['detail']=='JOB_BUSY'
    assert not calls


def test_scanned_appendix_after_native_references_still_requires_translation(ocr_api):
    client,store,_,_,_,answers,_=ocr_api
    path=store.data_dir/'documents'/DOC_ID/'source.pdf'
    scan=(store.data_dir/'documents'/DOC_ID/'crops'/'original-figure.png').read_bytes()
    with fitz.open() as pdf:
        page=pdf.new_page(width=220,height=330)
        page.insert_text((20,35),'References');page.insert_text((20,60),'[1] Native citation. 2026.')
        page=pdf.new_page(width=220,height=330);page.insert_image(page.rect,stream=scan)
        data=pdf.tobytes()
    path.write_bytes(data)
    with store.connect() as db:
        db.execute('UPDATE documents SET page_count=2,file_hash=? WHERE id=?',(hashlib.sha256(data).hexdigest(),DOC_ID))
        for saved in db.execute('SELECT block_id,payload,provenance FROM source_blocks WHERE doc_id=?',(DOC_ID,)).fetchall():
            source=json.loads(saved['payload']);source['section_path']=['References']
            provenance=json.loads(saved['provenance']);provenance['references']=True
            current=json.loads(db.execute('SELECT payload FROM blocks WHERE doc_id=? AND id=?',(DOC_ID,saved['block_id'])).fetchone()[0])
            current['section_path']=['References']
            db.execute('UPDATE blocks SET section_path=?,payload=? WHERE doc_id=? AND id=?',(json.dumps(['References']),json.dumps(current),DOC_ID,saved['block_id']))
            db.execute('UPDATE source_blocks SET payload=?,provenance=? WHERE doc_id=? AND block_id=?',(json.dumps(source),json.dumps(provenance),DOC_ID,saved['block_id']))
    answers[1]=response(2,'APPENDIX: New experiment obtains result 42.')
    value=create(client,[2])
    candidate=next(c for c in value['changes'] if c['kind']=='added')
    assert candidate['requires_translation'] and candidate['new_block']['section_path']==[]
    apply(client,value)
    block=next(b for b in store.document(DOC_ID).blocks if b.ocr)
    assert not store.sources(DOC_ID)[block.id]['references']
    assert store.document(DOC_ID).untranslated_count==1
    assert store.job(DOC_ID)['status']=='ready_to_translate'


def test_normal_reanalysis_can_reuse_verified_ocr_even_when_native_parser_returns_no_blocks(ocr_api,monkeypatch):
    client,store,_,parser,_,_,_=ocr_api
    value=create(client);apply(client,value)
    old=next(b for b in store.document(DOC_ID).blocks if b.ocr)
    monkeypatch.setattr(parser,'structure',lambda *_args:([],[]))
    result=client.post(BASE)
    assert result.status_code==200,result.text
    item=next(c for c in result.json()['changes'] if c['old_block'] and c['old_block']['id']==old.id)
    assert item['kind']=='unchanged' and item['new_block']['ocr']


@pytest.mark.parametrize('change',['merge','split','moved'])
def test_changed_ocr_segmentation_cannot_duplicate_or_replace_previous_paragraphs(ocr_api,change):
    client,store,app,_,provider,answers,_=ocr_api
    first_item={'type':'p','en':'First recognized paragraph has result 11.','bbox':[20,30,190,50],'page':1}
    second_item={'type':'p','en':'Second recognized paragraph has result 22.','bbox':[20,70,190,90],'page':1}
    original=[first_item,second_item] if change=='merge' else [first_item]
    answers[0]={**response(1),'items':original}
    value=create(client);apply(client,value)
    saved=[block for block in store.document(DOC_ID).blocks if block.ocr]
    for index,block in enumerate(saved):block.ko=f'그대로 보존할 기존 OCR 번역 {index}.'
    store.save_blocks(DOC_ID,saved)
    if change=='merge':
        new_items=[{'type':'p','en':first_item['en']+' '+second_item['en'],'bbox':[20,30,190,90],'page':1}]
    elif change=='split':new_items=[first_item,second_item]
    else:new_items=[{**first_item,'bbox':[20,160,190,190]}]
    answers[0]={**response(1),'items':new_items}
    before=saved_state(store)
    repeated=create(client)
    assert repeated['ocr']['results']==[{'page':1,'status':'failed','blocks':0,'error':'OCR_SEGMENTATION_CHANGED'}]
    assert not any(item['selectable'] for item in repeated['changes'])
    assert all(item['kind']=='unchanged' for item in repeated['changes'])
    assert saved_state(store)==before and not provider.calls and not app.state.pipeline.tasks
