"""API regressions found by independent review of selective reanalysis."""
import json

import pytest

from paperduet.models import Block,Note
from test_reanalysis import BASE,DOC_ID,api,apply_changes,changed,linked_state,preview,saved_state


@pytest.mark.parametrize('damage',['missing','modified'])
def test_reanalysis_recovers_a_damaged_cached_preview_and_can_apply_it(api,damage):
    client,store,_,parser,provider=api
    initial=preview(client)
    change=changed(initial,'b0002')
    staged=store.asset_path(DOC_ID,change['new_block']['image_path'])
    assert staged
    if damage=='missing':
        staged.unlink()
    else:
        staged.write_bytes(staged.read_bytes()+b'\nmodified after preview\n')
    before=saved_state(store)
    refused=apply_changes(client,initial,[change])
    assert refused.status_code==409 and refused.json()['detail']=='REANALYSIS_STALE'
    assert saved_state(store)==before

    fresh=preview(client)
    assert fresh['id']!=initial['id']
    replacement=changed(fresh,'b0002')
    asset=store.asset_path(DOC_ID,replacement['new_block']['image_path'])
    assert asset and asset.read_bytes().startswith(b'\x89PNG')
    assert len(parser.page_calls)==2
    assert saved_state(store)==before

    # A subsequent click must reuse the newly repaired preview, rather than
    # selecting the broken preview with the same second-resolution timestamp.
    again=preview(client)
    assert again['id']==fresh['id'] and len(parser.page_calls)==2
    result=apply_changes(client,fresh,[replacement])
    assert result.status_code==200,result.text
    assert store.block(DOC_ID,'b0002').image_path==replacement['new_block']['image_path']
    assert store.block(DOC_ID,'b0002').caption_ko=='직접 고친 그림 설명.'
    assert not provider.calls


def test_reordered_changed_sources_move_with_recursive_notes_and_are_not_proposed_twice(api,monkeypatch):
    client,store,_,parser,provider=api
    originals=['b0001','b0003','b0004']
    # HTML source anchors give unambiguous identity even when both source text
    # and reading order change. No matching by artificial bNNNN parser IDs.
    with store.connect() as db:
        for block_id in originals:
            row=db.execute('SELECT provenance FROM source_blocks WHERE doc_id=? AND block_id=?',(DOC_ID,block_id)).fetchone()
            source=json.loads(row[0]);source['source_anchor']=block_id
            db.execute('UPDATE source_blocks SET provenance=? WHERE doc_id=? AND block_id=?',(json.dumps(source),DOC_ID,block_id))
    child=Block(id='nested-note',doc_id=DOC_ID,order=0,type='note',section_path=['1'],
                note=Note(kind='ins',title='연결된 추가 메모',body_md='기존 메모와 이전 원문에 대한 기록.',
                          claim='interpretation',refs=['saved-note','b0001'],origin='user'))
    store.save_annotations(DOC_ID,'["review-nested"]',[('saved-note',child)],[])
    sources=store.sources(DOC_ID)
    original_payloads={block_id:sources[block_id]['block'].model_dump(exclude_none=True) for block_id in originals}
    before=linked_state(store)
    child_before=store.block(DOC_ID,'nested-note').note.model_dump()
    desired=['b0004','b0003','b0001','b0000','b0002']

    def revised_structure(_doc_id,_pages,_metadata):
        blocks,provenance=[],[]
        for block_id in desired:
            block=sources[block_id]['block'].model_copy(deep=True)
            block.id='new-'+block_id
            block.order=len(blocks)
            block.ko=block.caption_ko=None
            block.image_path=None
            source={k:v for k,v in sources[block_id].items() if k!='block'}
            if block_id in originals:
                block.en+=' revised'
            if block.type=='fig':
                # Also choose the independently improved figure so that this
                # application accepts the complete new source order, including
                # its otherwise unchanged neighboring figure and heading.
                block.image_path=f'documents/{DOC_ID}/crops/recovered-figure.png'
                source.update(bbox=[10,90,190,200],region=[10,90,190,190])
            blocks.append(block);provenance.append(source)
        return blocks,provenance

    monkeypatch.setattr(parser,'structure',revised_structure)
    value=preview(client)
    selected=[change for change in value['changes'] if change['selectable']]
    altered=[change for change in selected if change['old_block'] and change['old_block']['id'] in originals]
    assert [change['old_block']['id'] for change in altered]==['b0004','b0003','b0001']
    assert all(change['requires_translation'] for change in altered)
    applied=apply_changes(client,value,selected)
    assert applied.status_code==200,applied.text

    document=store.document(DOC_ID)
    replacements={block_id:store.block(DOC_ID,block_id).superseded_by for block_id in originals}
    replacement_order=[block.id for block in document.blocks if block.id in replacements.values()]
    assert replacement_order==[replacements[block_id] for block_id in ['b0004','b0003','b0001']]
    assert len({block.id for block in document.blocks})==len(document.blocks)
    assert linked_state(store)==before
    assert store.block(DOC_ID,'nested-note').note.model_dump()==child_before
    assert store.note_anchor(DOC_ID,'saved-note')=='b0001'
    assert store.note_anchor(DOC_ID,'nested-note')=='saved-note'
    order=[block.id for block in document.blocks]
    for original in originals:
        start=order.index(original)
        family=[original,'saved-note','nested-note',replacements[original]] if original=='b0001' else [original,replacements[original]]
        assert order[start:start+len(family)]==family
        assert store.sources(DOC_ID)[original]['block'].model_dump(exclude_none=True)==original_payloads[original]
        assert store.block(DOC_ID,original).ko==original_payloads[original]['ko']
        assert not store.block(DOC_ID,replacements[original]).ko

    saved=saved_state(store)
    second=preview(client)
    assert second['counts']=={'added':0,'changed':0,'missing':0,'unchanged':5}
    assert not any(change['selectable'] for change in second['changes'])
    assert saved_state(store)==saved
    assert linked_state(store)==before and not provider.calls


def test_all_proposed_changes_cover_nonadjacent_order_crossings_and_converge_in_one_apply(api,monkeypatch):
    client,store,_,parser,_=api
    revised={'b0001','b0003','b0004'}
    with store.connect() as db:
        for block_id in revised:
            row=db.execute('SELECT provenance FROM source_blocks WHERE doc_id=? AND block_id=?',(DOC_ID,block_id)).fetchone()
            provenance=json.loads(row[0]);provenance['source_anchor']=block_id
            db.execute('UPDATE source_blocks SET provenance=? WHERE doc_id=? AND block_id=?',(json.dumps(provenance),DOC_ID,block_id))
    sources=store.sources(DOC_ID)
    desired=['b0004','b0003','b0001','b0000','b0002']

    def reordered(_doc_id,_pages,_metadata):
        blocks,provenance=[],[]
        for block_id in desired:
            block=sources[block_id]['block'].model_copy(deep=True)
            block.id='new-'+block_id;block.order=len(blocks)
            block.ko=block.caption_ko=block.image_path=None
            if block_id in revised:block.en+=' revised'
            blocks.append(block)
            provenance.append({k:v for k,v in sources[block_id].items() if k!='block'})
        return blocks,provenance

    monkeypatch.setattr(parser,'structure',reordered)
    value=preview(client)
    # Although the unchanged figure has no adjacent inversion, the earlier
    # paragraphs pass it. Its new reading position must still be selectable.
    figure=next(c for c in value['changes'] if c['old_block'] and c['old_block']['id']=='b0002')
    assert figure['kind']=='changed' and figure['selectable']
    selected=[c for c in value['changes'] if c['selectable']]
    result=apply_changes(client,value,selected)
    assert result.status_code==200,result.text
    second=preview(client)
    assert second['counts']=={'added':0,'changed':0,'missing':0,'unchanged':5}
