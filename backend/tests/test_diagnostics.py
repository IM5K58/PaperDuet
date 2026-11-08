"""Observed processing states only: diagnostics must not invent missing content."""
import hashlib
import json

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from paperduet.adapter import parse_table
from paperduet.app import create_app
from paperduet.arxiv import ArxivImporter
from paperduet.models import Block,PipelineOptions
from test_arxiv_fidelity import import_paper,figure,IDENTIFIER
from test_reanalysis import api,DOC_ID,BASE,TOKEN,NoAI,OfflineVault,preview,changed,apply_changes,rows,saved_state


ENDPOINT=f'/documents/{DOC_ID}/diagnostics'


def report(client):
    response=client.get(ENDPOINT)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['doc_id']==DOC_ID and value['generated_at']
    assert value['summary']['total']==len(value['issues'])
    assert sum(value['summary']['by_kind'].values())==len(value['issues'])
    assert len({issue['id'] for issue in value['issues']})==len(value['issues'])
    for issue in value['issues']:
        assert issue['severity'] in {'attention','info'}
        assert issue['title'] and issue['detail'] and isinstance(issue['source_available'],bool)
        assert 'block_id' in issue and 'page' in issue
    return value


def add_block(store,identifier,kind='p',references=False,**fields):
    order=max(b.order for b in store.document(DOC_ID).blocks)+1
    block=Block(id=identifier,doc_id=DOC_ID,order=order,type=kind,section_path=['References'] if references else ['1'],page=1,**fields)
    store.save_blocks(DOC_ID,[block],[{'bbox':[20,100,190,170],'region':None,'references':references}])
    return block


def kinds_for(value,block_id):
    return {issue['kind'] for issue in value['issues'] if issue['block_id']==block_id}


def test_complete_saved_paper_has_no_invented_quality_warnings_and_get_is_read_only(api,monkeypatch):
    client,store,_,parser,provider=api
    before=saved_state(store);previews=rows(store,'document_reanalyses')
    folder=store.data_dir/'documents'/DOC_ID
    files={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in [folder/'source.pdf',folder/'crops'/'original-figure.png']}
    def no_network(*_args,**_kwargs):raise AssertionError('Diagnostics must not use a provider or network')
    monkeypatch.setattr('httpx.AsyncClient',no_network)
    value=report(client)
    assert not value['busy'] and value['issues']==[]
    assert value['summary']['blocks']=={'paragraphs':3,'figures':1,'tables':0,'equations':0}
    assert value['summary']['pages']=={'total':1,'with_text':1,'without_text':[]}
    assert saved_state(store)==before and rows(store,'document_reanalyses')==previews
    assert not provider.calls and not parser.page_calls
    assert all(hashlib.sha256(path.read_bytes()).hexdigest()==digest for path,digest in files.items())


def test_expected_image_path_missing_is_observed_but_caption_only_is_not_a_missing_image(api):
    client,store,_,_,_=api
    add_block(store,'missing-figure','fig',caption_en='A figure whose crop was expected.',caption_ko='기존 그림 설명.',image_path=f'documents/{DOC_ID}/crops/not-present.png')
    add_block(store,'caption-only','fig',caption_en='Caption supplied without an illustration.',caption_ko='캡션만 있는 원문.')
    add_block(store,'caption-only-table','tab',caption_en='Caption-only source table.',caption_ko='캡션만 있는 표.')
    value=report(client)
    missing=[issue for issue in value['issues'] if issue['kind']=='missing_image']
    assert {issue['block_id'] for issue in missing}=={'missing-figure'}
    assert missing[0]['severity']=='attention' and missing[0]['source_available']
    assert 'missing_image' not in kinds_for(value,'caption-only-table')


@pytest.mark.parametrize('failure',['invalid','numeric_mismatch'])
def test_explicit_table_restoration_failure_is_not_reported_as_pending(api,failure):
    client,store,_,_,_=api
    add_block(store,'failed-table','tab',caption_en='Observed table failure.',caption_ko='표 복원 실패.',
        table=parse_table('<table><thead><tr><th>Result</th></tr></thead><tbody><tr><td>42</td></tr></tbody></table>'),
        image_path=f'documents/{DOC_ID}/crops/original-figure.png',restore_error=failure)
    value=report(client)
    assert 'restore_failed' in kinds_for(value,'failed-table')
    assert 'restore_pending' not in kinds_for(value,'failed-table')
    issue=next(issue for issue in value['issues'] if issue['kind']=='restore_failed' and issue['block_id']=='failed-table')
    assert issue['severity']=='attention' and issue['source_available'] and issue['page']==1


def test_equation_image_without_latex_is_pending_but_valid_equation_is_not(api):
    client,store,_,_,_=api
    add_block(store,'pending-equation','eq',en='x = 42',image_path=f'documents/{DOC_ID}/crops/original-figure.png')
    add_block(store,'valid-equation','eq',en='x = 42',latex='x=42')
    value=report(client)
    assert 'restore_pending' in kinds_for(value,'pending-equation')
    assert not ({'restore_failed','restore_pending','missing_image'} & kinds_for(value,'valid-equation'))


def test_failed_equation_is_not_misclassified_as_still_waiting_for_restoration(api):
    client,store,_,_,_=api
    add_block(store,'failed-equation','eq',en='x = 42',restore_error='invalid',image_path=f'documents/{DOC_ID}/crops/original-figure.png')
    value=report(client)
    assert 'restore_failed' in kinds_for(value,'failed-equation')
    assert 'restore_pending' not in kinds_for(value,'failed-equation')


def test_image_table_waits_for_structure_without_claiming_its_image_is_missing(api):
    client,store,_,_,_=api
    add_block(store,'pending-table','tab',caption_en='Original image-based table.',caption_ko='이미지로 주어진 표.',image_path=f'documents/{DOC_ID}/crops/original-figure.png')
    value=report(client)
    assert 'restore_pending' in kinds_for(value,'pending-table')
    assert 'missing_image' not in kinds_for(value,'pending-table')


def test_untranslated_is_limited_to_active_nonreference_source(api):
    client,store,_,_,_=api
    block=store.block(DOC_ID,'b0001');block.ko=None;store.save_blocks(DOC_ID,[block])
    add_block(store,'reference',en='Reference 2026.',references=True)
    add_block(store,'active-replacement',en='Current source sentence.',ko='현재 원문의 번역.')
    add_block(store,'historical-source',en='Older source sentence.',superseded_by='active-replacement',translation_error='source_mismatch')
    value=report(client)
    untranslated=[issue for issue in value['issues'] if issue['kind']=='untranslated']
    assert {issue['block_id'] for issue in untranslated}=={'b0001'}
    assert 'untranslated' not in kinds_for(value,'saved-note')
    assert not kinds_for(value,'historical-source')
    assert value['summary']['blocks']['paragraphs']==5  # References count toward source volume, history does not.


@pytest.mark.parametrize('bad_response,reason',[
    ({'blocks':[{'id':'b0001','ko':'수치가 바뀐 결과 999.'}]},'source_mismatch'),
    ({'malformed':'no translated blocks'},'invalid_response'),
])
def test_only_explicit_translation_rejection_is_reported_and_success_clears_it(api,bad_response,reason):
    client,store,app,_,_=api
    block=store.block(DOC_ID,'b0001');block.ko=None
    sources=store.sources(DOC_ID)
    unit=app.state.pipeline.translate_unit(DOC_ID,[block],[],sources,PipelineOptions(),'diagnostic-rejection')
    next(unit);unit.send(bad_response)
    with pytest.raises(StopIteration):unit.send(bad_response)
    assert block.translation_error==reason
    store.save_blocks(DOC_ID,[block])
    value=report(client)
    assert 'translation_rejected' in kinds_for(value,block.id)
    successful=app.state.pipeline.translate_unit(DOC_ID,[block],[],sources,PipelineOptions(),'diagnostic-success')
    next(successful)
    with pytest.raises(StopIteration):successful.send({'blocks':[{'id':block.id,'ko':'원문 결과 42를 보존한 번역.'}]})
    assert block.translation_error is None
    store.save_blocks(DOC_ID,[block])
    assert not ({'translation_rejected','untranslated'} & kinds_for(report(client),block.id))


def test_image_only_pdf_page_is_information_without_claiming_content_was_lost(api):
    client,store,_,_,_=api
    folder=store.data_dir/'documents'/DOC_ID
    with fitz.open(folder/'source.pdf') as pdf:
        page=pdf.new_page(width=220,height=330)
        page.insert_image(fitz.Rect(20,20,200,200),filename=str(folder/'crops'/'original-figure.png'))
        content=pdf.tobytes()
    (folder/'source.pdf').write_bytes(content)
    with store.connect() as db:db.execute('UPDATE documents SET page_count=2 WHERE id=?',(DOC_ID,))
    value=report(client)
    assert value['summary']['pages']=={'total':2,'with_text':1,'without_text':[2]}
    page_issue=next(issue for issue in value['issues'] if issue['kind']=='page_without_text')
    assert page_issue['severity']=='info' and page_issue['page']==2 and page_issue['block_id'] is None
    assert page_issue['source_available']
    assert not any(issue['kind']=='missing_image' and issue['page']==2 for issue in value['issues'])


def test_saved_ocr_text_is_distinguished_from_a_page_with_no_recognition(api):
    client,store,_,_,provider=api
    folder=store.data_dir/'documents'/DOC_ID
    with fitz.open(folder/'source.pdf') as pdf:
        page=pdf.new_page(width=220,height=330)
        page.insert_image(fitz.Rect(20,20,200,200),filename=str(folder/'crops'/'original-figure.png'))
        content=pdf.tobytes()
    (folder/'source.pdf').write_bytes(content)
    with store.connect() as db:db.execute('UPDATE documents SET page_count=2 WHERE id=?',(DOC_ID,))
    order=max(b.order for b in store.document(DOC_ID).blocks)+1
    block=Block(id='ocr-paragraph',doc_id=DOC_ID,order=order,type='p',section_path=['1'],page=2,
                en='The OCR result contains 42 samples.',ko='인식한 결과는 표본 42개입니다.')
    store.save_blocks(DOC_ID,[block],[{'origin':'pdf_ocr','bbox':[30,50,180,70],'region':[30,50,180,70],
                                     'references':False,'needs_restore':False,'ocr_language':'eng'}])
    before=saved_state(store);value=report(client)
    assert value['summary']['pages']['without_text']==[2]  # The original PDF stays image-only.
    assert value['summary']['pages']['with_ocr']==[2]
    assert not any(issue['kind']=='page_without_text' and issue['page']==2 for issue in value['issues'])
    recognized=[issue for issue in value['issues'] if issue['kind']=='ocr_recognized']
    assert len(recognized)==1 and recognized[0]['block_id']==block.id and recognized[0]['page']==2
    assert recognized[0]['severity']=='info' and recognized[0]['source_available']
    assert any('숫자' in notice and 'OCR' in notice for notice in value['notices'])
    assert saved_state(store)==before and not provider.calls


def test_superseded_ocr_text_does_not_hide_an_unrecognized_source_page(api):
    client,store,_,_,_=api
    block=add_block(store,'historical-ocr',en='Old OCR text.',superseded_by='b0001')
    with store.connect() as db:
        provenance={'origin':'pdf_ocr','bbox':[20,20,100,40],'references':False}
        db.execute('UPDATE source_blocks SET provenance=? WHERE doc_id=? AND block_id=?',
                   (json.dumps(provenance),DOC_ID,block.id))
    value=report(client)
    assert not any(issue['kind']=='ocr_recognized' for issue in value['issues'])
    assert 'with_ocr' not in value['summary']['pages']


def test_running_extraction_marks_observed_missing_asset_as_temporary_information(api):
    client,store,_,_,_=api
    missing=store.block(DOC_ID,'b0002');missing.image_path=f'documents/{DOC_ID}/crops/not-yet-written.png'
    store.save_blocks(DOC_ID,[missing]);store.update_job(DOC_ID,'Extract','running',.1)
    before=saved_state(store)
    value=report(client)
    assert value['busy'] and value['notices']
    issue=next(issue for issue in value['issues'] if issue['kind']=='missing_image' and issue['block_id']=='b0002')
    assert issue['severity']=='info'
    assert saved_state(store)==before


def test_latest_unapplied_reanalysis_differences_are_information_and_are_not_applied(api):
    client,store,_,parser,_=api
    value=preview(client)
    before=saved_state(store);previews=rows(store,'document_reanalyses');page_calls=len(parser.page_calls)
    diagnostic=report(client)
    differences=[issue for issue in diagnostic['issues'] if issue['kind']=='unmatched_source']
    assert len(differences)==value['counts']['added']+value['counts']['missing']
    assert differences and all(issue['severity']=='info' for issue in differences)
    assert any(issue['block_id']=='b0003' for issue in differences)
    assert saved_state(store)==before and rows(store,'document_reanalyses')==previews and len(parser.page_calls)==page_calls
    applied=apply_changes(client,value,[changed(value,'b0002')])
    assert applied.status_code==200,applied.text
    remaining=[issue for issue in report(client)['issues'] if issue['kind']=='unmatched_source']
    assert len(remaining)==len(differences)  # Applying a crop does not resolve unselected source candidates.
    assert all(issue['severity']=='info' for issue in remaining)


def test_missing_original_pdf_does_not_claim_source_comparison_is_available(api):
    client,store,_,_,_=api
    block=store.block(DOC_ID,'b0001');block.ko=None;store.save_blocks(DOC_ID,[block])
    (store.data_dir/'documents'/DOC_ID/'source.pdf').unlink()
    value=report(client)
    assert value['issues'] and all(not issue['source_available'] for issue in value['issues'])
    assert value['notices']


def test_corrupted_original_pdf_is_unavailable_instead_of_causing_diagnostics_to_fail(api):
    client,store,_,_,_=api
    block=store.block(DOC_ID,'b0001');block.ko=None;store.save_blocks(DOC_ID,[block])
    original=store.data_dir/'documents'/DOC_ID/'source.pdf'
    original.write_bytes(b'%PDF-1.7\ncorrupted local source')
    before=saved_state(store)
    value=report(client)
    assert value['issues'] and all(not issue['source_available'] for issue in value['issues'])
    assert value['notices'] and saved_state(store)==before


def test_diagnostics_uses_document_scope_and_session_authorization(api):
    client,store,_,_,_=api
    before=saved_state(store)
    assert client.get('/documents/missing/diagnostics').status_code==404
    assert client.get(ENDPOINT,headers={'Authorization':''}).status_code==401
    assert client.get(ENDPOINT,headers={'Origin':'https://outside.example'}).status_code==403
    assert client.get('/documents/..%2Fother-paper/diagnostics').status_code==404
    assert saved_state(store)==before


def test_corrupt_png_is_reported_and_repaired_image_invalidates_the_read_cache(api):
    client,store,_,_,_=api
    crop=store.data_dir/'documents'/DOC_ID/'crops'/'original-figure.png'
    original=crop.read_bytes()
    assert 'missing_image' not in kinds_for(report(client),'b0002')
    crop.write_bytes(original[:24])  # PNG header and dimensions survive, pixel data does not.
    value=report(client)
    issue=next(issue for issue in value['issues'] if issue['kind']=='missing_image' and issue['block_id']=='b0002')
    assert issue['severity']=='attention' and issue['source_available']
    crop.write_bytes(original)
    assert 'missing_image' not in kinds_for(report(client),'b0002')


def test_unconnected_large_pdf_image_is_informational_and_provenance_can_cover_it(api):
    client,store,_,parser,_=api
    folder=store.data_dir/'documents'/DOC_ID
    region=[30,240,180,320]
    with fitz.open(folder/'source.pdf') as pdf:
        pdf[0].insert_image(fitz.Rect(region),filename=str(folder/'crops'/'original-figure.png'))
        content=pdf.tobytes()
    (folder/'source.pdf').write_bytes(content)
    before=saved_state(store)
    value=report(client)
    uncovered=[issue for issue in value['issues'] if issue['kind']=='unmatched_source' and issue.get('region')]
    assert len(uncovered)==1
    assert uncovered[0]['severity']=='info' and uncovered[0]['page']==1 and uncovered[0]['block_id'] is None
    assert uncovered[0]['region']==region and uncovered[0]['source_available']
    assert saved_state(store)==before and not parser.page_calls
    block=add_block(store,'connected-image','fig',caption_en='Preserved PDF image.',caption_ko='보존된 PDF 그림.',image_path=f'documents/{DOC_ID}/crops/original-figure.png')
    with store.connect() as db:db.execute('UPDATE source_blocks SET provenance=? WHERE doc_id=? AND block_id=?',
        (json.dumps({'bbox':region,'region':region,'references':False}),DOC_ID,block.id))
    assert not any(issue['kind']=='unmatched_source' and issue.get('region') for issue in report(client)['issues'])


def test_invalid_stored_table_grid_is_observed_without_a_saved_ai_error_marker(api):
    client,store,_,_,_=api
    table=parse_table('<table><tr><td>A</td><td>42</td></tr><tr><td>B</td></tr></table>')
    add_block(store,'invalid-grid','tab',caption_en='Inconsistent extracted grid.',caption_ko='불완전한 추출 표.',table=table)
    value=report(client)
    assert 'restore_failed' in kinds_for(value,'invalid-grid')
    assert 'restore_pending' not in kinds_for(value,'invalid-grid')


def test_empty_equation_remains_an_informational_source_comparison_target(api):
    client,store,_,_,_=api
    add_block(store,'empty-equation','eq')
    before=saved_state(store)
    value=report(client)
    issue=next(issue for issue in value['issues'] if issue['block_id']=='empty-equation' and issue['kind']=='restore_pending')
    assert issue['severity']=='info' and issue['source_available'] and issue['page']==1
    assert 'missing_image' not in kinds_for(value,'empty-equation')
    assert client.get(f'/documents/{DOC_ID}/blocks/empty-equation/source').json()['available']
    assert saved_state(store)==before


def test_text_page_without_linked_blocks_can_be_opened_without_asserting_text_loss(api):
    client,store,_,_,_=api
    source=store.data_dir/'documents'/DOC_ID/'source.pdf'
    with fitz.open(source) as pdf:
        page=pdf.new_page(width=220,height=330)
        page.insert_text((20,40),'Text on page two can be part of a paragraph saved on page one.')
        content=pdf.tobytes()
    source.write_bytes(content)
    with store.connect() as db:db.execute('UPDATE documents SET page_count=2 WHERE id=?',(DOC_ID,))
    before=saved_state(store)
    value=report(client)
    issue=next(issue for issue in value['issues'] if issue['kind']=='unmatched_source' and issue['page']==2)
    assert issue['severity']=='info' and issue['block_id'] is None and issue['source_available']
    assert '합쳐졌을 수도' in issue['detail'] and '대조' in issue['detail']
    assert value['summary']['pages']=={'total':2,'with_text':2,'without_text':[]}
    location=client.get(f'/documents/{DOC_ID}/source')
    assert location.status_code==200 and location.json()['available'] and location.json()['page_count']==2
    rendered=client.get(f'/documents/{DOC_ID}/pages/{issue["page"]}/image')
    assert rendered.status_code==200 and rendered.json()['data_url'].startswith('data:image/png;base64,')
    assert saved_state(store)==before and source.read_bytes()==content


@pytest.mark.parametrize('reason,notice_parts',[
    ('html_unavailable',['HTML 원문','가져오지 못해']),
    ('html_structure',['일부 구조','보존하기 어려워']),
    ('html_images',['그림','불러오거나 변환하지 못해']),
])
def test_arxiv_import_records_actual_pdf_fallback_reason_and_diagnostics_explains_it(tmp_path,monkeypatch,reason,notice_parts):
    fragment=figure('not-available.png') if reason=='html_images' else '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    if reason=='html_unavailable':
        original_fetch=ArxivImporter.fetch
        async def unavailable_html(self,client,url,limit,optional=False):
            if url.endswith(f'/html/{IDENTIFIER}'):return None
            return await original_fetch(self,client,url,limit,optional)
        monkeypatch.setattr(ArxivImporter,'fetch',unavailable_html)
    store,result,started,_,pdfbytes=import_paper(tmp_path,fragment)
    assert result['fallback'] and result['source_kind']=='pdf' and started==[result['doc_id']]
    assert store.job(result['doc_id'])['checkpoint']['source_fallback_reason']==reason
    # Inspect the actual import checkpoint before any startup extraction runs.
    app=create_app(TOKEN,tmp_path,tmp_path/'absent-fixture.json',vault=OfflineVault(),provider=NoAI())
    client=TestClient(app,headers={'Authorization':'Bearer '+TOKEN})
    before=saved_state(store)
    def no_network(*_args,**_kwargs):raise AssertionError('Diagnostic GET must remain offline')
    monkeypatch.setattr('httpx.AsyncClient',no_network)
    try:
        response=client.get(f'/documents/{result["doc_id"]}/diagnostics')
        assert response.status_code==200,response.text
        value=response.json()
        assert any(all(part in notice for part in notice_parts) for notice in value['notices'])
        assert saved_state(store)==before
        assert (tmp_path/'documents'/result['doc_id']/'source.pdf').read_bytes()==pdfbytes
    finally:client.close()


def set_source_provenance(store,block_id,**values):
    with store.connect() as db:
        row=db.execute('SELECT provenance FROM source_blocks WHERE doc_id=? AND block_id=?',(DOC_ID,block_id)).fetchone()
        current=json.loads(row[0]);current.update(values)
        db.execute('UPDATE source_blocks SET provenance=? WHERE doc_id=? AND block_id=?',(json.dumps(current),DOC_ID,block_id))


def insert_unlinked_pdf_image(store):
    folder=store.data_dir/'documents'/DOC_ID
    with fitz.open(folder/'source.pdf') as pdf:
        pdf[0].insert_image(fitz.Rect(30,240,180,320),filename=str(folder/'crops'/'original-figure.png'))
        content=pdf.tobytes()
    (folder/'source.pdf').write_bytes(content)
    return content


@pytest.mark.parametrize('reason,detail_part',[('html_structure','구조'),('html_images','이미지를 불러오거나 변환하지 못해')])
def test_partial_arxiv_pdf_visuals_are_informational_and_current_provenance_wins(api,reason,detail_part):
    client,store,_,parser,provider=api
    with store.connect() as db:db.execute("UPDATE documents SET source_kind='mixed' WHERE id=?",(DOC_ID,))
    set_source_provenance(store,'b0002',origin='arxiv_pdf',visual_fallback_reason=reason,source_anchor='F1')
    healthy=add_block(store,'healthy-html','fig',caption_en='An intact HTML illustration.',caption_ko='온전한 HTML 그림.',image_path=f'documents/{DOC_ID}/crops/original-figure.png')
    healthy.page=None
    store.save_blocks(DOC_ID,[healthy],[{'bbox':[0,0,0,0],'region':None,'references':False,'origin':'arxiv_html','source_anchor':'F2'}])
    set_source_provenance(store,healthy.id,bbox=[0,0,0,0],region=None,origin='arxiv_html',source_anchor='F2')
    # A legacy checkpoint must not classify an intact HTML block as a PDF replacement.
    store.update_job(DOC_ID,'Render','complete',1,visual_fallbacks=[{'block_id':healthy.id,'reason':'html_images','page':1}],source_fallback_reason='html_images')
    pdfbytes=insert_unlinked_pdf_image(store);before=saved_state(store)
    value=report(client)
    replacements=[issue for issue in value['issues'] if issue['kind']=='visual_pdf_fallback']
    assert len(replacements)==1 and replacements[0]['block_id']=='b0002'
    assert replacements[0]['severity']=='info' and replacements[0]['source_available'] and replacements[0]['page']==1
    assert detail_part in replacements[0]['detail']
    assert 'visual_pdf_fallback' not in kinds_for(value,healthy.id)
    assert not any(issue['kind']=='unmatched_source' and issue.get('region') for issue in value['issues'])
    assert any('자동 연결 진단은 보류' in notice for notice in value['notices'])
    assert any('다른 HTML 원문은 유지' in notice for notice in value['notices'])
    assert not any('원본 PDF 방식으로 가져온 논문' in notice for notice in value['notices'])
    assert saved_state(store)==before and not parser.page_calls and not provider.calls
    assert (store.data_dir/'documents'/DOC_ID/'source.pdf').read_bytes()==pdfbytes


def test_mixed_document_without_pdf_replacement_provenance_does_not_invent_one(api):
    client,store,_,_,_=api
    with store.connect() as db:db.execute("UPDATE documents SET source_kind='mixed' WHERE id=?",(DOC_ID,))
    set_source_provenance(store,'b0002',origin='arxiv_html',visual_fallback_reason='html_images')
    store.update_job(DOC_ID,'Render','complete',1,visual_fallbacks=[{'block_id':'b0002','reason':'html_images','page':1}])
    value=report(client)
    assert not any(issue['kind']=='visual_pdf_fallback' for issue in value['issues'])
    assert any('함께 사용하는 논문' in notice for notice in value['notices'])
    assert not any('원문 다시 분석에서' in notice for notice in value['notices'])


def test_mapped_html_visuals_allow_pdf_coverage_and_historical_unmapped_visuals_do_not_block_it(api):
    client,store,_,_,_=api
    with store.connect() as db:db.execute("UPDATE documents SET source_kind='mixed' WHERE id=?",(DOC_ID,))
    set_source_provenance(store,'b0002',origin='arxiv_html')
    older=add_block(store,'older-html','fig',caption_en='Preserved prior source.',superseded_by='b0002')
    older.page=None
    store.save_blocks(DOC_ID,[older],[{'bbox':[0,0,0,0],'region':None,'references':False,'origin':'arxiv_html'}])
    set_source_provenance(store,older.id,bbox=[0,0,0,0],region=None,origin='arxiv_html')
    archived=add_block(store,'older-pdf','fig',caption_en='Prior PDF replacement.',superseded_by='b0002')
    set_source_provenance(store,archived.id,origin='arxiv_pdf',visual_fallback_reason='html_images')
    insert_unlinked_pdf_image(store)
    value=report(client)
    assert any(issue['kind']=='unmatched_source' and issue.get('region') for issue in value['issues'])
    assert not any('자동 연결 진단은 보류' in notice for notice in value['notices'])
    assert not any(issue['kind']=='visual_pdf_fallback' for issue in value['issues'])


def test_partial_pdf_visual_provenance_remains_information_when_original_pdf_is_missing(api):
    client,store,_,_,_=api
    with store.connect() as db:db.execute("UPDATE documents SET source_kind='mixed' WHERE id=?",(DOC_ID,))
    set_source_provenance(store,'b0002',origin='arxiv_pdf',visual_fallback_reason='html_images',source_anchor='F1')
    (store.data_dir/'documents'/DOC_ID/'source.pdf').unlink()
    value=report(client)
    issue=next(issue for issue in value['issues'] if issue['kind']=='visual_pdf_fallback')
    assert issue['severity']=='info' and not issue['source_available']
    assert 'missing_image' not in kinds_for(value,'b0002')  # The retained crop is still available.
