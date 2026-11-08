"""Selected HTML visual failures borrow only complete, unambiguous PDF crops."""
from copy import deepcopy
from pathlib import Path

import pymupdf as fitz
import pytest

from paperduet.html_visual_fallback import resolve_pdf_visuals
from paperduet.models import Block,Cell,Table
from paperduet.pdf_parser import PyMuPDFParser


def image(page,rect,color=(1,0,0)):
    with fitz.open() as temporary:
        sample=temporary.new_page(width=100,height=80)
        sample.draw_rect(sample.rect,color=color,fill=color)
        raw=sample.get_pixmap(alpha=False).tobytes('png')
    page.insert_image(rect,stream=raw,keep_proportion=False)


def pdf_file(tmp_path,populate,pages=1):
    path=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        for number in range(pages):
            page=pdf.new_page(width=595,height=842)
            page.insert_text((50,80),'The original source paper preserves all of its visual material.',fontsize=11)
            populate(page,number)
        pdf.save(path)
    return path


def visual(identifier='b0',caption='Figure 1: Original diagram overview.',kind='fig',n='Figure 1',**values):
    return Block(id=identifier,doc_id='paper',order=0,type=kind,section_path=['1'],n=n,caption_en=caption,**values)


def provenance(**values):
    return {'bbox':[0,0,0,0],'region':None,'origin':'arxiv_html','references':True,
            'source_anchor':'F1','visual_fallback_reason':'image_download',**values}


def one_figure(page,index):
    image(page,(50,180,290,340))
    page.insert_text((50,360),'Figure 1: Original diagram overview.',fontsize=9)


def state(blocks,sources):
    return [block.model_dump() for block in blocks],deepcopy(sources)


def test_selected_figure_reuses_source_pixels_without_touching_html_prose_or_captions(tmp_path):
    path=pdf_file(tmp_path,one_figure);original=path.read_bytes()
    paragraph=Block(id='p0',doc_id='paper',order=0,type='p',section_path=['1'],en='Good <b>HTML</b> source.',ko='기존 번역')
    target=visual(caption='<b>Figure 1:</b> Original diagram overview.')
    blocks=[paragraph,target];sources=[provenance(source_anchor='P1'),provenance()]
    good=paragraph.model_dump();caption=target.caption_en
    output=tmp_path/'crops';output.mkdir();(output/'keep.png').write_bytes(b'existing')
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},output)=={'b0'}
    assert paragraph.model_dump()==good and target.caption_en==caption and target.n=='Figure 1'
    assert target.page==1 and target.image_path.startswith('documents/paper/crops/html-pdf-')
    saved=output/Path(target.image_path).name
    pixels=fitz.Pixmap(str(saved))
    assert pixels.pixel(pixels.width//2,pixels.height//2)[:3]==(255,0,0)
    assert sources[1]['origin']=='arxiv_pdf' and sources[1]['visual_origin']=='pdf_crop'
    assert sources[1]['source_anchor']=='F1' and sources[1]['references']
    assert sources[1]['visual_fallback_reason']=='image_download' and not sources[1]['needs_restore']
    assert path.read_bytes()==original and (output/'keep.png').read_bytes()==b'existing'
    assert len(list(output.glob('*.png')))==2


def test_label_embedded_in_caption_is_usable_without_changing_html_label(tmp_path):
    path=pdf_file(tmp_path,one_figure)
    blocks=[visual(n=None)];sources=[provenance()]
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    assert blocks[0].n is None and blocks[0].caption_en=='Figure 1: Original diagram overview.'


def test_pdf_supplement_uses_the_visible_bounds_of_a_clipped_vector_canvas(tmp_path):
    def populate(page,index):
        with fitz.open() as source:
            canvas=source.new_page(width=500,height=200)
            canvas.draw_rect(canvas.rect,color=None,fill=(1,1,1))
            canvas.draw_rect(fitz.Rect(220,40,350,140),color=None,fill=(.2,.4,.8))
            page.show_pdf_page(fitz.Rect(320,180,545,340),source,0,clip=fitz.Rect(200,20,400,180),keep_proportion=False)
        page.insert_text((320,360),'Figure 1: Original diagram overview.',fontsize=9)
    path=pdf_file(tmp_path,populate)
    blocks=[visual()];sources=[provenance()]
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    assert sources[0]['region'][0]>=320 and sources[0]['region'][2]<=545


@pytest.mark.parametrize('caption',['Figure 1: Original diagram result 43.','Figure 2: Original diagram overview.','Figure 1: Original x<sup>2</sup> diagram.'])
def test_inconsistent_caption_number_or_scientific_script_rejects_without_mutation(tmp_path,caption):
    path=pdf_file(tmp_path,one_figure)
    blocks=[visual(caption=caption)];sources=[provenance()];before=state(blocks,sources)
    output=tmp_path/'crops'
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},output)==set()
    assert state(blocks,sources)==before and not list(output.glob('*'))


def test_reused_pdf_label_is_ambiguous_even_if_only_one_caption_matches(tmp_path):
    def populate(page,index):
        image(page,(50,180,290,340))
        page.insert_text((50,360),'Figure 1: '+('Original diagram overview.' if index==0 else 'A completely different appendix diagram.'),fontsize=9)
    path=pdf_file(tmp_path,populate,pages=2)
    blocks=[visual()];sources=[provenance()];before=state(blocks,sources)
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')==set()
    assert state(blocks,sources)==before


def test_missing_one_requested_visual_does_not_publish_a_successful_subset(tmp_path):
    path=pdf_file(tmp_path,one_figure)
    blocks=[visual(),visual('b1','Figure 2: Missing source diagram.',n='Figure 2')]
    sources=[provenance(),provenance(source_anchor='F2')];before=state(blocks,sources)
    output=tmp_path/'crops';output.mkdir();(output/'keep.png').write_bytes(b'keep')
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0','b1'},output)==set()
    assert state(blocks,sources)==before and [file.name for file in output.iterdir()]==['keep.png']


def test_one_pdf_visual_cannot_be_assigned_to_two_different_html_blocks(tmp_path):
    path=pdf_file(tmp_path,one_figure)
    blocks=[visual(),visual('b1')];sources=[provenance(),provenance()]
    before=state(blocks,sources)
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0','b1'},tmp_path/'crops')==set()
    assert state(blocks,sources)==before


def test_multipanel_pixels_and_subcaptions_stay_in_the_original_arrangement(tmp_path):
    caption='Panels compare the original red and blue sources in a combined layout.'
    def populate(page,index):
        image(page,(50,180,280,300),(1,0,0));image(page,(315,180,545,300),(0,0,1))
        page.insert_text((50,325),'(a) First panel',fontsize=9)
        page.insert_text((315,325),'(b) Second panel',fontsize=9)
        page.insert_text((50,360),'Figure 1: '+caption,fontsize=9)
    path=pdf_file(tmp_path,populate)
    combined='Figure 1: '+caption+'<br>(a) First panel<br>(b) Second panel'
    blocks=[visual(caption=combined)];sources=[provenance(visual_panel_count=2,visual_fallback_caption='Figure 1: '+caption)]
    output=tmp_path/'crops'
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},output)=={'b0'}
    assert blocks[0].caption_en==combined
    with fitz.open(path) as pdf:
        text=pdf[0].get_text('text',clip=fitz.Rect(sources[0]['region']))
    assert '(a) First panel' in text and '(b) Second panel' in text and 'Figure 1:' not in text
    pixels=fitz.Pixmap(str(output/Path(blocks[0].image_path).name))
    x0,y0,x1,y1=sources[0]['region'];scale=pixels.width/(x1-x0)
    assert pixels.pixel(round((150-x0)*scale),round((240-y0)*scale))[:3]==(255,0,0)
    assert pixels.pixel(round((430-x0)*scale),round((240-y0)*scale))[:3]==(0,0,255)


def test_wrapped_subcaption_continuations_are_not_cut_off(tmp_path):
    caption='Panels compare the original red and blue sources in a combined layout.'
    def populate(page,index):
        image(page,(50,180,280,300));image(page,(315,180,545,300),(0,0,1))
        page.insert_text((50,325),'(a) The first original panel\ncontains a wrapped explanation\nand every final source label.',fontsize=9)
        page.insert_text((315,325),'(b) The second original panel',fontsize=9)
        page.insert_text((50,390),'Figure 1: '+caption,fontsize=9)
    path=pdf_file(tmp_path,populate)
    blocks=[visual(caption='Figure 1: '+caption)];sources=[provenance(visual_panel_count=2)]
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    with fitz.open(path) as pdf:
        text=pdf[0].get_text('text',clip=fitz.Rect(sources[0]['region']))
    assert 'contains a wrapped explanation' in text and 'and every final source label.' in text
    assert 'Figure 1:' not in text


@pytest.mark.parametrize('raster',[False,True])
def test_partial_side_by_side_panel_crop_is_rejected_for_vectors_and_rasters(tmp_path,raster):
    def populate(page,index):
        if raster:
            image(page,(50,180,280,300));image(page,(315,180,545,300),(0,0,1))
        else:
            page.draw_rect(fitz.Rect(50,180,280,300),color=(1,0,0),fill=(1,0,0))
            page.draw_rect(fitz.Rect(315,180,545,300),color=(0,0,1),fill=(0,0,1))
        for x in (50,320):
            for y in (500,530,560):page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)
        page.insert_text((50,360),'Figure 1: Overview.',fontsize=9)
    path=pdf_file(tmp_path,populate)
    blocks=[visual(caption='Figure 1: Overview.')];sources=[provenance(visual_panel_count=2)]
    before=state(blocks,sources)
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')==set()
    assert state(blocks,sources)==before


@pytest.mark.parametrize('text_only',[False,True])
def test_missing_panel_made_of_thin_paths_or_only_text_is_not_silently_dropped(tmp_path,text_only):
    def populate(page,index):
        page.draw_rect(fitz.Rect(50,180,280,300),color=(1,0,0),fill=(1,0,0))
        if not text_only:
            for start,end in [((315,180),(545,180)),((545,180),(545,300)),((545,300),(315,300)),((315,300),(315,180))]:
                page.draw_line(start,end,color=(0,0,1))
        else:
            page.insert_text((320,220),'Original text-only panel',fontsize=9)
            page.insert_text((50,325),'(a) First original panel',fontsize=9)
            page.insert_text((315,325),'(b) Other original panel',fontsize=9)
        page.insert_text((50,360),'Figure 1: Overview.',fontsize=9)
        for x in (50,320):
            for y in (500,530,560):page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)
    path=pdf_file(tmp_path,populate)
    blocks=[visual(caption='Figure 1: Overview.')];sources=[provenance(visual_panel_count=2)]
    before=state(blocks,sources)
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')==set()
    assert state(blocks,sources)==before


def test_a_composite_pdf_image_can_cover_multiple_html_panels(tmp_path):
    def populate(page,index):
        image(page,(50,180,545,340));page.insert_text((50,360),'Figure 1: Original diagram overview.',fontsize=9)
    path=pdf_file(tmp_path,populate)
    blocks=[visual()];sources=[provenance(visual_panel_count=2)]
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    assert sources[0]['region'][2]>=545


def two_figures(page,index):
    image(page,(50,180,280,300));image(page,(315,180,545,300),(0,0,1))
    page.insert_text((50,360),'Figure 1: Original diagram overview.',fontsize=9)
    page.insert_text((320,360),'Figure 2: Other source diagram.',fontsize=9)
    for x in (50,320):
        for y in (500,530,560):page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)


def test_separately_labelled_neighbor_is_not_treated_as_a_missing_panel(tmp_path):
    path=pdf_file(tmp_path,two_figures)
    blocks=[visual()];sources=[provenance()]
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    assert sources[0]['region'][2]<315


def table_page(page,index):
    page.insert_text((50,160),'Table 1: Scores.',fontsize=9)
    for x in (50,150,250):page.draw_line((x,180),(x,270))
    for y in (180,210,240,270):page.draw_line((50,y),(250,y))
    for x,y,text in [(60,200,'Method'),(160,200,'Score'),(60,230,'A'),(160,230,'42'),(60,260,'B'),(160,260,'43')]:
        page.insert_text((x,y),text,fontsize=10)


def test_failed_html_table_gets_source_crop_and_raw_text_without_inventing_a_grid(tmp_path):
    path=pdf_file(tmp_path,table_page)
    blocks=[visual(kind='tab',n='Table 1',caption='Table 1: Scores.')];sources=[provenance()]
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    assert blocks[0].table is None and '42' in blocks[0].en and '43' in blocks[0].en
    assert sources[0]['needs_restore'] and sources[0]['origin']=='arxiv_pdf'


def test_a_valid_html_grid_is_preserved_when_only_its_picture_needs_replacement(tmp_path):
    path=pdf_file(tmp_path,table_page)
    table=Table(header=[[Cell(text_en='A')]],body=[[Cell(text_en='42',text_ko='원래 번역')]])
    blocks=[visual(kind='tab',n='Table 1',caption='Table 1: Scores.',table=table)];sources=[provenance()]
    before=table.model_dump()
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0'},tmp_path/'crops')=={'b0'}
    assert blocks[0].table.model_dump()==before and not sources[0]['needs_restore']


def test_native_table_grid_crossing_crop_boundary_is_not_accepted_as_complete(tmp_path):
    class PartialTableParser(PyMuPDFParser):
        def structure(self,*args):
            blocks,sources=super().structure(*args)
            for block,source in zip(blocks,sources):
                if block.type=='tab':source['region'][2]=150
            return blocks,sources
    path=pdf_file(tmp_path,table_page)
    blocks=[visual(kind='tab',n='Table 1',caption='Table 1: Scores.')];sources=[provenance()]
    before=state(blocks,sources)
    assert resolve_pdf_visuals(PartialTableParser(),path,blocks,sources,{'b0'},tmp_path/'crops')==set()
    assert state(blocks,sources)==before


def test_batch_extracts_pdf_only_once_and_supports_reanalysis_image_prefix(tmp_path):
    class CountingParser(PyMuPDFParser):
        def __init__(self):super().__init__();self.calls=[]
        def page(self,path,index,target):self.calls.append(index);return super().page(path,index,target)
    path=pdf_file(tmp_path,two_figures);parser=CountingParser()
    blocks=[visual(),visual('b1','Figure 2: Other source diagram.',n='Figure 2')];sources=[provenance(),provenance()]
    prefix='documents/paper/reanalysis/preview/crops'
    assert resolve_pdf_visuals(parser,path,blocks,sources,{'b0','b1'},tmp_path/'crops',image_prefix=prefix)=={'b0','b1'}
    assert parser.calls==[0] and all(block.image_path.startswith(prefix+'/') for block in blocks)
    assert len(list((tmp_path/'crops').glob('*.png')))==2


def test_second_publish_failure_rolls_back_first_file_and_all_block_changes(tmp_path,monkeypatch):
    path=pdf_file(tmp_path,two_figures)
    blocks=[visual(),visual('b1','Figure 2: Other source diagram.',n='Figure 2')];sources=[provenance(),provenance()]
    before=state(blocks,sources)
    target=tmp_path/'crops';target.mkdir();(target/'keep.png').write_bytes(b'keep')
    opened=Path.open;writes=[]
    def failing(path,mode='r',*args,**kwargs):
        if path.parent==target and mode=='xb':
            writes.append(path)
            if len(writes)==2:raise OSError('Injected second publish failure')
        return opened(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,'open',failing)
    assert resolve_pdf_visuals(PyMuPDFParser(),path,blocks,sources,{'b0','b1'},target)==set()
    assert len(writes)==2 and state(blocks,sources)==before
    assert [path.name for path in target.iterdir()]==['keep.png']
