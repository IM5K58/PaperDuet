"""Document export retains scientific notation without retaining unsafe HTML."""
from bs4 import BeautifulSoup

from paperduet.exports import block_markdown, markdown_export, table_html
from paperduet.models import Block, Card, Cell, Document, Table


def block(**values):
    return Block(id='b0001', doc_id='paper', order=0, type='p', section_path=[], **values)


def test_markdown_preserves_scientific_inline_formatting_in_text_and_captions():
    source = 'O(n<sup>2</sup>), H<sub>2</sub>O, <b>best</b>, <i>variable</i>, <code>model_name</code><br>next line'
    result = block_markdown(block(en=source, ko=source, caption_en=source, caption_ko=source))
    for markup in ['<sup>2</sup>','<sub>2</sub>','<b>best</b>','<i>variable</i>','<code>model\\_name</code>','<br/>next line']:
        assert result.count(markup)==4
    heading = Block(id='h', doc_id='paper', order=1, type='sec', section_path=[], en='H<sub>2</sub>O', ko='x<sup>2</sup>')
    assert 'H<sub>2</sub>O' in block_markdown(heading)
    assert 'x<sup>2</sup>' in block_markdown(heading)


def test_ocr_source_remains_identified_when_exported_with_its_translation():
    recognized=block(en='The recognized result is 42.',ko='인식한 결과는 42입니다.',ocr=True)
    result=block_markdown(recognized)
    assert '**OCR로 읽은 원문**' in result and '원본 PDF와 대조하세요.' in result
    assert recognized.en in result and recognized.ko in result
    assert 'OCR로 읽은 원문' not in block_markdown(block(en='Native PDF text.'))
    previous=recognized.model_copy(update={'superseded_by':'replacement'})
    retained=block_markdown(previous)
    assert retained.count('**OCR로 읽은 원문**')==1 and '이전 원문 · 기존 번역' in retained


def test_table_export_preserves_spans_and_notation_but_removes_executable_html():
    table = Table(header=[[Cell(text_en='<b onclick="bad()">H<sub>2</sub>O</b>', colspan=2, is_header=True)]],
                  body=[[Cell(text_en='x<sup>2</sup><script>bad()</script>', text_ko='<i style="color:red">제곱</i>', rowspan=2)]])
    result = table_html(table)
    soup = BeautifulSoup(result, 'html.parser')
    assert soup.th['colspan'] == '2' and soup.td['rowspan'] == '2'
    assert soup.th.select_one('b sub').get_text() == '2'
    assert soup.td.select_one('sup').get_text() == '2'
    assert soup.td.select_one('i').get_text() == '제곱'
    assert not soup.select('script,[onclick],[style]')
    assert 'bad()' not in result


def test_markdown_sanitizes_text_while_preserving_encoded_symbols_and_math():
    source = r'Safe <b onclick="bad()">bold</b> \(x<y\) &amp; \(a&lt;b\) <img src=x onerror="bad()"><script>bad()</script>'
    result = block_markdown(block(en=source))
    assert '<b>bold</b>' in result
    assert result.count(r'$x\textless{}y$')==1 and r'$a\textless{}b$' in result
    assert '&amp;lt;' not in result
    assert not BeautifulSoup(result, 'html.parser').select('img,script,[onclick],[onerror]')


def test_markdown_document_title_keeps_notation_and_sanitizes_korean_title():
    doc = Document(id='paper', title='H<sub>2</sub>O', title_ko='<b onclick="bad()">물</b><script>bad()</script>',
                   arxiv_id='', status='complete', blocks=[])
    result = markdown_export(None, doc)
    assert result.startswith('# H<sub>2</sub>O\n\n<b>물</b>')
    assert 'bad()' not in result


def test_markdown_unverified_restoration_uses_original_image_and_labels_extraction(tmp_path):
    image=tmp_path/'original.png'
    image.write_bytes(b'original image bytes')
    class ImageStore:
        def asset_path(self, doc_id, path):
            return image
    table=Block(id='table', doc_id='paper', order=0, type='tab', section_path=[], en='Extracted 42',
                caption_en='Scores', restore_error='numeric_mismatch', image_path='original.png',
                table=Table(header=[], body=[[Cell(text_en='999')]]))
    equation=Block(id='equation', doc_id='paper', order=1, type='eq', section_path=[], en='Extracted x=42',
                   latex='x=999', restore_error='invalid', image_path='original.png')
    doc=Document(id='paper', title='Paper', title_ko='', arxiv_id='', status='complete', blocks=[table,equation])
    result=markdown_export(ImageStore(),doc)
    assert '<table>' not in result and '$$' not in result and '999' not in result
    assert result.count('원본 이미지를 확인하세요.')==2
    assert '**추출 원문**\n\nExtracted 42' in result
    assert '**추출 원문**\n\nExtracted x=42' in result
    assert result.count('data:image/png;base64,')==2


def test_markdown_unverified_restoration_does_not_promise_an_absent_image():
    result=block_markdown(Block(id='equation', doc_id='paper', order=0, type='eq', section_path=[], en='x=42',
                               latex='x=999', restore_error='invalid'))
    assert '추출한 원문을 표시합니다.' in result
    assert '원본 이미지를 확인하세요.' not in result
    assert '999' not in result


def test_markdown_literal_punctuation_and_code_are_not_reinterpreted():
    result=block_markdown(block(en=r'1. model_name *literal* [label](value) `ticks` <code>\(x<y\) and $z$</code>'))
    assert r'1\. model\_name \*literal\* \[label\]\(value\) \`ticks\`' in result
    assert r'<code>\\\(x&lt;y\\\) and \$z\$</code>' in result


def test_markdown_supports_delimiters_and_keeps_math_html_inert():
    result=block_markdown(block(en=r'\(x<y\) $a^2$ \[z>0\] $$w=1$$ \(\text{<script>alert(1)</script>}\)'))
    assert r'$x\textless{}y$' in result and '$a^2$' in result
    assert '$$\nz\\textgreater{}0\n$$' in result and '$$\nw=1\n$$' in result
    assert '<script>' not in result and '</script>' not in result
    assert r'\textless{}script\textgreater{}' in result


def test_markdown_fenced_code_does_not_change_backticks_in_the_source():
    body='before\n```\na_b * c\n```\nafter'
    result=block_markdown(Block(id='card',doc_id='paper',order=0,type='card',section_path=[],card=Card(body=body,explain_ko='')))
    assert result.rstrip('\n')=='````text\n'+body+'\n````'
