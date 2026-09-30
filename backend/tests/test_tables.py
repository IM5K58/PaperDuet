"""PDF grids that are well-formed but wrong (merged or partial) are not trusted."""
import asyncio

from paperduet.models import Block, Cell, PipelineOptions, Table
from paperduet.pipeline import Pipeline
from paperduet.validation import suspicious_table, validate_block
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


def test_merged_or_partial_grids_are_distrusted_and_normal_ones_are_not():
    assert suspicious_table(MERGED)
    assert not suspicious_table(GOOD)
    region = ' '.join(f'{90 + i}.5' for i in range(40))  # the page shows far more numbers than the grid holds
    assert suspicious_table(GOOD, region) and not suspicious_table(GOOD, ' '.join(['81.1'] * 16))
    block = Block(id='t', doc_id='d', order=0, type='tab', section_path=[], table=MERGED)
    assert 'V4' in validate_block(block, block, [])


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


def test_papers_processed_earlier_get_their_broken_tables_flagged(tmp_path):
    store, doc_id = seed(tmp_path)
    doc = store.document(doc_id)
    table = next(b for b in doc.blocks if b.type == 'tab')
    table.table = MERGED
    store.save_blocks(doc_id, [table])
    pipeline = Pipeline(store, None)
    pipeline.flag_suspicious_tables(doc_id)
    pipeline.flag_suspicious_tables(doc_id)  # idempotent
    flagged = next(b for b in store.document(doc_id).blocks if b.id == table.id)
    assert flagged.qa_flags.count('V4') == 1
    assert store.job(doc_id)['status'] == 'review'
