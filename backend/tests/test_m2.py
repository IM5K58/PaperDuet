import asyncio
import json
import secrets
import sqlite3

from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.models import Block, Note, PipelineOptions, ReadingPosition
from paperduet.pipeline import Pipeline
from paperduet.store import Store
from paperduet.table_style import decorate_tables
from paperduet.validation import numeric_locations, table_grid
from m2_sample import FIXTURE, SampleProvider, seed
from test_m1 import MemoryVault


def run(store,doc_id,provider):
    pipeline=Pipeline(store,provider)
    async def work():
        pipeline.approve(doc_id,PipelineOptions())
        await pipeline.tasks[doc_id]
    asyncio.run(work())
    return pipeline


def test_m1_upgrade_annotates_all_six_kinds_without_retranslation_and_persists(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider()
    sources={k:v['block'].model_dump() for k,v in store.sources(doc_id).items()}
    store.position(doc_id,ReadingPosition(block_id='b0004',offset=5))
    run(store,doc_id,provider)
    job=store.job(doc_id);assert job['status']=='complete',job
    assert 'Annotate' in job['checkpoint']['completed_stages']
    doc=store.document(doc_id);notes=[b for b in doc.blocks if b.note]
    assert len(notes)==6 and {b.note.kind for b in notes}=={'key','res','lim','ins','mth','trm'}
    assert not any(b.qa_flags for b in doc.blocks)
    assert doc.reading_position.block_id=='b0004'
    assert [b.order for b in doc.blocks]==list(range(14))
    assert {k:v['block'].model_dump() for k,v in store.sources(doc_id).items()}==sources
    assert all(stage=='annotate' for stage,_ in provider.calls)
    assert any(e['id']=='b0003' for e in provider.calls[0][1]['evidence_blocks'])
    assert not any(b['id'] in {'b0006','b0007'} for _,p in provider.calls for b in p['target_blocks']+p['evidence_blocks'])
    persisted=Store(tmp_path,FIXTURE).document(doc_id)
    assert persisted.model_dump()==doc.model_dump()
    before=len(provider.calls);run(store,doc_id,provider)
    assert len(provider.calls)==before and store.document(doc_id).blocks==doc.blocks


def test_note_numbers_do_not_trigger_review_or_automatic_repairs_and_edits_persist(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider(bad=True);run(store,doc_id,provider)
    bad=next(b for b in store.document(doc_id).blocks if b.note and b.note.kind=='res')
    assert not bad.qa_flags and store.job(doc_id)['status']=='complete'
    assert len(provider.calls)==2 and not any('repair_notes' in payload for _,payload in provider.calls)
    token=secrets.token_hex(32);app=create_app(token,tmp_path,FIXTURE,vault=MemoryVault(),provider=provider)
    with TestClient(app,headers={'Authorization':'Bearer '+token}) as client:
        endpoint=f'/documents/{doc_id}/blocks/{bad.id}'
        repaired=client.post(endpoint+'/regenerate')
        assert repaired.status_code==200,repaired.text
        assert repaired.json()['id']==bad.id and not repaired.json()['qa_flags']
        note=repaired.json()['note'];note['body_md']='숫자 99999는 근거에 없다.'
        result=client.patch(endpoint,json={'note':note});assert result.json()['qa_flags']==[]
        assert 'review_count' not in client.get(f'/documents/{doc_id}/job').json()['checkpoint']
        note['body_md']='결과는 42.0이다.'
        fixed=client.patch(endpoint,json={'note':note}).json()
        assert fixed['note']['origin']=='user' and fixed['qa_flags']==[]
        assert client.get(f'/documents/{doc_id}/job').json()['status']=='complete'
        assert client.post(endpoint+'/regenerate',headers={'Authorization':''}).status_code==401
        assert client.patch(endpoint,json={'note':note},headers={'Origin':'https://evil.example'}).status_code==403
    assert Store(tmp_path,FIXTURE).document(doc_id).blocks==store.document(doc_id).blocks


def test_annotation_resume_uses_atomic_section_cache_after_rate_limit(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider();provider.fail_after=2
    run(store,doc_id,provider)
    assert store.job(doc_id)['status']=='paused'
    assert len(store.annotated_sections(doc_id))==1
    oldnotes=[b.model_dump() for b in store.document(doc_id).blocks if b.note]
    nextprovider=SampleProvider();run(Store(tmp_path,FIXTURE),doc_id,nextprovider)
    assert store.job(doc_id)['status']=='complete'
    assert len(nextprovider.calls)==1
    assert all(b['id']!='b0001' for _,p in nextprovider.calls for b in p['target_blocks'])
    assert [b.model_dump() for b in store.document(doc_id).blocks if b.note and b.section_path==['Abstract']]==oldnotes


def test_malformed_annotation_response_is_an_error_and_resume_retries_only_missing_work(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider();provider.malformed=True
    pipeline=run(store,doc_id,provider)
    assert store.job(doc_id)['status']=='paused'
    assert store.job(doc_id)['checkpoint']['error']=='AI_INVALID_JSON'
    assert len(provider.calls)==2
    assert not any(b.qa_flags for b in store.document(doc_id).blocks)
    provider.malformed=False
    run(store,doc_id,provider)
    assert store.job(doc_id)['status']=='complete'
    assert len([b for b in store.document(doc_id).blocks if b.note])==6


def test_invalid_json_is_a_request_error_and_usage_is_recorded(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider();provider.invalid_json=True
    run(store,doc_id,provider)
    job=store.job(doc_id);assert job['status']=='paused'
    assert job['checkpoint']['error']=='AI_INVALID_JSON'
    assert sum(u['tokens_in'] for u in job['usage'])==20


def test_missing_translation_can_resume_without_retranslating_short_or_changed_text(tmp_path):
    store,doc_id=seed(tmp_path)
    block=next(b for b in store.document(doc_id).blocks if b.id=='b0001');block.ko=''
    store.save_blocks(doc_id,[block])
    class Translator(SampleProvider):
        def __init__(self):super().__init__();self.empty=True
        async def json(self,stage,model,payload,image=None):
            if stage!='translate':return await super().json(stage,model,payload,image)
            self.calls.append((stage,payload))
            return {'blocks':[{'id':b['id'],'ko':'' if self.empty else '끝'} for b in payload['blocks']]},{}
    provider=Translator();run(store,doc_id,provider)
    job=store.job(doc_id)
    assert job['status']=='ready_to_translate' and job['checkpoint']['untranslated_count']==1
    assert store.document(doc_id).untranslated_count==1
    provider.empty=False;before=len(provider.calls);run(store,doc_id,provider)
    calls=provider.calls[before:]
    assert len(calls)==1 and calls[0][0]=='translate'
    assert [b['id'] for b in calls[0][1]['blocks']]==['b0001']
    assert store.job(doc_id)['status']=='complete' and store.document(doc_id).untranslated_count==0
    assert not any(b.qa_flags for b in store.document(doc_id).blocks)


def test_legacy_flags_are_ignored_without_changing_saved_content():
    block=Block(id='n',doc_id='d',order=0,type='note',section_path=[],qa_flags=['V5','NOTE_MATH','NOTE_TERM'],
        note=Note(kind='mth',title='계산',body_md='자유롭게 정리한 숫자 99999.',claim='mixed',refs=['missing'],origin='user'))
    assert block.qa_flags==[] and block.note.body_md=='자유롭게 정리한 숫자 99999.'


def test_table_model_rows_maxima_ties_rowspans_and_source_unchanged(tmp_path):
    store,doc_id=seed(tmp_path);blocks=store.document(doc_id).blocks
    table=next(b.table for b in blocks if b.table);original=table.model_copy(deep=True)
    assert decorate_tables(blocks)==['Nova']
    assert table.highlight_rows==[1]
    assert set(table.best_cells)=={(1,1),(0,2),(1,2)}
    assert table_grid(table)==table_grid(original) and numeric_locations(table)==numeric_locations(original)
    assert table.header==original.header and table.body==original.body
    from paperduet.adapter import parse_table
    from paperduet.table_style import decorate_table
    merged=parse_table('<table><thead><tr><th>Type</th><th>Model</th><th>AP</th><th>Score Thresh.</th></tr></thead><tbody><tr><td rowspan="2">Group</td><td>A</td><td>9.1</td><td>0.9</td></tr><tr><td>Nova</td><td>10.1</td><td>0.4</td></tr></tbody></table>')
    decorate_table(merged,['Nova'])
    assert merged.highlight_rows==[1] and merged.best_cells==[(1,1)]


def test_v2_database_migration_preserves_existing_m1_doc_and_position(tmp_path):
    db=sqlite3.connect(tmp_path/'paperduet.sqlite3')
    root=FIXTURE.parents[1]/'backend/paperduet'
    db.executescript((root/'schema.sql').read_text()+(root/'migration_2.sql').read_text())
    db.execute("INSERT INTO documents(id,title,status,last_offset) VALUES('old','Existing M1','complete',19)")
    db.execute('INSERT INTO pipeline_settings VALUES(1,?)',(json.dumps({'translate_model':'custom-model'}),));db.commit();db.close()
    store=Store(tmp_path,FIXTURE)
    assert store.document('old').reading_position.offset==19
    assert store.pipeline_options().translate_model=='custom-model' and store.pipeline_options().annotate_model=='claude-sonnet-5'
    with store.connect() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==9


def test_term_regeneration_relocates_to_first_use_preserving_note_id(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider();pipeline=run(store,doc_id,provider)
    term=next(b for b in store.document(doc_id).blocks if b.note and b.note.kind=='trm')
    store.save_repaired_note(doc_id,term,'b0005')
    assert store.note_anchor(doc_id,term.id)=='b0005'
    result=asyncio.run(pipeline.regenerate(doc_id,term.id))
    assert result.id==term.id and result.qa_flags==[]
    assert store.note_anchor(doc_id,term.id)=='b0001'
    doc=store.document(doc_id)
    assert [b.order for b in doc.blocks]==list(range(len(doc.blocks)))


def test_section_retry_preserves_manual_note_edits(tmp_path):
    from paperduet.annotations import section_batches
    store,doc_id=seed(tmp_path);provider=SampleProvider();pipeline=run(store,doc_id,provider)
    doc=store.document(doc_id);edited=next(b for b in doc.blocks if b.note and b.note.kind=='key')
    edited.note.origin='user';edited.note.body_md='내가 직접 수정한 설명이다.';store.save_blocks(doc_id,[edited])
    key,batch=section_batches(doc.blocks,store.sources(doc_id))[0]
    asyncio.run(pipeline.annotate_section(doc_id,key,batch,PipelineOptions()))
    assert next(b for b in store.document(doc_id).blocks if b.id==edited.id).note==edited.note
