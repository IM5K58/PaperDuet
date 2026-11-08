"""Mixed imports stay local and preserve edits when a PDF snippet is repaired."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.arxiv import ArxivImporter
from paperduet.pdf_parser import PyMuPDFParser
from paperduet.store import Store


IDENTIFIER = '2603.07952v1'
TOKEN = 'v' * 64
CAPTION = 'Comparison of the original red and blue panels.'
HTML = f'''<article class="ltx_document">
<h1 class="ltx_title ltx_title_document">HTML title</h1>
<p id="p1">Clean HTML preserves <b>this emphasis</b> and the result 42.</p>
<figure id="fig1" class="ltx_figure">
<img class="ltx_graphics" src="{IDENTIFIER}/red.png">
<img class="ltx_graphics" src="{IDENTIFIER}/blue.png">
<figcaption><span class="ltx_tag ltx_tag_figure">Figure 1:</span> {CAPTION}</figcaption>
</figure>
<p id="p2">This paragraph follows the complete illustration.</p>
<figure id="fig2" class="ltx_figure">
<img class="ltx_graphics" src="{IDENTIFIER}/green.png">
<figcaption><span class="ltx_tag ltx_tag_figure">Figure 2:</span> The healthy HTML illustration.</figcaption>
</figure></article>'''


class OfflineVault:
    def get(self):
        return None


class NoAI:
    def connected(self):
        return False

    async def json(self, *_args, **_kwargs):
        raise AssertionError('Reanalysis must not call AI')


def imported_mixed_paper(directory):
    store = Store(directory, directory / 'absent-fixture.json')
    parser = PyMuPDFParser()
    with fitz.open() as pdf:
        page = pdf.new_page(width=500, height=700)
        page.insert_text((40, 65), 'Original PDF body text before the illustration.', fontsize=11)
        page.draw_rect(fitz.Rect(40, 100, 210, 200), color=(1, 0, 0), fill=(1, 0, 0))
        page.draw_rect(fitz.Rect(240, 100, 410, 200), color=(0, 0, 1), fill=(0, 0, 1))
        page.insert_text((50, 212), '(a) Original red panel', fontsize=8)
        page.insert_text((250, 212), '(b) Original blue panel', fontsize=8)
        page.insert_text((40, 240), 'Figure 1: ' + CAPTION, fontsize=9)
        page.insert_text((40, 295), 'Original PDF text after the complete illustration.', fontsize=11)
        page.draw_rect(fitz.Rect(40, 350, 210, 450), color=(0, 1, 0), fill=(0, 1, 0))
        page.insert_text((40, 485), 'Figure 2: The healthy HTML illustration.', fontsize=9)
        pdf_bytes = pdf.tobytes()
        green = page.get_pixmap(clip=fitz.Rect(40, 350, 210, 450)).tobytes('png')

    def handler(request):
        if request.url.path == f'/html/{IDENTIFIER}':
            return httpx.Response(200, text=HTML)
        if request.url.path == f'/pdf/{IDENTIFIER}':
            return httpx.Response(200, content=pdf_bytes)
        if request.url.path == f'/html/{IDENTIFIER}/green.png':
            return httpx.Response(200, content=green)
        raise AssertionError('Unsupported panels must be recovered locally from PDF')

    pipeline = SimpleNamespace(parser=parser, start=lambda _doc_id: pytest.fail('Mixed import must keep HTML'))
    result = asyncio.run(ArxivImporter(store, pipeline, httpx.MockTransport(handler)).ingest(IDENTIFIER))
    assert result['source_kind'] == 'mixed' and not result['fallback']
    return store, result['doc_id']


@pytest.mark.parametrize('previous_kind', ['mixed', 'pdf'])
def test_saved_html_repairs_only_broken_visual_offline_and_preserves_edits(tmp_path, monkeypatch, previous_kind):
    store, doc_id = imported_mixed_paper(tmp_path)
    doc = store.document(doc_id)
    figure = next(b for b in doc.blocks if b.n == 'Figure 1')
    healthy = next(b for b in doc.blocks if b.n == 'Figure 2')
    paragraph = next(b for b in doc.blocks if b.type == 'p')
    figure.caption_ko = '직접 고친 그림 설명입니다.'
    paragraph.ko = '직접 고친 본문 결과 42.'
    store.save_blocks(doc_id, [figure, paragraph])
    broken = store.asset_path(doc_id, figure.image_path)
    broken.write_bytes(b'broken original crop')
    healthy_path = store.asset_path(doc_id, healthy.image_path)
    healthy_bytes = healthy_path.read_bytes()
    with store.connect() as db:
        db.execute('UPDATE documents SET source_kind=? WHERE id=?', (previous_kind, doc_id))
        if previous_kind == 'pdf':
            checkpoint = store.job(doc_id)['checkpoint']
            checkpoint['source_fallback_reason'] = 'html_images'
            db.execute('UPDATE jobs SET checkpoint=? WHERE doc_id=?', (json.dumps(checkpoint), doc_id))

    def no_network(*_args, **_kwargs):
        raise AssertionError('Stored-source reanalysis must not download anything')

    monkeypatch.setattr('httpx.AsyncClient', no_network)
    app = create_app(TOKEN, tmp_path, tmp_path / 'absent-fixture.json', vault=OfflineVault(), provider=NoAI())
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        base = f'/documents/{doc_id}/reanalysis'
        preview_response = client.post(base)
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview['method'] == 'mixed'
        assert preview['counts']['added'] == preview['counts']['missing'] == 0
        change = next(c for c in preview['changes'] if c['old_block']['id'] == figure.id)
        assert change['kind'] == 'changed' and change['translation_kept'] and not change['requires_translation']
        assert change['new_block']['page'] == 1
        assert '/reanalysis/' in change['new_block']['image_path']
        assert store.block(doc_id, figure.id).image_path == figure.image_path
        assert broken.read_bytes() == b'broken original crop'
        assert store.block(doc_id, paragraph.id).ko == paragraph.ko
        assert next(c for c in preview['changes'] if c['old_block']['id'] == healthy.id)['kind'] == 'unchanged'

        applied = client.post(f"{base}/{preview['id']}/apply", json={'change_ids': [change['id']]})
        assert applied.status_code == 200, applied.text
        result = store.block(doc_id, figure.id)
        assert result.id == figure.id and result.caption_ko == figure.caption_ko and not result.superseded_by
        assert store.document(doc_id).source_kind == 'mixed'
        assert store.asset_path(doc_id, result.image_path).read_bytes().startswith(b'\x89PNG')
        assert healthy_path.read_bytes() == healthy_bytes
        assert store.block(doc_id, healthy.id).image_path == healthy.image_path
        assert store.block(doc_id, paragraph.id).ko == paragraph.ko
        provenance = store.sources(doc_id)[figure.id]
        assert provenance['origin'] == 'arxiv_pdf' and provenance['source_anchor'] == 'fig1'
        assert provenance['region'] and provenance['visual_fallback_reason'] == 'html_structure'
        checkpoint = store.job(doc_id)['checkpoint']
        assert not checkpoint.get('source_fallback_reason')
        assert checkpoint['visual_fallbacks'] == [{'block_id': figure.id, 'reason': 'html_structure', 'page': 1}]
        source = client.get(f'/documents/{doc_id}/blocks/{figure.id}/source').json()
        assert source['available'] and source['page'] == 1 and source['region'] and source['source_anchor'] == 'fig1'

        again = client.post(base).json()
        assert again['method'] == 'mixed' and not any(c['selectable'] for c in again['changes'])


@pytest.mark.parametrize('change', ['url', 'legacy_pdf'])
def test_unverified_html_image_cannot_reuse_the_previous_saved_crop(tmp_path, monkeypatch, change):
    store, doc_id = imported_mixed_paper(tmp_path)
    healthy = next(b for b in store.document(doc_id).blocks if b.n == 'Figure 2')
    folder = store.data_dir / 'documents' / doc_id
    if change == 'url':
        (folder / 'source.html').write_text(HTML.replace('/green.png', '/different.png'), encoding='utf-8')
    else:
        healthy.page = 1
        store.save_blocks(doc_id, [healthy])
        with store.connect() as db:
            source = store.sources(doc_id)[healthy.id]
            source.pop('block')
            source.pop('origin')
            source.pop('visual_source_urls')
            source.update(bbox=[40, 350, 100, 450], region=[40, 350, 100, 450])
            db.execute('UPDATE source_blocks SET payload=?,provenance=? WHERE doc_id=? AND block_id=?',
                       (healthy.model_dump_json(exclude_none=True), json.dumps(source), doc_id, healthy.id))

    def no_network(*_args, **_kwargs):
        raise AssertionError('Reanalysis must use the saved PDF for an unknown image URL')

    monkeypatch.setattr('httpx.AsyncClient', no_network)
    app = create_app(TOKEN, tmp_path, tmp_path / 'absent-fixture.json', vault=OfflineVault(), provider=NoAI())
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        response = client.post(f'/documents/{doc_id}/reanalysis')
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['method'] == 'mixed'
        change = next(c for c in preview['changes'] if c['old_block']['id'] == healthy.id)
        assert change['kind'] == 'changed' and change['new_block']['page'] == 1
        assert '/reanalysis/' in change['new_block']['image_path']
        assert store.block(doc_id, healthy.id).image_path == healthy.image_path


def test_legacy_versionless_saved_html_never_mixes_with_an_unverified_pdf_revision(tmp_path):
    store, doc_id = imported_mixed_paper(tmp_path)
    with store.connect() as db:
        checkpoint = store.job(doc_id)['checkpoint']
        checkpoint.pop('source_arxiv_id', None)
        db.execute('UPDATE jobs SET checkpoint=? WHERE doc_id=?', (json.dumps(checkpoint), doc_id))
        db.execute('UPDATE documents SET arxiv_id=?,source_url=? WHERE id=?',
                   ('2603.07952', 'https://arxiv.org/abs/2603.07952', doc_id))
    app = create_app(TOKEN, tmp_path, tmp_path / 'absent-fixture.json', vault=OfflineVault(), provider=NoAI())
    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        response = client.post(f'/documents/{doc_id}/reanalysis')
        assert response.status_code == 200, response.text
        assert response.json()['method'] == 'pdf'
        assert store.document(doc_id).source_kind == 'mixed'  # Preview leaves the saved paper alone.
