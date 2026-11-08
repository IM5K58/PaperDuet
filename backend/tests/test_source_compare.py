"""Original PDF locations are precise, bounded, read-only and rotation aware."""
import json

import pymupdf as fitz
import pytest

from paperduet.models import Block
from paperduet.source_compare import MANUAL_REASON,locate_source
from paperduet.store import Store


DOC='source-paper'
URL='https://arxiv.org/abs/2603.07952v1'


def block(identifier='b0',text='Original source paragraph.',kind='p',page=1,**values):
    return Block(id=identifier,doc_id=DOC,order=0,type=kind,section_path=['1'],en=text,page=page,**values)


def seed(tmp_path,blocks,provenance,populate=None,*,pages=1,source_kind='pdf'):
    store=Store(tmp_path,tmp_path/'absent.json')
    path=tmp_path/'documents'/DOC/'source.pdf'
    path.parent.mkdir(parents=True)
    with fitz.open() as pdf:
        for index in range(pages):
            page=pdf.new_page(width=600,height=800)
            if populate:populate(page,index)
        pdf.save(path)
    with store.connect() as db:
        db.execute('INSERT INTO documents(id,title,status,page_count,source_kind,source_url,source_path) VALUES(?,?,?,?,?,?,?)',
                   (DOC,'Source paper','complete',pages,source_kind,URL,f'documents/{DOC}/source.pdf'))
    store.save_blocks(DOC,[value.model_copy(update={'order':index}) for index,value in enumerate(blocks)],provenance)
    return store,path


def test_stored_source_provenance_is_exact_clamped_and_read_only(tmp_path):
    store,path=seed(tmp_path,[block()],[{'bbox':[-5,80,900,130],'region':[10,90,150,120],'source_anchor':'p1'}])
    before=path.read_bytes()
    with store.connect() as db:
        rows=[tuple(row) for row in db.execute('SELECT * FROM source_blocks')]
    result=locate_source(store,DOC,'b0')
    assert result=={'block_id':'b0','page':1,'page_count':1,'bbox':[0.,80.,600.,130.],
                    'region':[10.,90.,150.,120.],'page_size':{'width':600.,'height':800.},
                    'source_kind':'pdf','source_url':URL,'source_anchor':'p1','available':True,'reason':None}
    assert path.read_bytes()==before
    with store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM source_blocks')]==rows


@pytest.mark.parametrize('bad',[['NaN',0,50,50],[0,0,'Infinity',50],[0,0,0,0],[90,90,10,10],[700,900,800,950],['bad',0,50,50]])
def test_invalid_provenance_never_leaks_nan_or_an_invented_highlight(tmp_path,bad):
    store,_=seed(tmp_path,[block()],[{'bbox':bad,'region':bad}])
    result=locate_source(store,DOC,'b0')
    assert result['available'] and result['page']==1
    assert result['bbox'] is None and result['region'] is None and result['reason']==MANUAL_REASON
    json.dumps(result,allow_nan=False)


def test_extreme_but_finite_coordinates_are_clamped_before_native_float_conversion(tmp_path):
    store,_=seed(tmp_path,[block()],[{'bbox':[-1e300,-1e300,1e300,1e300],'region':[1e300,1e300,2e300,2e300]}])
    result=locate_source(store,DOC,'b0')
    assert result['available'] and result['bbox']==[0.,0.,600.,800.]
    assert result['region'] is None
    json.dumps(result,allow_nan=False)


def test_saved_source_of_a_superseded_block_wins_over_the_live_block(tmp_path):
    original=block(page=1)
    store,_=seed(tmp_path,[original],[{'bbox':[50,80,250,110],'region':None}],pages=2)
    live=original.model_copy(update={'page':2,'superseded_by':'replacement','en':'Changed current content.'})
    store.save_blocks(DOC,[live])
    result=locate_source(store,DOC,original.id)
    assert result['page']==1 and result['bbox']==[50.,80.,250.,110.]


def test_html_paragraph_uses_unique_substantial_saved_pdf_text(tmp_path):
    text='The unique source paragraph reports the complete measured result for this experiment.'
    def populate(page,index):
        page.insert_text((50,100),text if index==1 else 'A different source page.',fontsize=10)
    store,_=seed(tmp_path,[block(text=text,page=None)],[{'bbox':[0,0,0,0],'region':None,'source_anchor':'S1.p1'}],populate,pages=2,source_kind='arxiv_html')
    result=locate_source(store,DOC,'b0')
    assert result['available'] and result['page']==2 and result['reason'] is None
    assert result['bbox'] and result['region'] is None and result['source_anchor']=='S1.p1'
    assert result['source_kind']=='arxiv_html' and result['source_url']==URL


def test_repeated_html_paragraph_does_not_guess_a_page(tmp_path):
    text='This repeated source paragraph describes the same measurement on two different pages.'
    store,_=seed(tmp_path,[block(text=text,page=None)],[{'bbox':[0,0,0,0]}],
                 lambda page,index:page.insert_text((50,100),text,fontsize=10),pages=2,source_kind='arxiv_html')
    result=locate_source(store,DOC,'b0')
    assert result['available'] and result['page_count']==2
    assert result['page'] is None and result['bbox'] is None and result['region'] is None
    assert result['page_size'] is None and result['reason']==MANUAL_REASON


def test_figure_label_ignores_body_references_and_finds_the_unique_caption(tmp_path):
    figure=block(text=None,kind='fig',page=None,n='Figure 1',caption_en='Overview.')
    def populate(page,index):
        page.insert_text((50,100),'Figure 1 shows the result in the source paper.' if index==0 else 'Fig. 1: Overview.',fontsize=10)
    store,_=seed(tmp_path,[figure],[{'bbox':[0,0,0,0]}],populate,pages=2,source_kind='arxiv_html')
    result=locate_source(store,DOC,figure.id)
    assert result['page']==2 and result['bbox'] and result['region'] is None


def test_ambiguous_figure_labels_do_not_guess_from_the_label_alone(tmp_path):
    figure=block(text=None,kind='fig',page=None,n='Figure 1',caption_en='Overview.')
    store,_=seed(tmp_path,[figure],[{'bbox':[0,0,0,0]}],
                 lambda page,index:page.insert_text((50,100),'Figure 1: Overview.',fontsize=10),pages=2,source_kind='arxiv_html')
    result=locate_source(store,DOC,figure.id)
    assert result['available'] and result['page'] is None and result['reason']==MANUAL_REASON


def test_numbered_equation_uses_a_unique_math_line_instead_of_prose_references(tmp_path):
    equation=block(text='x=y',kind='eq',page=None,n='(2)',latex='x=y')
    def populate(page,index):
        page.insert_text((50,100),'We refer to Equation (2)' if index==0 else 'x = y (2)',fontsize=10)
    store,_=seed(tmp_path,[equation],[{'bbox':[0,0,0,0]}],populate,pages=2,source_kind='arxiv_html')
    assert locate_source(store,DOC,equation.id)['page']==2


@pytest.mark.parametrize('rotation',[90,270])
@pytest.mark.parametrize('cropped',[False,True])
def test_coordinates_overlay_the_actual_rotated_pixels_including_cropbox_origin(tmp_path,rotation,cropped):
    def populate(page,index):
        rect=fitz.Rect(150,300,250,360) if cropped else fitz.Rect(50,100,150,160)
        page.draw_rect(rect,color=(1,0,0),fill=(1,0,0))
        if cropped:page.set_cropbox(fitz.Rect(100,200,500,700))
        page.set_rotation(rotation)
    # Both versions have the same unrotated coordinates relative to the cropbox.
    store,path=seed(tmp_path,[block()],[{'bbox':[50,100,150,160],'region':[50,100,150,160]}],populate)
    result=locate_source(store,DOC,'b0')
    with fitz.open(path) as pdf:
        pixels=pdf[0].get_pixmap(alpha=False)
    x0,y0,x1,y1=result['region']
    assert result['page_size']=={'width':float(pixels.width),'height':float(pixels.height)}
    assert x1-x0==60 and y1-y0==100
    assert pixels.pixel(round((x0+x1)/2),round((y0+y1)/2))[:3]==(255,0,0)
    assert 0<=x0<x1<=pixels.width and 0<=y0<y1<=pixels.height


def test_missing_pdf_keeps_source_link_and_does_not_claim_a_location(tmp_path):
    store,path=seed(tmp_path,[block()],[{'bbox':[50,100,150,160]}],source_kind='arxiv_html')
    path.unlink()
    result=locate_source(store,DOC,'b0')
    assert not result['available'] and result['source_url']==URL
    assert result['page'] is None and result['bbox'] is None and result['page_size'] is None
    assert result['reason']


def test_corrupt_pdf_is_unavailable_and_does_not_modify_the_file(tmp_path):
    store,path=seed(tmp_path,[block()],[{'bbox':[50,100,150,160]}])
    path.write_bytes(b'not a PDF')
    result=locate_source(store,DOC,'b0')
    assert not result['available'] and result['reason'] and path.read_bytes()==b'not a PDF'


def test_source_pdf_path_cannot_escape_to_another_document(tmp_path):
    store,_=seed(tmp_path,[block()],[{'bbox':[50,100,150,160]}])
    other=tmp_path/'documents'/'other'/'source.pdf';other.parent.mkdir()
    with fitz.open() as pdf:
        pdf.new_page();pdf.save(other)
    with store.connect() as db:
        db.execute('UPDATE documents SET source_path=? WHERE id=?',('documents/other/source.pdf',DOC))
    assert not locate_source(store,DOC,'b0')['available']


def test_missing_document_and_missing_source_block_have_explicit_errors(tmp_path):
    store,_=seed(tmp_path,[block()],[{'bbox':[50,100,150,160]}])
    with pytest.raises(ValueError,match='^DOCUMENT_NOT_FOUND$'):locate_source(store,'missing','b0')
    with pytest.raises(ValueError,match='^BLOCK_NOT_FOUND$'):locate_source(store,DOC,'missing')


def test_source_text_cache_is_shared_by_blocks_and_invalidates_when_pdf_changes(tmp_path,monkeypatch):
    texts=['The first original source paragraph contains a uniquely measured experimental result.',
           'The second source paragraph describes a completely separate calibration experiment.']
    blocks=[block(str(index),text,page=None) for index,text in enumerate(texts)]
    store,path=seed(tmp_path,blocks,[{'bbox':[0,0,0,0]}]*2,
                    lambda page,index:page.insert_text((50,100),texts[index],fontsize=10),pages=2,source_kind='arxiv_html')
    get_text=fitz.Page.get_text;calls=[]
    def counted(page,*args,**kwargs):
        calls.append(page.number)
        return get_text(page,*args,**kwargs)
    monkeypatch.setattr(fitz.Page,'get_text',counted)
    assert locate_source(store,DOC,'0')['page']==1
    assert locate_source(store,DOC,'1')['page']==2
    assert locate_source(store,DOC,'0')['page']==1
    assert calls==[0,1]
    path.write_bytes(path.read_bytes()+b'\n% source PDF changed\n')
    assert locate_source(store,DOC,'0')['page']==1
    assert calls==[0,1,0,1]
