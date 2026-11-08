"""Source pixels and mathematical typography survive PDF extraction."""
import pymupdf as fitz
import pytest

from paperduet.pdf_parser import PyMuPDFParser, inline_lines, lines_of, save_crop


def image(page, rect):
    pixels=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,200,80))
    pixels.clear_with(120)
    page.insert_image(rect,stream=pixels.tobytes('png'),keep_proportion=False)


def extract(tmp_path, populate, width=595, height=842):
    path=tmp_path/'source.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=width,height=height)
        page.insert_text((50,80),'An independent source extraction study',fontsize=12)
        populate(page)
        pdf.save(path)
    return PyMuPDFParser().page(path,0,tmp_path/'crops')


@pytest.mark.parametrize('columns',[False,True])
def test_short_caption_does_not_clip_a_full_width_figure(tmp_path,columns):
    source=fitz.Rect(50,180,545,340)
    def populate(page):
        image(page,source)
        page.insert_text((50,360),'Figure 1: Overview',fontsize=9)
        if columns:
            for x in (50,320):
                for y in (500,530,560):
                    page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)
    figures=[item for item in extract(tmp_path,populate) if item['type']=='fig']
    assert len(figures)==1
    assert fitz.Rect(figures[0]['region']).contains(source)
    pixels=fitz.Pixmap(str(tmp_path/'crops'/figures[0]['crop']))
    assert pixels.width>=source.width*3.9


@pytest.mark.parametrize('left,right,caption_x',[(50,303,50),(292,545,320)])
def test_column_figure_pixels_are_preserved_when_they_enter_the_gutter(tmp_path,left,right,caption_x):
    source=fitz.Rect(left,180,right,340)
    def populate(page):
        image(page,source)
        page.insert_text((caption_x,360),'Figure 1: Overview',fontsize=9)
        for x in (50,320):
            for y in (500,530,560):
                page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)
    figures=[item for item in extract(tmp_path,populate) if item['type']=='fig']
    assert len(figures)==1
    assert fitz.Rect(figures[0]['region']).contains(source)


@pytest.mark.parametrize('above',[False,True])
def test_independent_column_captions_at_the_same_height_are_not_merged(tmp_path,above):
    left=fitz.Rect(50,180,285,340)
    right=fitz.Rect(310,180,545,340)
    def populate(page):
        image(page,left)
        image(page,right)
        baseline=160 if above else 360
        page.insert_text((50,baseline),'Figure 1: Left panel',fontsize=9)
        page.insert_text((320,baseline),'Figure 2: Right panel',fontsize=9)
        for x in (50,320):
            for y in (500,530,560):
                page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)
    figures={item.get('n'):item for item in extract(tmp_path,populate) if item['type']=='fig'}
    assert set(figures)=={'Figure 1','Figure 2'}
    assert figures['Figure 1']['caption_en']=='Left panel'
    assert figures['Figure 2']['caption_en']=='Right panel'
    assert fitz.Rect(figures['Figure 1']['region']).contains(left)
    assert fitz.Rect(figures['Figure 1']['region']).x1<right.x0
    assert fitz.Rect(figures['Figure 2']['region']).contains(right)
    assert fitz.Rect(figures['Figure 2']['region']).x0>left.x1


@pytest.mark.parametrize('caption',['Figure 1: Overview','Figure S1: Overview','Fig. 2 Overview','Figure 1 Overview'])
@pytest.mark.parametrize('above',[False,True])
def test_caption_formats_and_both_sides_of_the_artwork(tmp_path,caption,above):
    source=fitz.Rect(50,180,290,340)
    def populate(page):
        image(page,source)
        page.insert_text((50,160 if above else 360),caption,fontsize=9)
    figures=[item for item in extract(tmp_path,populate) if item['type']=='fig']
    assert len(figures)==1
    assert figures[0]['caption_en']=='Overview'
    assert fitz.Rect(figures[0]['region']).contains(source)


def test_image_without_caption_is_preserved_without_consuming_body_text(tmp_path):
    source=fitz.Rect(50,180,290,340)
    body='An ordinary paragraph below the image must remain searchable source text.'
    def populate(page):
        image(page,source)
        page.insert_text((50,400),body,fontsize=10)
    items=extract(tmp_path,populate)
    figures=[item for item in items if item['type']=='fig']
    assert len(figures)==1 and fitz.Rect(figures[0]['region']).contains(source)
    assert any(item.get('en')==body for item in items if item['type']=='p')


def test_rejected_lower_graphic_does_not_discard_the_figure_above_a_caption(tmp_path):
    source=fitz.Rect(50,100,545,200)
    body='The independent body paragraph must remain outside the source artwork.'
    def populate(page):
        image(page,source)
        page.insert_text((50,225),'Figure 1: The supported illustration above this caption.',fontsize=9)
        page.draw_rect(fitz.Rect(50,226,290,400),color=(0,0,0),fill=(.9,.9,.9))
        page.insert_text((320,280),body,fontsize=10)
    items=extract(tmp_path,populate,width=700)
    figure=next(item for item in items if item['type']=='fig' and item.get('n')=='Figure 1')
    assert fitz.Rect(figure['region']).contains(source)
    assert figure['region'][3]<225
    assert any(body in item.get('en','') for item in items if item['type']=='p')


def test_a_wide_figure_is_not_reused_by_a_later_column_caption(tmp_path):
    wide=fitz.Rect(50,180,545,300)
    narrow=fitz.Rect(50,380,285,480)
    def populate(page):
        image(page,wide);image(page,narrow)
        page.insert_text((50,330),'Figure 1: A wide illustration with a caption that stretches across both columns of the source page.',fontsize=9)
        page.insert_text((50,500),'Figure 2: Separate lower illustration.',fontsize=9)
        for x in (50,320):
            for y in (600,630,660):page.insert_text((x,y),'Several ordinary words belong in this body column.',fontsize=9)
    items=extract(tmp_path,populate)
    figures={item.get('n'):item for item in items if item['type']=='fig'}
    assert set(figures)=={'Figure 1','Figure 2'}
    assert fitz.Rect(figures['Figure 1']['region']).contains(wide)
    assert fitz.Rect(figures['Figure 2']['region']).contains(narrow)
    assert figures['Figure 2']['region'][1]>wide.y1
    assert figures['Figure 2']['region'][2]<wide.x1


def test_clipped_vector_canvas_does_not_consume_prose_in_the_other_column(tmp_path):
    target=fitz.Rect(320,180,545,340)
    body='The independent prose in the left column remains searchable and complete.'
    def populate(page):
        with fitz.open() as source:
            canvas=source.new_page(width=500,height=200)
            canvas.draw_rect(canvas.rect,color=None,fill=(1,1,1))
            canvas.draw_rect(fitz.Rect(220,40,350,140),color=None,fill=(.2,.4,.8))
            page.show_pdf_page(target,source,0,clip=fitz.Rect(200,20,400,180),keep_proportion=False)
        page.insert_text((320,360),'Figure 1: A clipped source canvas.',fontsize=9)
        page.insert_textbox(fitz.Rect(50,180,290,340),body,fontsize=10)
    items=extract(tmp_path,populate)
    figure=next(item for item in items if item['type']=='fig' and item.get('n')=='Figure 1')
    region=fitz.Rect(figure['region'])
    assert region.x0>=target.x0-.1 and region.x1<=target.x1+.1
    assert region.contains(target)
    assert any(body in item.get('en','') for item in items if item['type']=='p')


@pytest.mark.parametrize('baseline',[360,500])
def test_body_reference_is_not_mistaken_for_a_punctuation_free_caption(tmp_path,baseline):
    reference='Figure 1 shows that the comparison must be read together with the methods.'
    def populate(page):
        image(page,(50,180,290,340))
        page.insert_text((50,baseline),reference,fontsize=10)
    items=extract(tmp_path,populate)
    assert any(item.get('en')==reference for item in items if item['type']=='p')
    assert len([item for item in items if item['type']=='fig'])==1


def test_top_table_caption_with_colon_keeps_the_actual_table(tmp_path):
    def populate(page):
        page.insert_text((50,180),'Table 1: Scores',fontsize=9)
        for x in (50,150,250):
            page.draw_line((x,200),(x,260))
        for y in (200,230,260):
            page.draw_line((50,y),(250,y))
        for x,y,text in [(60,220,'Method'),(160,220,'Score'),(60,250,'A'),(160,250,'42')]:
            page.insert_text((x,y),text,fontsize=10)
    tables=[item for item in extract(tmp_path,populate) if item['type']=='tab']
    assert len(tables)==1
    assert fitz.Rect(tables[0]['region']).contains(fitz.Rect(50,200,250,260))
    assert '42' in tables[0]['en']


def test_unlabelled_vector_artwork_survives_but_a_page_rule_is_not_a_figure(tmp_path):
    def populate(page):
        shape=page.new_shape()
        shape.draw_line((50,180),(250,180))
        shape.draw_line((250,180),(150,320))
        shape.draw_line((150,320),(50,180))
        shape.finish(color=(0,0,0))
        shape.commit()
        page.draw_line((50,400),(540,400))
        page.insert_text((50,430),'The surrounding body paragraph is kept as editable and searchable text.',fontsize=10)
    items=extract(tmp_path,populate)
    figures=[item for item in items if item['type']=='fig']
    assert len(figures)==1
    assert fitz.Rect(figures[0]['region']).contains(fitz.Rect(50,180,250,320))
    assert any('surrounding body paragraph' in item.get('en','') for item in items)


def test_rounded_box_of_short_body_lines_is_not_replaced_by_a_picture(tmp_path):
    text='The key assumption is required.\nThis applies to all new samples.'
    def populate(page):
        page.insert_text((50,120),'A longer paragraph establishes the ordinary body font size for this paper.',fontsize=10)
        page.draw_rect(fitz.Rect(50,180,290,280),radius=.1)
        page.insert_text((65,210),text,fontsize=10)
    items=extract(tmp_path,populate)
    assert any(item.get('en')==text.replace('\n',' ') for item in items if item['type']=='p')
    assert not any(item['type']=='fig' for item in items)


def test_superscripts_and_subscripts_retain_their_mathematical_meaning():
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((50,100),'E = mc',fontsize=12)
        page.insert_text((89.5,96),'2',fontsize=7)
        page.insert_text((50,150),'H',fontsize=12)
        page.insert_text((59,153),'2',fontsize=7)
        page.insert_text((63,150),'O',fontsize=12)
        content=inline_lines(lines_of(page))
    assert 'mc<sup>2</sup>' in content
    assert 'H<sub>2</sub>O' in content


def test_large_crop_has_a_bounded_pixel_budget(tmp_path):
    with fitz.open() as pdf:
        page=pdf.new_page(width=1900,height=500)
        region=fitz.Rect(50,50,1850,450)
        save_crop(page,region,tmp_path/'large.png')
    pixels=fitz.Pixmap(str(tmp_path/'large.png'))
    assert pixels.width==3600 and pixels.height==800
