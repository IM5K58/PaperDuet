"""Stable source identities without guessing across source loss/resegmentation."""
import pytest

from paperduet.models import Block,Cell,Table,Note
from paperduet.reanalysis_match import match_blocks,source_signature


def paragraph(identifier,text,*,page=1,section=('1',),kind='p',**values):
    return Block(id=identifier,doc_id='paper',order=0,type=kind,section_path=list(section),page=page,en=text,**values)


def source(x=50,y=100,**values):
    return {'bbox':[x,y,x+200,y+30],**values}


def test_signature_excludes_translations_notes_locations_ids_and_decorations():
    old=paragraph('old','A &amp; B use x<sup>2</sup> at \\(n=2\\).',ko='한국어')
    changed=old.model_copy(deep=True)
    changed.id='new';changed.doc_id='different';changed.order=30;changed.page=99
    changed.section_path=['different'];changed.ko='수정한 번역';changed.qa_flags=[]
    changed.note=Note(kind='key',title='A note',body_md='New note',claim='interpretation',refs=['old'],origin='user')
    changed.en='A &amp; B use x<sup>2</sup> at \\(n=2\\).'
    assert source_signature(old)==source_signature(changed)
    assert source_signature(old)==source_signature(old.model_copy(update={'en':'A & B use x<sup>2</sup> at \\(n=2\\).'}))


@pytest.mark.parametrize('changed',[
    'A &amp; B use x2 at \\(n=2\\).',
    'A &amp; B use x<sub>2</sub> at \\(n=2\\).',
    'A &amp; B use x<sup>2</sup> at \\(n=3\\).',
    'A &amp; B use x<sup>3</sup> at \\(n=2\\).',
])
def test_signature_preserves_scientific_meaning(changed):
    old=paragraph('old','A &amp; B use x<sup>2</sup> at \\(n=2\\).')
    assert source_signature(old)!=source_signature(old.model_copy(update={'en':changed}))


def test_signature_retains_table_cell_source_and_spans_but_ignores_korean_and_highlights():
    old=paragraph('old','',kind='tab',n='Table 1',table=Table(header=[[Cell(text_en='Scores',colspan=2)]],body=[[Cell(text_en='A'),Cell(text_en='42')]]))
    new=old.model_copy(deep=True)
    new.table.header[0][0].text_ko='점수';new.table.body[0][1].text_ko='42'
    new.table.best_cells=[(0,1)];new.table.highlight_rows=[0]
    assert source_signature(old)==source_signature(new)
    new.table.body[0][1].text_en='43'
    assert source_signature(old)!=source_signature(new)
    new=old.model_copy(deep=True);new.table.header[0][0].colspan=1
    assert source_signature(old)!=source_signature(new)


def test_exact_source_content_survives_reordering_and_ids_do_not_drive_matching():
    old=[paragraph('old-a','First source.'),paragraph('old-b','Second source.')]
    new=[paragraph('old-a','Second source.',page=20),paragraph('old-b','First source.',page=10)]
    assert match_blocks(old,{},new,{})==[('old-b',0),('old-a',1)]


def test_additions_are_in_new_order_and_missing_blocks_append_in_old_order():
    old=[paragraph('a','Kept.'),paragraph('b','Missing first.'),paragraph('c','Missing second.')]
    new=[paragraph('new','Added.'),paragraph('keep','Kept.')]
    assert match_blocks(old,{},new,{})==[(None,0),('a',1),('b',None),('c',None)]


def test_unique_source_anchor_precedes_a_coincidental_exact_content_match():
    old=[paragraph('a','Before.'),paragraph('b','Elsewhere.')]
    new=[paragraph('new-a','Elsewhere.'),paragraph('new-b','Before.')]
    before={'a':source(source_anchor='para-a',origin='arxiv_html'),'b':source(source_anchor='para-b',origin='arxiv_html')}
    after={'new-a':source(source_anchor='para-a',origin='arxiv_html'),'new-b':source(source_anchor='para-b',origin='arxiv_html')}
    assert match_blocks(old,before,new,after)==[('a',0),('b',1)]


def test_duplicate_content_matches_physical_occurrences_before_order():
    old=[paragraph('a','Repeated.'),paragraph('b','Repeated.')]
    new=[paragraph('x','Repeated.'),paragraph('y','Repeated.')]
    assert match_blocks(old,{'a':source(y=100),'b':source(y=300)},new,{'x':source(y=300),'y':source(y=100)})==[('b',0),('a',1)]


def test_duplicates_without_geometry_keep_occurrence_order_only_when_counts_agree():
    old=[paragraph('a','Repeated.'),paragraph('b','Repeated.')]
    new=[paragraph('x','Repeated.'),paragraph('y','Repeated.')]
    assert match_blocks(old,{},new,{})==[('a',0),('b',1)]
    new.append(paragraph('z','Repeated.'))
    assert match_blocks(old,{},new,{})==[(None,0),(None,1),(None,2),('a',None),('b',None)]


def test_inserted_duplicate_is_not_assigned_an_existing_identity_by_occurrence():
    old=[paragraph('a','Repeated.'),paragraph('b','Repeated.')]
    new=[paragraph('x','Repeated.'),paragraph('y','Repeated.'),paragraph('z','Repeated.')]
    before={'a':source(y=100),'b':source(y=300)}
    after={'x':source(y=50),'y':source(y=100),'z':source(y=300)}
    assert match_blocks(old,before,new,after)==[(None,0),('a',1),('b',2)]


def test_removed_and_added_repetitions_on_different_pages_are_not_guessed():
    old=[paragraph('a','Repeated.',page=1),paragraph('b','Repeated.',page=2)]
    new=[paragraph('x','Repeated.',page=2),paragraph('y','Repeated.',page=3)]
    assert match_blocks(old,{},new,{})==[('b',0),(None,1),('a',None)]


def test_removed_and_inserted_repetitions_with_conflicting_locations_do_not_use_order():
    old=[paragraph('a','Repeated.'),paragraph('b','Repeated.')]
    new=[paragraph('x','Repeated.'),paragraph('y','Repeated.')]
    before={'a':source(y=100),'b':source(y=300)}
    after={'x':source(y=100),'y':source(y=500)}
    assert match_blocks(old,before,new,after)==[('a',0),(None,1),('b',None)]


def test_source_type_changes_are_addition_and_missing_even_with_the_same_anchor():
    old=[paragraph('old','',kind='fig',n='Figure 1',caption_en='Same source.')]
    new=[paragraph('new','',kind='tab',n='Figure 1',caption_en='Same source.')]
    assert match_blocks(old,{'old':source(source_anchor='same')},new,{'new':source(source_anchor='same')})==[(None,0),('old',None)]


def test_anonymous_images_require_pixels_or_anchor_instead_of_empty_source_equality():
    old=[paragraph('old','',kind='fig')]
    new=[paragraph('new','',kind='fig')]
    assert match_blocks(old,{},new,{})==[(None,0),('old',None)]
    assert match_blocks(old,{'old':source(image_fingerprint='pixels')},new,{'new':source(image_fingerprint='pixels')})==[('old',0)]


def test_named_artwork_can_be_related_without_claiming_its_source_is_unchanged():
    old=[paragraph('old','',kind='fig',n='Figure 1',caption_en='Incomplete caption.')]
    new=[paragraph('new','',kind='fig',n='Figure 1',caption_en='A fully recovered description of the original diagram.')]
    assert source_signature(old[0])!=source_signature(new[0])
    assert match_blocks(old,{},new,{})==[('old',0)]
    new[0].section_path=['2']
    assert match_blocks(old,{},new,{})==[(None,0),('old',None)]


def test_small_source_correction_requires_nearby_same_page_and_section():
    before='The baseline model obtains 42.1 percent accuracy on the evaluation dataset.'
    after='The baseline model obtains 42.2 percent accuracy on the evaluation dataset.'
    old=[paragraph('old',before)];new=[paragraph('new',after)]
    assert match_blocks(old,{'old':source()},new,{'new':source(y=102)})==[('old',0)]
    assert source_signature(old[0])!=source_signature(new[0])
    assert match_blocks(old,{},new,{})==[(None,0),('old',None)]
    assert match_blocks(old,{'old':source()},new,{'new':source(y=500)})==[(None,0),('old',None)]
    new[0].page=2
    assert match_blocks(old,{'old':source()},new,{'new':source()})==[(None,0),('old',None)]


def test_ambiguous_similar_paragraphs_do_not_transfer_an_arbitrary_identity():
    old=[paragraph('old','The baseline model obtains 42.1 percent accuracy on the evaluation dataset.')]
    new=[paragraph('a','The baseline model obtains 42.2 percent accuracy on the evaluation dataset.'),paragraph('b','The baseline model obtains 42.3 percent accuracy on the evaluation dataset.')]
    assert match_blocks(old,{'old':source()},new,{'a':source(),'b':source()})==[(None,0),(None,1),('old',None)]


@pytest.mark.parametrize('split_at',[50,95])
@pytest.mark.parametrize('reverse',[False,True])
def test_split_and_merged_paragraphs_are_not_arbitrarily_paired(split_at,reverse):
    words=['A scientific source paragraph contains all original measurements and must remain complete.' for _ in range(3)]
    text=' '.join(words)
    index=round(len(text)*split_at/100)
    index=text.rfind(' ',0,index)
    whole=[paragraph('whole',text)]
    pieces=[paragraph('part-a',text[:index]),paragraph('part-b',text[index+1:])]
    old,new=(pieces,whole) if reverse else (whole,pieces)
    before={b.id:source() for b in old};after={b.id:source() for b in new}
    assert match_blocks(old,before,new,after)==[(None,i) for i in range(len(new))]+[(b.id,None) for b in old]


def test_near_split_with_a_small_correction_is_more_plausible_than_a_fuzzy_pair():
    text='The original model obtains 42.1 percent accuracy using the entire evaluation dataset and the documented settings. The scores remain reproducible.'
    split=text.rfind(' The scores')
    old=[paragraph('whole',text)]
    new=[paragraph('first',text[:split].replace('42.1','42.2')),paragraph('last',text[split+1:])]
    assert match_blocks(old,{'whole':source()},new,{'first':source(),'last':source(y=130)})==[(None,0),(None,1),('whole',None)]


def test_user_notes_are_not_source_block_candidates():
    old=[paragraph('note','Same text.',kind='note')]
    new=[paragraph('new-note','Same text.',kind='note')]
    assert match_blocks(old,{},new,{})==[(None,0),('note',None)]


def test_parallel_provenance_lists_are_supported_without_mutating_inputs():
    old=[paragraph('old','Original source.')];new=[paragraph('new','Original source.')]
    before=[source()];after=[source()]
    assert match_blocks(old,before,new,after)==[('old',0)]
    assert before==[source()] and old[0].id=='old' and new[0].id=='new'
