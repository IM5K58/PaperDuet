import asyncio
import json
import secrets
import sqlite3

from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.annotations import first_occurrences, validate_note
from paperduet.models import Block, Note, PipelineOptions, ReadingPosition
from paperduet.pipeline import Pipeline
from paperduet.store import Store
from paperduet.table_style import decorate_tables
from paperduet.validation import numeric_locations, table_grid
from m2_sample import FIXTURE, SampleProvider, seed
from test_m1 import MemoryVault, wait_job


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


def test_v5_failure_visible_regeneration_and_manual_edit_resolve_without_id_change(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider(bad=True);run(store,doc_id,provider)
    bad=next(b for b in store.document(doc_id).blocks if b.note and b.note.kind=='res')
    assert bad.qa_flags==['V5'] and store.job(doc_id)['status']=='review'
    token=secrets.token_hex(32);app=create_app(token,tmp_path,FIXTURE,vault=MemoryVault(),provider=provider)
    with TestClient(app,headers={'Authorization':'Bearer '+token}) as client:
        endpoint=f'/documents/{doc_id}/blocks/{bad.id}'
        repaired=client.post(endpoint+'/regenerate')
        assert repaired.status_code==200,repaired.text
        assert repaired.json()['id']==bad.id and not repaired.json()['qa_flags']
        note=repaired.json()['note'];note['body_md']='숫자 99999는 근거에 없다.'
        result=client.patch(endpoint,json={'note':note});assert result.json()['qa_flags']==['V5']
        assert client.get(f'/documents/{doc_id}/job').json()['checkpoint']['review_count']==1
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


def test_malformed_annotations_flag_anchor_and_section_retry_recovers(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider();provider.malformed=True
    pipeline=run(store,doc_id,provider)
    assert store.job(doc_id)['status']=='review'
    assert len(provider.calls)==4
    flagged=[b for b in store.document(doc_id).blocks if 'ANNOTATE_REVIEW' in b.qa_flags]
    assert len(flagged)==2
    provider.malformed=False
    for block in flagged:asyncio.run(pipeline.regenerate(doc_id,block.id))
    assert store.job(doc_id)['status']=='complete'
    assert len([b for b in store.document(doc_id).blocks if b.note])==6


def test_invalid_json_after_transport_retry_is_reviewable_and_usage_recorded(tmp_path):
    store,doc_id=seed(tmp_path);provider=SampleProvider();provider.invalid_json=True
    run(store,doc_id,provider)
    job=store.job(doc_id);assert job['status']=='review'
    assert sum(u['tokens_in'] for u in job['usage'])==40


def test_v5_calculation_scope_missing_refs_first_use_and_equation_format(tmp_path):
    store,doc_id=seed(tmp_path);doc=store.document(doc_id);by_id={b.id:b for b in doc.blocks}
    firsts=first_occurrences(doc.blocks,doc.glossary)
    note=Block(id='n',doc_id=doc_id,order=0,type='note',section_path=[],note=Note(kind='res',title='결과',body_md='결과는 42.0이다.\n\n계산값: 84.0 = 42.0 × 2 (두 번 합산).',claim='mixed',refs=['b0001'],origin='generated'))
    assert not validate_note(note,by_id,'b0001',firsts)
    note.note.body_md+='\n\n근거 없는 12345.'
    assert validate_note(note,by_id,'b0001',firsts)==['V5']
    note.note.body_md='결과는 42.0이다.';note.note.refs=['missing']
    assert validate_note(note,by_id,'b0001',firsts)==['V5']
    note.note.refs=['b0001'];note.note.kind='trm';note.note.title='Nova'
    assert validate_note(note,by_id,'b0001',firsts)==[]
    assert validate_note(note,by_id,'b0005',firsts)==['NOTE_TERM']
    note.note.kind='mth';note.note.title='식';note.note.refs=['b0004']
    assert validate_note(note,by_id,'b0004',firsts)==['NOTE_MATH']


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
    with store.connect() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==8


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
