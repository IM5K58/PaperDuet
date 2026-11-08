"""Source fidelity regressions: no network or paid AI calls."""
import asyncio
import json

import pytest

from paperduet.adapter import inline, plain
from paperduet.models import Block, Cell, PipelineOptions, Table
from paperduet.pipeline import PAGE_CACHE, PARSER_REVISION, Pipeline
from paperduet.store import Store
from paperduet.validation import table_numeric_conflict, translation_complete, translation_preserves_content
from m2_sample import seed


@pytest.mark.parametrize('formula', [r'\(x<y\)', r'\[x<y\]', '$x<y$', '$$x<y$$', r'\(x&lt;y\)'])
def test_comparisons_and_following_sentence_survive_inline_cleanup(formula):
    source=f'조건 {formula}를 만족한다.'
    result=inline(source)
    assert plain(result)==source.replace('&lt;', '<')
    assert inline(result)==result


def test_inline_emphasis_breaks_and_security_are_preserved():
    result=inline('<strong>Result</strong><br><em>x</em><sup>2</sup><script>alert(1)</script>')
    assert result=='<b>Result</b><br/><i>x</i><sup>2</sup>'
    assert plain(result)=='Result\nx2'


@pytest.mark.parametrize('source',['<code>x<y</code> after','<code>x&lt;y</code> after',
    '<code><img src=x></code> after','<b>Result</b> x<y after','x<y <i>after</i>'])
def test_comparison_text_and_code_cannot_consume_following_text(source):
    result=inline(source)
    assert 'after' in plain(result)
    assert inline(result)==result
    assert '<img src=x>' not in result
    if source.startswith('<code>'):
        assert '</code> after' in result
    else:
        assert 'x<y' in plain(result)


def test_currency_and_literal_dollar_code_do_not_form_one_math_expression():
    source='Cost $5 <code>$HOME</code> and $10.'
    assert inline(source)==source
    assert translation_preserves_content(source,'비용은 $5 <code>$HOME</code> 및 $10이다.')


@pytest.mark.parametrize(('source','translated','expected'), [
    ('Score 42.0% on 100 examples.', '100개에서 정확도42.0%이다.', True),
    ('Score 42.0% on 100 examples.', '결과는 999%이다.', False),
    ('Scores 42 and 42.', '점수는 42이다.', False),
    (r'We use \(x<y\).', '부등식을 사용한다.', False),
    (r'We use \(x<y\).', r'\(x&lt;y\)를 사용한다.', True),
    ('The value is −2.5.', '값은 -2.5이다.', True),
    ('Score 42%.', '점수는 42이다.', False),
    ('Score 42 percent.', '점수는 42%이다.', True),
    ('Score 42%.', '점수는 42퍼센트이다.', True),
    ('Score 42 %.', '점수는 42%이다.', True),
    ('Score 42 percent.', '점수는 42이다.', False),
    ('We use x<sup>2</sup>.', 'x<sup>3</sup>을 사용한다.', False),
    ('We use H<sub>2</sub>O.', 'H<sub>3</sub>O를 사용한다.', False),
    ('We use H<sub>2</sub>O.', 'H<sub>2</sub>O를 사용한다.', True),
])
def test_ai_output_must_retain_source_numbers_and_formulas(source,translated,expected):
    assert translation_preserves_content(source,translated) is expected


def test_bad_translation_is_retried_without_mutating_source(tmp_path):
    store=Store(tmp_path,tmp_path/'absent.json')
    pipeline=Pipeline(store,None)
    source=Block(id='p',doc_id='d',order=0,type='p',section_path=[],en=r'42 samples satisfy \(x<y\).')
    block=source.model_copy(deep=True)
    unit=pipeline.translate_unit('d',[block],[],{'p':{'block':source}},PipelineOptions(),'test')
    first=next(unit)
    second=unit.send({'blocks':[{'id':'p','ko':'999개이다.'}]})
    assert block.ko is None and first.key!=second.key
    with pytest.raises(StopIteration):
        unit.send({'blocks':[{'id':'p','ko':r'42개가 \(x<y\)를 만족한다.'}]})
    assert translation_complete(block,source)
    assert plain(block.ko)==r'42개가 \(x<y\)를 만족한다.'
    assert source.ko is None


def test_repeated_bad_translation_stays_untranslated_and_evicts_cached_answer(tmp_path):
    store,doc_id=seed(tmp_path)
    pipeline=Pipeline(store,None)
    source=Block(id='p',doc_id=doc_id,order=0,type='p',section_path=[],en='42 samples.')
    block=source.model_copy(deep=True)
    unit=pipeline.translate_unit(doc_id,[block],[],{'p':{'block':source}},PipelineOptions(),'test')
    request=next(unit)
    invalid={'blocks':[{'id':'p','ko':'999개이다.'}]}
    for attempt in range(2):
        store.save_ai_result(doc_id,request.key,invalid)
        if attempt==0:
            previous=request
            request=unit.send(invalid)
            assert store.ai_result(doc_id,previous.key) is None
        else:
            with pytest.raises(StopIteration):unit.send(invalid)
            assert store.ai_result(doc_id,request.key) is None
    assert block.ko is None and not translation_complete(block,source)


def test_numeric_table_conflict_requires_comparable_geometry_and_rows():
    source=Table(header=[[Cell(text_en='Model'),Cell(text_en='Score')]],body=[[Cell(text_en='X'),Cell(text_en='42',numeric=True)]])
    recovered=source.model_copy(deep=True)
    recovered.body[0][1].text_en='999'
    assert table_numeric_conflict(source,recovered)
    recovered.body[0][0].text_en='Repaired row'
    assert not table_numeric_conflict(source,recovered)


@pytest.mark.parametrize(('before','after'),[('92.2 ± 0.3','99.9 ± 0.8'),
    ('(92.2, 93.2, 96.7)','(92.2, 99.9, 96.7)'),
    ('(92.2, 93.2, 96.7)','(96.7, 93.2, 92.2)'),('1e-3','1e-9'),('42%','42')])
def test_numeric_table_conflicts_cover_scientific_score_formats(before,after):
    source=Table(header=[[Cell(text_en='Method'),Cell(text_en='Score')]],
                 body=[[Cell(text_en='X'),Cell(text_en=before)]])
    recovered=source.model_copy(deep=True)
    assert not table_numeric_conflict(source,recovered)
    recovered.body[0][1].text_en=after
    assert table_numeric_conflict(source,recovered)


@pytest.mark.parametrize('invalid',[{'blocks':[]},{'unexpected':True}])
def test_missing_or_malformed_answers_are_not_replayed_forever(tmp_path,invalid):
    store,doc_id=seed(tmp_path)
    pipeline=Pipeline(store,None)
    source=Block(id='p',doc_id=doc_id,order=0,type='p',section_path=[],en='A source sentence.')
    block=source.model_copy(deep=True)
    unit=pipeline.translate_unit(doc_id,[block],[],{'p':{'block':source}},PipelineOptions(),'test')
    request=next(unit)
    store.save_ai_result(doc_id,request.key,invalid)
    unit.send(invalid)
    assert store.ai_result(doc_id,request.key) is None
    assert block.ko is None


def test_regeneration_does_not_erase_an_existing_translation_with_an_empty_answer(tmp_path):
    store=Store(tmp_path,tmp_path/'absent.json')
    pipeline=Pipeline(store,None)
    source=Block(id='p',doc_id='d',order=0,type='p',section_path=[],en='A source sentence.')
    block=source.model_copy(update={'ko':'기존 번역이다.'})
    unit=pipeline.translate_unit('d',[block],[],{'p':{'block':source}},PipelineOptions(),'test')
    next(unit)
    with pytest.raises(StopIteration):unit.send({'blocks':[{'id':'p','ko':''}]})
    assert block.ko=='기존 번역이다.'


def test_only_untouched_previews_get_new_extraction_and_new_page_cache(tmp_path):
    store,doc_id=seed(tmp_path)
    with store.connect() as db:
        db.execute("INSERT INTO documents(id,title,status,source_kind) VALUES('preview','Preview','awaiting_ai','pdf')")
        db.execute('INSERT INTO jobs(id,doc_id,stage,status,checkpoint) VALUES(?,?,?,?,?)',
                   ('preview','preview','Glossary','awaiting_ai',json.dumps({'parser_revision':3,'completed_stages':['Ingest','Extract','Structure']})))
    with store.connect() as db:
        before=[tuple(row) for row in db.execute('SELECT id,payload FROM blocks WHERE doc_id=?',(doc_id,))]
    pipeline=Pipeline(store,None)
    started=[]
    pipeline.start=started.append
    asyncio.run(pipeline.recover())
    checkpoint=store.job('preview')['checkpoint']
    assert checkpoint['parser_revision']==PARSER_REVISION==4
    assert checkpoint['reextract'] and checkpoint['completed_stages']==['Ingest']
    assert PAGE_CACHE=='pages-v4' and 'preview' in started
    with store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT id,payload FROM blocks WHERE doc_id=?',(doc_id,))]==before
