"""Offline regressions for arXiv content that previously disappeared on import."""
import asyncio
from types import SimpleNamespace

import httpx
import pymupdf as fitz
import pytest

from paperduet.arxiv import ArxivImporter,parse_html
from paperduet.models import PipelineOptions
from paperduet.pdf_parser import PyMuPDFParser
from paperduet.pipeline import PARSER_REVISION,Pipeline,trusted_table
from paperduet.store import Store
from test_html_visual_fallback import pdf_file,one_figure,image,table_page


IDENTIFIER='2603.07952v1'
SVG=b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="red"/></svg>'


def article(fragment):
    return '<article class="ltx_document"><h1 class="ltx_title ltx_title_document">HTML title</h1><p>Original text.</p>'+fragment+'</article>'


def import_paper(tmp_path,fragment,assets=None,identifier=IDENTIFIER,source_pdf=None):
    store=Store(tmp_path,tmp_path/'absent-fixture.json')
    started=[]
    pipeline=SimpleNamespace(parser=PyMuPDFParser(),start=started.append)
    if source_pdf is None:
        with fitz.open() as pdf:
            pdf.new_page().insert_text((40,50),'Original PDF text and figure captions')
            pdfbytes=pdf.tobytes()
    else:pdfbytes=source_pdf
    requests=[]
    assets=assets or {}

    def handler(request):
        path=request.url.path;requests.append(path)
        if path==f'/html/{identifier}':return httpx.Response(200,text=article(fragment))
        if path.startswith('/pdf/') and path not in assets:return httpx.Response(200,content=pdfbytes)
        asset=assets.get(path,404)
        if isinstance(asset,Exception):raise asset
        return httpx.Response(asset) if isinstance(asset,int) else httpx.Response(200,content=asset)

    importer=ArxivImporter(store,pipeline,transport=httpx.MockTransport(handler))
    result=asyncio.run(importer.ingest(identifier))
    return store,result,started,requests,pdfbytes


def figure(filename='figure.svg',kind='ltx_figure'):
    return f'<figure class="{kind}"><img class="ltx_graphics" src="{IDENTIFIER}/{filename}"><figcaption>Original caption.</figcaption></figure>'


def assert_pdf_fallback(store,result,started,pdfbytes):
    assert result['fallback'] is True and result['source_kind']=='pdf'
    doc=store.document(result['doc_id'])
    assert doc.source_kind=='pdf' and not doc.blocks
    assert started==[doc.id]
    checkpoint=store.job(doc.id)['checkpoint']
    assert checkpoint['completed_stages']==['Ingest']
    assert checkpoint['parser_revision']==PARSER_REVISION
    assert (store.data_dir/'documents'/doc.id/'source.pdf').read_bytes()==pdfbytes


def test_svg_figure_is_rendered_and_retains_visible_content(tmp_path):
    store,result,started,_,_=import_paper(tmp_path,figure(),{f'/html/{IDENTIFIER}/figure.svg':SVG})
    assert not result['fallback'] and not started
    block=next(b for b in store.document(result['doc_id']).blocks if b.type=='fig')
    image=fitz.Pixmap(str(store.asset_path(result['doc_id'],block.image_path)))
    assert (image.width,image.height)==(80,40)
    assert image.pixel(40,20)[:3]==(255,0,0)
    assert block.caption_en=='Original caption.'


def test_image_table_is_visible_and_supplied_to_table_restoration(tmp_path):
    with fitz.open() as pdf:
        page=pdf.new_page(width=80,height=40)
        page.insert_text((5,20),'A 42')
        png=page.get_pixmap().tobytes('png')
    store,result,started,requests,_=import_paper(tmp_path,figure('table.png','ltx_table'),{f'/html/{IDENTIFIER}/table.png':png})
    assert not result['fallback'] and not started
    doc=store.document(result['doc_id'])
    block=next(b for b in doc.blocks if b.type=='tab')
    source=store.sources(doc.id)[block.id]
    assert f'/html/{IDENTIFIER}/table.png' in requests
    assert source['needs_restore'] is True
    assert block.table is None and block.image_path and not trusted_table(source['block'])
    image_path=store.asset_path(doc.id,block.image_path)
    assert image_path and image_path.read_bytes().startswith(b'\x89PNG')
    unit=Pipeline(store,None).restore_unit(doc.id,block,source['block'],PipelineOptions())
    request=next(unit)
    assert request.image==image_path
    with pytest.raises(StopIteration):unit.send({'html':'<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>42</td></tr></tbody></table>'})
    assert block.table.body[0][0].text_en=='42'


@pytest.mark.parametrize('asset',[404,403,b'not an image',b'<html>Server error</html>',httpx.ConnectError('Offline')])
def test_failed_image_never_commits_incomplete_html(tmp_path,asset):
    store,result,started,_,pdfbytes=import_paper(tmp_path,figure(),{f'/html/{IDENTIFIER}/figure.svg':asset})
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert not (store.data_dir/'documents'/result['doc_id']/'crops').exists()


def test_rejected_image_location_uses_pdf_without_requesting_it(tmp_path):
    fragment=figure().replace(f'{IDENTIFIER}/figure.svg','https://evil.test/figure.svg')
    store,result,started,requests,pdfbytes=import_paper(tmp_path,fragment)
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert requests==[f'/html/{IDENTIFIER}',f'/pdf/{IDENTIFIER}']


@pytest.mark.parametrize('fragment',[
    '<figure class="ltx_figure"><figure><img class="ltx_graphics" src="a.png"><figcaption>(a) First panel</figcaption></figure><figure><img class="ltx_graphics" src="b.png"><figcaption>(b) Second panel</figcaption></figure><figcaption>Figure 1: Overall caption</figcaption></figure>',
    '<figure class="ltx_figure"><img class="ltx_graphics" src="a.png"><img class="ltx_graphics" src="b.png"><figcaption>Figure 1: A and B</figcaption></figure>',
    '<figure class="ltx_figure"><svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg><figcaption>Figure 1: Inline vector</figcaption></figure>',
    '<table class="ltx_equation"><tr><td><math><mfrac><mi>x</mi><mi>y</mi></mfrac></math></td></tr></table>',
    '<table class="ltx_equation"><tr><td>No usable MathML</td></tr></table>',
])
def test_unrepresentable_layout_or_math_keeps_complete_pdf(tmp_path,fragment):
    store,result,started,requests,pdfbytes=import_paper(tmp_path,fragment)
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert requests==[f'/html/{IDENTIFIER}',f'/pdf/{IDENTIFIER}']


def test_display_equations_use_tex_annotations_including_multiline_rows():
    fragment='''<table class="ltx_equationgroup"><tr class="ltx_eqn_row"><td><math><semantics><mfrac><mi>x</mi><mi>y</mi></mfrac><annotation encoding="application/x-tex">\\frac{x}{y}</annotation></semantics></math></td></tr><tr class="ltx_eqn_row"><td><math alttext="z=2"></math></td></tr></table>'''
    _,blocks,_,_=parse_html('d',article(fragment))
    equation=next(b for b in blocks if b.type=='eq')
    assert equation.latex==r'\begin{aligned}\frac{x}{y}\\z=2\end{aligned}'


def test_single_display_equation_uses_tex_annotation():
    fragment='<div class="ltx_equation"><math><annotation encoding="application/x-tex">x &lt; 1</annotation></math></div>'
    _,blocks,_,_=parse_html('d',article(fragment))
    equation=next(b for b in blocks if b.type=='eq')
    assert equation.latex=='x < 1'


def test_caption_wrapped_in_div_is_preserved(tmp_path):
    fragment=figure().replace('<figcaption>','<div><figcaption>').replace('</figcaption>','</figcaption></div>')
    store,result,_,_,_=import_paper(tmp_path,fragment,{f'/html/{IDENTIFIER}/figure.svg':SVG})
    assert not result['fallback']
    block=next(b for b in store.document(result['doc_id']).blocks if b.type=='fig')
    assert block.caption_en=='Original caption.' and block.image_path


@pytest.mark.parametrize('caption',['<figcaption>Caption without an illustration.</figcaption>',
    '<div><span><figcaption>Caption without an illustration.</figcaption></span></div>'])
def test_genuinely_caption_only_figure_does_not_force_pdf_fallback(tmp_path,caption):
    store,result,started,_,_=import_paper(tmp_path,f'<figure class="ltx_figure">{caption}</figure>')
    assert not result['fallback'] and not started
    block=next(b for b in store.document(result['doc_id']).blocks if b.type=='fig')
    assert block.caption_en=='Caption without an illustration.' and block.image_path is None


@pytest.mark.parametrize('fragment',[
    '<figure class="ltx_figure"><figure><img class="ltx_graphics" src="a.png"><figcaption>(a) First panel</figcaption></figure><div><figcaption>Overall caption.</figcaption></div></figure>',
    '<figure class="ltx_figure"><img class="ltx_graphics" src="a.png"><img src="extra.png"><figcaption>Two illustrations.</figcaption></figure>',
    '<figure class="ltx_figure"><img src="unclassified.png"><figcaption>Unclassified illustration.</figcaption></figure>',
    '<figure class="ltx_figure"><hr><figcaption>Visible content besides the caption.</figcaption></figure>',
    '<figure class="ltx_figure"><img src="loose.png"><figure><img class="ltx_graphics" src="a.png"><figcaption>Inner panel.</figcaption></figure></figure>',
])
def test_wrapped_parent_caption_or_unhandled_visual_is_not_dropped(tmp_path,fragment):
    store,result,started,_,pdfbytes=import_paper(tmp_path,fragment)
    assert_pdf_fallback(store,result,started,pdfbytes)


@pytest.mark.parametrize('fragment',[
    '<p>Inline illustration <img class="ltx_graphics" src="inline.png"> beside text.</p>',
    '<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>',
    '<object data="diagram.svg" type="image/svg+xml"></object>',
    '<canvas width="10" height="10"></canvas>',
    '<figure class="ltx_figure"><figcaption>Caption with icon <img class="ltx_graphics" src="icon.png"></figcaption></figure>',
])
def test_graphics_outside_supported_figure_content_trigger_pdf_fallback(tmp_path,fragment):
    store,result,started,_,pdfbytes=import_paper(tmp_path,fragment)
    assert_pdf_fallback(store,result,started,pdfbytes)


def test_standalone_html_table_retains_grid_spans_caption_and_cell_math(tmp_path):
    fragment='''<table class="ltx_tabular"><caption>Original table caption</caption><thead><tr><th rowspan="2">Model</th><th colspan="2">Score</th></tr><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>Ours</td><td>42</td><td><math alttext="x^2"></math></td></tr></tbody></table>'''
    store,result,started,_,_=import_paper(tmp_path,fragment)
    assert not result['fallback'] and not started
    block=next(b for b in store.document(result['doc_id']).blocks if b.type=='tab')
    assert block.caption_en=='Original table caption'
    assert block.table.header[0][0].rowspan==2 and block.table.header[0][1].colspan==2
    assert [c.text_en for c in block.table.body[0]]==['Ours','42',r'\(x^2\)']


@pytest.mark.parametrize('header',['<tr><th>Model</th><th>Enabled</th></tr>',
    '<thead><tr><th>Model</th><th>Enabled</th></tr></thead>'])
def test_categorical_table_body_rows_do_not_become_headers(header):
    _,blocks,_,_=parse_html('d',article('<table class="ltx_tabular">'+header+'<tr><td>Ours</td><td>Yes</td></tr><tr><td>Baseline</td><td>No</td></tr></table>'))
    table=next(b.table for b in blocks if b.table)
    assert len(table.header)==1
    assert [[c.text_en for c in row] for row in table.body]==[['Ours','Yes'],['Baseline','No']]
    assert not any(c.is_header for row in table.body for c in row)


@pytest.mark.parametrize('rows',[
    '<tr><td rowspan="10">Ours</td><td>42</td></tr>',
    '<tr><td>A</td><td>42</td></tr><tr><td>B</td></tr>',
    '<thead><tr><th rowspan="2">A</th><th>Score</th></tr></thead><tbody><tr><td>42</td></tr></tbody>',
    '<tr><td>Outer<table><tr><td>Inner</td></tr></table></td><td>42</td></tr>',
])
def test_invalid_or_nested_html_table_is_not_committed_as_valid_grid(tmp_path,rows):
    store,result,started,_,pdfbytes=import_paper(tmp_path,'<table class="ltx_tabular">'+rows+'</table>')
    assert_pdf_fallback(store,result,started,pdfbytes)


@pytest.mark.parametrize('embedded',[
    '<figure><img class="ltx_graphics" src="2603.07952v1/figure.svg"><figcaption>Original caption</figcaption></figure>',
    '<table class="ltx_tabular"><tr><td>A</td><td>42</td></tr></table>',
    '<span class="ltx_equation"><math alttext="x=1"></math><span class="ltx_tag_equation">(1)</span></span>',
])
def test_paragraph_embedded_blocks_are_not_flattened_or_dropped(tmp_path,embedded):
    store,result,started,_,pdfbytes=import_paper(tmp_path,'<p>Before '+embedded+' After</p>')
    assert_pdf_fallback(store,result,started,pdfbytes)


def test_inline_math_is_retained_in_section_and_run_in_titles():
    math='<math alttext="x^2"><mi>x</mi><annotation encoding="application/x-tex">x^2</annotation></math>'
    fragment='<h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1.</span>Condition '+math+'</h2><h5 class="ltx_title ltx_title_paragraph">Case '+math+'</h5>'
    _,blocks,_,_=parse_html('d',article(fragment))
    section=next(b for b in blocks if b.type=='sec')
    assert section.en==r'Condition \(x^2\)' and section.n=='1' and section.section_path==['1']
    assert blocks[-1].en==r'<b>Case \(x^2\)</b>'


def test_document_title_preserves_math_instead_of_flattening_mathml():
    source=article('').replace('HTML title','Condition <math alttext="x^2"><mi>x</mi><annotation encoding="application/x-tex">x^2</annotation></math>')
    meta,_,_,_=parse_html('d',source)
    assert meta['title']==r'Condition \(x^2\)'


def test_standalone_mathml_is_not_skipped():
    _,blocks,_,_=parse_html('d',article('<div><math display="block" alttext="x=1"></math></div>'))
    equation=next(b for b in blocks if b.type=='eq')
    assert equation.latex=='x=1'


def test_versionless_id_accepts_asset_from_its_resolved_version(tmp_path):
    fragment=figure().replace(IDENTIFIER,'2603.07952v3')
    store,result,_,requests,_=import_paper(tmp_path,fragment,{f'/html/2603.07952v3/figure.svg':SVG},identifier='2603.07952')
    assert not result['fallback'] and '/html/2603.07952v3/figure.svg' in requests
    assert '/pdf/2603.07952v3' in requests and '/pdf/2603.07952' not in requests
    assert store.job(result['doc_id'])['checkpoint']['source_arxiv_id']=='2603.07952v3'
    assert store.document(result['doc_id']).arxiv_id=='2603.07952'
    assert store.document(result['doc_id']).source_url=='https://arxiv.org/abs/2603.07952v3'
    assert next(b for b in store.document(result['doc_id']).blocks if b.type=='fig').image_path


def test_pinned_version_does_not_import_asset_from_another_revision(tmp_path):
    fragment=figure().replace(IDENTIFIER,'2603.07952v2')
    store,result,started,requests,pdfbytes=import_paper(tmp_path,fragment)
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert requests==[f'/html/{IDENTIFIER}',f'/pdf/{IDENTIFIER}']


MAIN_CAPTION='Panels compare the original red and blue sources in a combined layout.'
MULTIPANEL=f'''<figure id="F1" class="ltx_figure"><figure><img class="ltx_graphics" src="{IDENTIFIER}/red.png"><figcaption>(a) First panel</figcaption></figure><figure><img class="ltx_graphics" src="{IDENTIFIER}/blue.png"><figcaption>(b) Second panel</figcaption></figure><figcaption><span class="ltx_tag ltx_tag_figure">Figure 1:</span> {MAIN_CAPTION}</figcaption></figure>'''


def multipanel_pdf(page,index):
    image(page,(50,180,280,300),(1,0,0));image(page,(315,180,545,300),(0,0,1))
    page.insert_text((50,325),'(a) First panel',fontsize=9)
    page.insert_text((315,325),'(b) Second panel',fontsize=9)
    page.insert_text((50,360),'Figure 1: '+MAIN_CAPTION,fontsize=9)


def test_visual_fallback_is_opt_in_and_keeps_all_parent_and_subcaptions():
    with pytest.raises(ValueError,match='ARXIV_HTML_UNAVAILABLE'):parse_html('d',article(MULTIPANEL))
    _,blocks,sources,images=parse_html('d',article(MULTIPANEL),allow_visual_fallback=True)
    block=next(b for b in blocks if b.type=='fig');source=sources[block.order]
    assert block.n=='Figure 1' and block.image_path is None
    assert all(text in block.caption_en for text in [MAIN_CAPTION,'(a) First panel','(b) Second panel'])
    assert source['visual_fallback_caption']=='Figure 1: '+MAIN_CAPTION
    assert source['visual_fallback_reason']=='html_structure' and source['visual_panel_count']==2
    assert source['source_anchor']=='F1' and source['visual_source_urls']==[f'{IDENTIFIER}/red.png',f'{IDENTIFIER}/blue.png']
    assert images=={}


@pytest.mark.parametrize('bad_image',[404,b'broken image data'])
def test_failed_html_image_replaces_only_that_figure_with_a_pdf_crop(tmp_path,bad_image):
    def populate(page,index):
        if index==0:one_figure(page,index)
        else:
            image(page,(50,180,290,340),(0,1,0))
            page.insert_text((50,360),'Figure 2: Healthy HTML illustration.',fontsize=9)
    pdfbytes=pdf_file(tmp_path,populate,pages=2).read_bytes()
    fragment=f'''<p>Good <b>HTML</b> preserves <math alttext="x^2"></math> here.</p><figure id="F1" class="ltx_figure"><img class="ltx_graphics" src="{IDENTIFIER}/bad.png"><figcaption><span class="ltx_tag ltx_tag_figure">Figure 1:</span> Original diagram overview.</figcaption></figure><figure id="F2" class="ltx_figure"><img class="ltx_graphics" src="{IDENTIFIER}/green.svg"><figcaption><span class="ltx_tag ltx_tag_figure">Figure 2:</span> Healthy HTML illustration.</figcaption></figure>'''
    store,result,started,requests,_=import_paper(tmp_path,fragment,{f'/html/{IDENTIFIER}/bad.png':bad_image,f'/html/{IDENTIFIER}/green.svg':SVG.replace(b'red',b'lime')},source_pdf=pdfbytes)
    assert result['source_kind']=='mixed' and not result['fallback'] and not started
    doc=store.document(result['doc_id']);sources=store.sources(doc.id)
    assert any(b.en==r'Good <b>HTML</b> preserves \(x^2\) here.' for b in doc.blocks)
    repaired=next(b for b in doc.blocks if b.n=='Figure 1');healthy=next(b for b in doc.blocks if b.n=='Figure 2')
    assert repaired.page==1 and sources[repaired.id]['origin']=='arxiv_pdf'
    assert healthy.page is None and sources[healthy.id]['origin']=='arxiv_html'
    assert sources[healthy.id]['visual_source_urls']==[f'{IDENTIFIER}/green.svg']
    for block,color in [(repaired,(255,0,0)),(healthy,(0,255,0))]:
        pixels=fitz.Pixmap(str(store.asset_path(doc.id,block.image_path)))
        assert pixels.pixel(pixels.width//2,pixels.height//2)[:3]==color
    checkpoint=store.job(doc.id)['checkpoint']
    assert checkpoint['visual_fallbacks']==[{'block_id':repaired.id,'reason':'html_images','page':1}]
    assert 'source_fallback_reason' not in checkpoint
    assert checkpoint['completed_stages']==['Ingest','Extract','Structure']
    assert (tmp_path/'documents'/doc.id/'source.pdf').read_bytes()==pdfbytes
    assert (tmp_path/'documents'/doc.id/'source.html').read_text(encoding='utf-8')==article(fragment)
    assert f'/html/{IDENTIFIER}/green.svg' in requests


def test_multipanel_import_retains_pdf_panel_layout_and_all_html_captions(tmp_path):
    pdfbytes=pdf_file(tmp_path,multipanel_pdf).read_bytes()
    store,result,started,requests,_=import_paper(tmp_path,MULTIPANEL,source_pdf=pdfbytes)
    assert result['source_kind']=='mixed' and not result['fallback'] and not started
    doc=store.document(result['doc_id']);block=next(b for b in doc.blocks if b.type=='fig')
    source=store.sources(doc.id)[block.id]
    assert block.n=='Figure 1' and all(text in block.caption_en for text in [MAIN_CAPTION,'(a) First panel','(b) Second panel'])
    with fitz.open(stream=pdfbytes,filetype='pdf') as pdf:
        text=pdf[0].get_text('text',clip=fitz.Rect(source['region']))
    assert '(a) First panel' in text and '(b) Second panel' in text
    pixels=fitz.Pixmap(str(store.asset_path(doc.id,block.image_path)))
    x0,y0,x1,y1=source['region'];scale=pixels.width/(x1-x0)
    assert pixels.pixel(round((150-x0)*scale),round((240-y0)*scale))[:3]==(255,0,0)
    assert pixels.pixel(round((430-x0)*scale),round((240-y0)*scale))[:3]==(0,0,255)
    assert source['visual_fallback_reason']=='html_structure' and source['source_anchor']=='F1'
    assert requests==[f'/html/{IDENTIFIER}',f'/pdf/{IDENTIFIER}']


def test_invalid_html_table_uses_pdf_image_while_other_html_table_stays_structured(tmp_path):
    pdfbytes=pdf_file(tmp_path,table_page).read_bytes()
    fragment='''<figure class="ltx_table"><figcaption><span class="ltx_tag ltx_tag_table">Table 1:</span> Scores.</figcaption><table class="ltx_tabular"><tr><td rowspan="9">Broken</td><td>999</td></tr></table></figure><figure class="ltx_table"><figcaption><span class="ltx_tag ltx_tag_table">Table 2:</span> Healthy scores.</figcaption><table class="ltx_tabular"><thead><tr><th>Method</th><th>Score</th></tr></thead><tr><td>Good</td><td>9</td></tr></table></figure>'''
    store,result,started,_,_=import_paper(tmp_path,fragment,source_pdf=pdfbytes)
    assert result['source_kind']=='mixed' and not result['fallback'] and not started
    doc=store.document(result['doc_id']);sources=store.sources(doc.id)
    bad=next(b for b in doc.blocks if b.n=='Table 1');good=next(b for b in doc.blocks if b.n=='Table 2')
    assert bad.table is None and bad.image_path and bad.page==1 and '42' in bad.en and '43' in bad.en
    assert sources[bad.id]['needs_restore'] and sources[bad.id]['origin']=='arxiv_pdf'
    assert good.table.body[0][1].text_en=='9' and good.image_path is None
    assert sources[good.id]['origin']=='arxiv_html'


def test_one_unresolved_visual_keeps_the_complete_pdf_import_and_saved_html(tmp_path):
    pdfbytes=pdf_file(tmp_path,one_figure).read_bytes()
    fragment=f'''<figure class="ltx_figure"><img class="ltx_graphics" src="{IDENTIFIER}/missing1.png"><figcaption><span class="ltx_tag ltx_tag_figure">Figure 1:</span> Original diagram overview.</figcaption></figure><figure class="ltx_figure"><img class="ltx_graphics" src="{IDENTIFIER}/missing99.png"><figcaption><span class="ltx_tag ltx_tag_figure">Figure 99:</span> Unmatched illustration.</figcaption></figure>'''
    store,result,started,_,_=import_paper(tmp_path,fragment,source_pdf=pdfbytes)
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert not store.document(result['doc_id']).blocks
    assert (tmp_path/'documents'/result['doc_id']/'source.html').read_text(encoding='utf-8')==article(fragment)
    assert 'visual_fallbacks' not in store.job(result['doc_id'])['checkpoint']


def test_versionless_mixed_revision_assets_use_complete_requested_pdf_without_fetching_assets(tmp_path):
    fragment=figure('red.svg').replace(IDENTIFIER,'2603.07952v2')+figure('blue.svg').replace(IDENTIFIER,'2603.07952v3')
    store,result,started,requests,pdfbytes=import_paper(tmp_path,fragment,identifier='2603.07952')
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert requests==['/html/2603.07952','/pdf/2603.07952']
    assert store.job(result['doc_id'])['checkpoint']['source_arxiv_id']=='2603.07952'


def test_versionless_visual_supplement_requires_an_explicit_revision_proof(tmp_path,monkeypatch):
    pdfbytes=pdf_file(tmp_path,multipanel_pdf).read_bytes()
    fragment=MULTIPANEL.replace(f'{IDENTIFIER}/','')
    calls=[]
    monkeypatch.setattr('paperduet.html_visual_fallback.resolve_pdf_visuals',lambda *args,**kwargs:calls.append(args) or set())
    store,result,started,requests,_=import_paper(tmp_path,fragment,identifier='2603.07952',source_pdf=pdfbytes)
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert not calls and requests==['/html/2603.07952','/pdf/2603.07952']


def test_pinned_multipanel_placeholder_cannot_borrow_another_revisions_assets(tmp_path):
    pdfbytes=pdf_file(tmp_path,multipanel_pdf).read_bytes()
    store,result,started,requests,_=import_paper(tmp_path,MULTIPANEL.replace(IDENTIFIER,'2603.07952v2'),source_pdf=pdfbytes)
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert requests==[f'/html/{IDENTIFIER}',f'/pdf/{IDENTIFIER}']


@pytest.mark.parametrize('failure',[404,b'not a PDF',b'%PDF-1.7\nbroken document'])
def test_unavailable_pdf_version_inferred_from_html_retries_only_a_complete_requested_pdf(tmp_path,failure):
    fragment=figure().replace(IDENTIFIER,'2603.07952v3')
    store,result,started,requests,pdfbytes=import_paper(tmp_path,fragment,{'/pdf/2603.07952v3':failure},identifier='2603.07952')
    assert_pdf_fallback(store,result,started,pdfbytes)
    assert requests==['/html/2603.07952','/pdf/2603.07952v3','/pdf/2603.07952']
    checkpoint=store.job(result['doc_id'])['checkpoint']
    assert checkpoint['source_arxiv_id']=='2603.07952' and checkpoint['source_fallback_reason']=='html_images'
    assert store.document(result['doc_id']).source_url=='https://arxiv.org/abs/2603.07952'
    assert (tmp_path/'documents'/result['doc_id']/'source.html').read_text(encoding='utf-8')==article(fragment)


def test_unavailable_explicitly_requested_pdf_version_is_not_silently_changed(tmp_path):
    with pytest.raises(ValueError,match='ARXIV_DOWNLOAD_FAILED'):
        import_paper(tmp_path,figure(),{f'/pdf/{IDENTIFIER}':404})
