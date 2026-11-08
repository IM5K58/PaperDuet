"""Exercise a real scan through upload, selective OCR, review and source lookup."""
import hashlib
import time

import pymupdf as fitz
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.models import Block, Note
from test_reanalysis import TOKEN, NoAI, OfflineVault, saved_state


def mixed_scan():
    with fitz.open() as source, fitz.open() as pdf:
        source_page=source.new_page(width=500,height=320)
        source_page.insert_text((35,150),'The scanned experiment contains 42 samples.',fontsize=16)
        pixmap=source_page.get_pixmap(matrix=fitz.Matrix(2,2))
        pdf.new_page(width=500,height=320).insert_text((35,70),'Native source paragraph with 17 observations.',fontsize=11)
        page=pdf.new_page(width=500,height=320)
        page.insert_image(page.rect,pixmap=pixmap)
        return pdf.tobytes()


def test_real_scan_is_reviewed_and_applied_without_changing_the_pdf_or_existing_evidence(tmp_path,monkeypatch):
    provider=NoAI()
    app=create_app(TOKEN,tmp_path,tmp_path/'absent-fixture.json',vault=OfflineVault(),provider=provider)
    with TestClient(app,headers={'Authorization':'Bearer '+TOKEN}) as client:
        uploaded=client.post('/documents',content=mixed_scan(),headers={'Content-Type':'application/pdf'})
        assert uploaded.status_code==202,uploaded.text
        doc_id=uploaded.json()['doc_id'];base=f'/documents/{doc_id}'
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            job=client.get(base+'/job').json()
            if job['status'] not in {'queued','running'}:break
            time.sleep(.01)
        assert job['checkpoint']['completed_stages']==['Ingest','Extract','Structure']
        store=app.state.store;doc=store.document(doc_id)
        native=next(b for b in doc.blocks if b.page==1 and b.en and '17' in b.en)
        native.ko='직접 고친 관측 17개입니다.';store.save_blocks(doc_id,[native])
        note=Note(kind='key',title='저장한 근거',body_md='기존 본문의 관측 17개를 기억합니다.',claim='stated',refs=[native.id],origin='user')
        note_block=Block(id='saved-note',doc_id=doc_id,order=len(doc.blocks),type='note',section_path=[],note=note)
        store.save_annotations(doc_id,'[]',[(native.id,note_block)],[])
        source=store.data_dir/'documents'/doc_id/'source.pdf'
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        crops={b.image_path:store.asset_path(doc_id,b.image_path).read_bytes() for b in doc.blocks if b.image_path}
        before=saved_state(store)

        catalog=client.get(base+'/ocr')
        assert catalog.status_code==200,catalog.text
        information=catalog.json()
        assert information['available'] and 'eng' in information['languages'] and information['max_pages']==20
        assert [(p['page'],p['eligible'],p['has_text']) for p in information['pages']]==[(1,False,True),(2,True,False)]
        assert client.post(base+'/ocr',json={'pages':[1],'language':'eng'}).json()['detail']=='OCR_PAGE_HAS_TEXT'
        result=client.post(base+'/ocr',json={'pages':[2],'language':'eng'})
        assert result.status_code==200,result.text
        preview=result.json()
        assert preview['method']=='ocr' and preview['ocr']['results'][0]['status']=='recognized'
        changes=[change for change in preview['changes'] if change['selectable']]
        assert changes and all(change['kind']=='added' and change['new_block']['ocr'] for change in changes)
        assert any('42 samples' in change['new_block']['en'] for change in changes)
        assert saved_state(store)==before and not provider.calls
        assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash

        applied=client.post(f"{base}/reanalysis/{preview['id']}/apply",json={'change_ids':[change['id'] for change in changes]})
        assert applied.status_code==200,applied.text
        doc=store.document(doc_id)
        recognized=[b for b in doc.blocks if b.ocr]
        assert recognized and all(b.page==2 and not b.ko for b in recognized)
        assert doc.status=='ready_to_translate'
        assert store.block(doc_id,native.id).ko==native.ko
        assert any(b.note and b.note.title==note.title and b.note.refs==[native.id] for b in doc.blocks)
        assert all(store.asset_path(doc_id,path).read_bytes()==content for path,content in crops.items())
        assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash and not provider.calls

        location=client.get(f'{base}/blocks/{recognized[0].id}/source').json()
        assert location['available'] and location['page']==2 and location['region']
        assert location['page_size']=={'width':500.0,'height':320.0}
        assert location['region'][1]<150<location['region'][3]
        diagnostic=client.get(base+'/diagnostics').json()
        assert diagnostic['summary']['pages']['with_ocr']==[2]
        assert any(issue['kind']=='ocr_recognized' and issue['page']==2 for issue in diagnostic['issues'])
        markdown=client.get(base+'/export?format=md')
        assert markdown.status_code==200 and '**OCR로 읽은 원문**' in markdown.text and '42 samples' in markdown.text

        def no_repeat(*_args,**_kwargs):
            raise AssertionError('Normal reanalysis must reuse the accepted OCR source')
        monkeypatch.setattr('paperduet.ocr_engine.recognize_page',no_repeat)
        again=client.post(base+'/reanalysis')
        assert again.status_code==200,again.text
        kept=[change for change in again.json()['changes'] if change['old_block'] and change['old_block']['id'] in {b.id for b in recognized}]
        assert len(kept)==len(recognized) and all(change['kind']=='unchanged' for change in kept)
