"""PDF grids that are well-formed but wrong (merged or partial) are not trusted."""
import asyncio

from paperduet.models import Block, Cell, PipelineOptions, Table
from paperduet.pipeline import Pipeline
from paperduet.validation import table_grid
from m2_sample import seed


def row(*texts):
    return [Cell(text_en=t, numeric=t[:1].isdigit()) for t in texts]


# The VisualAD comparison table: only the two shaded columns got a grid, the other
# seven columns (21 rows of score tuples) landed in one cell.
TUPLES = ' '.join(f'({a}.1, {a}.2, {a}.3)' for a in range(10, 40))
MERGED = Table(header=[row('Methods', 'VisualAD(CLIP)', 'VisualAD(DINOv2)')],
               body=[[Cell(text_en='MVTec-AD ' + TUPLES), *row('(92.2, 93.2, 96.7)', '(90.1, 92.4, 94.8)')]]
                    + [row('VisA', '(84.7, 82.5, 87.6)', '(83.1, 81.4, 86.8)')])
GOOD = Table(header=[row('Method', 'AUROC', 'AP')],
             body=[row(f'M{i}', f'{80 + i}.1', f'{70 + i}.2') for i in range(8)])


def test_tables_are_not_judged_by_the_distribution_of_numbers():
    assert table_grid(MERGED)[1]==3
    assert table_grid(GOOD)[1]==3
    block=Block(id='t',doc_id='d',order=0,type='tab',section_path=[],table=MERGED,qa_flags=['V4'])
    assert block.qa_flags==[]


def test_restore_replaces_a_distrusted_grid_from_the_image(tmp_path):
    store, doc_id = seed(tmp_path)
    source = Block(id='t', doc_id=doc_id, order=0, type='tab', section_path=[], table=MERGED,
                   en='Methods A B MVTec-AD 91.5 92.5 VisA 81.5 82.5', image_path='documents/m2-paper/crops/figure.png')
    recovered = '<table><thead><tr><th>Methods</th><th>A</th><th>B</th></tr></thead><tbody><tr><td>MVTec-AD</td><td>91.5</td><td>92.5</td></tr><tr><td>VisA</td><td>81.5</td><td>82.5</td></tr></tbody></table>'
    class Restorer:
        async def json(self, *args): return {'html': recovered}, {'tokens_in': 1, 'tokens_out': 1}
    block = source.model_copy(deep=True)
    asyncio.run(Pipeline(store, Restorer()).restore_block(doc_id, block, source, PipelineOptions()))
    # Before, the new grid was compared cell by cell against the broken one and always rejected.
    assert block.qa_flags == [] and len(block.table.body) == 2 and len(block.table.header[0]) == 3


def test_a_table_that_cannot_be_restored_keeps_its_extraction_and_the_paper_goes_on(tmp_path):
    # 0.10.0 stopped the whole paper here, and every resume stopped at the same table again.
    store, doc_id = seed(tmp_path)
    source = Block(id='t', doc_id=doc_id, order=0, type='tab', section_path=[], table=GOOD,
                   en='Method AUROC AP', image_path='documents/m2-paper/crops/figure.png')
    calls = []
    class Unusable:
        async def json(self, *args):
            calls.append(args)  # a grid whose rows do not line up
            return {'html': '<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>'}, {'tokens_in': 1, 'tokens_out': 1}
    block = source.model_copy(deep=True)
    asyncio.run(Pipeline(store, Unusable()).restore_block(doc_id, block, source, PipelineOptions()))
    assert len(calls) == 2 and block.table == GOOD  # both attempts used, extraction kept, no error


def test_legacy_review_status_is_normalized_without_altering_table_content(tmp_path):
    store,doc_id=seed(tmp_path)
    doc=store.document(doc_id);table=next(b for b in doc.blocks if b.type=='tab')
    before=table.model_dump()
    with store.connect() as db:
        db.execute("UPDATE documents SET status='review' WHERE id=?",(doc_id,))
        db.execute("UPDATE jobs SET status='review',checkpoint=json_set(checkpoint,'$.review_count',99) WHERE doc_id=?",(doc_id,))
        db.execute("UPDATE blocks SET payload=json_set(payload,'$.qa_flags',json('[\"V4\"]')) WHERE doc_id=? AND id=?",(doc_id,table.id))
    asyncio.run(Pipeline(store,None).recover())
    assert next(b for b in store.document(doc_id).blocks if b.id==table.id).model_dump()==before
    assert store.job(doc_id)['status']=='complete'
    assert 'review_count' not in store.job(doc_id)['checkpoint']
