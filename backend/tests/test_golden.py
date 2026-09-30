"""Independent acceptance oracle: PDF geometry versus the supplied reference.

Set PAPERDUET_GOLDEN_PDF or download with scripts/fetch-golden.ps1. Never use the
fixture as a parser input or production fallback. This is an external comparison.
"""
import os
from collections import Counter
from pathlib import Path

import pytest

from paperduet.adapter import adapt_fixture,plain
from paperduet.pdf_parser import PyMuPDFParser
from paperduet.validation import table_grid

ROOT=Path(__file__).resolve().parents[2]
PDF=Path(os.environ.get('PAPERDUET_GOLDEN_PDF',ROOT/'artifacts/m1/rex-omni.pdf'))


@pytest.fixture(scope='module')
def golden(tmp_path_factory):
    if not PDF.is_file():pytest.skip('Golden PDF unavailable; run scripts/fetch-golden.ps1')
    target=tmp_path_factory.mktemp('golden 한글')
    parser=PyMuPDFParser();metadata=parser.metadata(PDF)
    pages=[parser.page(PDF,i,target) for i in range(metadata['page_count'])]
    blocks,sources=parser.structure('golden',pages,metadata)
    return metadata,blocks,sources


@pytest.mark.sample
def test_ac3_all_48_pages_reference_sections_and_regions(golden):
    meta,blocks,sources=golden
    assert meta['page_count']==48
    assert max(b.page for b in blocks)==48
    counts=Counter(b.type for b in blocks if b.page<=31)
    assert counts['tab']==17 and counts['fig']==18 and counts['eq']>=5
    fixture=adapt_fixture(ROOT/'fixtures/rex-omni.blocks.json')
    expected=[b.n for b in fixture.blocks if b.type in {'sec','sub','ssub'}]
    actual=[b.n for b in blocks if b.page<=31 and b.type in {'sec','sub','ssub'}]
    assert actual==expected
    assert any(s['references'] for s in sources)
    assert all(not s['references'] for b,s in zip(blocks,sources) if b.page>=43)
    # The same term legitimately appears in the explanatory prose below Figure 5.
    # Check geometry, not an indiscriminate keyword ban.
    figure=next(s for b,s in zip(blocks,sources) if b.n=='Figure 5')
    from paperduet.pdf_parser import overlap
    assert all(not overlap(s['bbox'],figure['region']) for b,s in zip(blocks,sources) if b.page==8 and b.type in {'p','li'})


@pytest.mark.sample
def test_ac4_original_14_columns_all_scores_and_merged_headers(golden):
    _,blocks,_=golden
    table=next(b.table for b in blocks if b.n=='Table 2')
    grid,columns=table_grid(table)
    assert columns==14 and len(table.body)==18
    assert [c.rowspan for c in table.header[0]]==[2,2,2,2,1]
    assert table.header[0][-1].text_en=='COCO' and table.header[0][-1].colspan==10
    assert [r[0].rowspan for r in [table.body[0],table.body[8]]]==[7,10]
    reference=next(b.table for b in adapt_fixture(ROOT/'fixtures/rex-omni.blocks.json').blocks if b.n=='Table 2')
    ref,_=table_grid(reference)
    normalize=lambda value:plain(value).strip().replace('–','-').replace('−','-')
    reference_rows={normalize(ref[ri,1]):[normalize(ref[ri,c]) for c in range(3,13)]
                    for ri in range(len(reference.header)+len(reference.body)) if (ri,1) in ref}
    compared=0
    for ri in range(2,20):
        name=normalize(grid[ri,1])
        assert name in reference_rows,name
        assert [normalize(grid[ri,c]) for c in range(4,14)]==reference_rows[name],name
        compared+=10
    assert compared==180
    # Threshold column was omitted by the reference. Independently transcribed
    # from page 14, these values are retained in the original 14-column form.
    assert [grid[r,3] for r in range(2,20)]==['0.42','0.78','0.24','0.31','0.34','0.30','0.32','0.37']+['-']*10


def test_m2_golden_model_detection_and_table_decoration_preserve_all_cells(golden):
    from paperduet.table_style import decorate_tables
    _,original,_=golden
    blocks=[b.model_copy(deep=True) for b in original]
    before={b.id:b.table.model_dump(include={'header','body'}) for b in blocks if b.table}
    assert 'Rex-Omni' in decorate_tables(blocks)
    assert {b.id:b.table.model_dump(include={'header','body'}) for b in blocks if b.table}==before
    table=next(b.table for b in blocks if b.n=='Table 2')
    assert table.highlight_rows
    assert all(any('Rex-Omni' in c.text_en for c in table.body[r]) for r in table.highlight_rows)
    assert table.best_cells
    # Independent row/column oracle, including rowspans and score ties.
    grid,_=table_grid(table)
    score_max={c:max(float(grid[r,c]) for r in range(2,20) if grid[r,c]!='-') for c in range(4,14)}
    seen=set()
    for r,ci in table.best_cells:
        value=float(table.body[r][ci].text_en)
        matches=[c for c in range(4,14) if grid[r+2,c]==table.body[r][ci].text_en and value==score_max[c]]
        assert matches
        seen.update(matches)
    assert seen==set(range(4,14))
