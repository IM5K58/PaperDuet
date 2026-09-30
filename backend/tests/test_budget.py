"""Token budget regression gate for one full paper (see token_audit.py).

The ceiling ratchets down as each optimisation phase lands; the product goal
is TARGET_TOKENS for a 48-page paper on every provider.
"""
import asyncio
import json

import pytest

from paperduet.cli_provider import CLI_ROLE, CLIProvider
from paperduet.annotations import glossary_candidates
from paperduet.pipeline import glossary_for
from paperduet.providers import APIProvider
from token_audit import SAMPLE_PDF, format_report, run_audit

GLOSSARY = [{'term': 'Transformer', 'ko': '트랜스포머', 'keep_english': True, 'definition_ko': '어텐션 기반 모델.'},
            {'term': 'attention', 'ko': '어텐션', 'keep_english': False, 'definition_ko': '가중 합.'},
            {'term': 'BLEU', 'ko': 'BLEU', 'keep_english': True, 'definition_ko': '번역 평가 지표.'}]


def test_glossary_is_filtered_per_batch_and_definitions_only_where_requested():
    texts = ['The <b>Transformer</b> is fast.', None, 'Attentive readers']
    assert glossary_for(GLOSSARY, texts) == [{'term': 'Transformer', 'ko': '트랜스포머', 'keep_english': True}]
    texts.append('We apply self-attention twice.')
    assert glossary_for(GLOSSARY, texts, {'attention'}) == [
        {'term': 'Transformer', 'ko': '트랜스포머', 'keep_english': True},
        {'term': 'attention', 'ko': '어텐션', 'definition_ko': '가중 합.'}]
    assert glossary_for(GLOSSARY, []) == []


@pytest.mark.parametrize('provider,model,effort,expected', [
    ('anthropic', 'claude-opus-5-5', 'low', ['--system-prompt', CLI_ROLE, '--effort', 'low']),
    ('anthropic', 'claude-haiku-4-5', 'low', ['--system-prompt', CLI_ROLE]),
    ('anthropic', 'sonnet', None, ['--system-prompt', CLI_ROLE]),
    ('openai', 'gpt-6-luna', 'low', ['-c', 'model_reasoning_effort="low"']),
])
def test_cli_replaces_agent_prompt_and_lowers_effort(monkeypatch, provider, model, effort, expected):
    import paperduet.cli_provider as cli
    commands = []
    monkeypatch.setattr(cli, 'resolve_cli', lambda *args: ['official.exe'])
    async def fake(command, data, cwd, timeout=600):
        if '--help' in command:
            yield ('--tools --strict-mcp-config --setting-sources --no-session-persistence --system-prompt --effort '
                   '--ignore-user-config --ignore-rules --ephemeral'); return
        commands.append(command)
        if provider == 'anthropic':
            yield json.dumps({'type': 'result', 'result': '{}', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        else:
            yield json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{}'}})
            yield json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
    monkeypatch.setattr(cli, 'execute', fake)
    async def run():
        return [c async for c in CLIProvider(provider).stream(model, 'rules', [{'role': 'user', 'content': 'x'}], effort=effort)]
    asyncio.run(run())
    command = commands[0]
    assert ' \x00 '.join(expected) in ' \x00 '.join(command)
    if 'haiku' in model or effort is None:
        assert '--effort' not in command


def test_cli_skips_flags_an_old_cli_does_not_know(monkeypatch):
    import paperduet.cli_provider as cli
    commands = []
    monkeypatch.setattr(cli, 'resolve_cli', lambda *args: ['official.exe'])
    async def fake(command, data, cwd, timeout=600):
        if '--help' in command:
            yield '--tools --strict-mcp-config --setting-sources --no-session-persistence'; return
        commands.append(command)
        yield json.dumps({'type': 'result', 'result': '{}', 'usage': {}})
    monkeypatch.setattr(cli, 'execute', fake)
    async def run():
        return [c async for c in CLIProvider('anthropic').stream('opus', 'rules', [{'role': 'user', 'content': 'x'}], effort='low')]
    asyncio.run(run())
    assert '--system-prompt' not in commands[0] and '--effort' not in commands[0]


@pytest.mark.parametrize('provider,model,field,value', [
    ('anthropic', 'claude-opus-5-5', 'output_config', {'effort': 'low'}),
    ('anthropic', 'claude-sonnet-4-6', 'output_config', {'effort': 'low'}),
    ('anthropic', 'claude-haiku-4-5', 'output_config', None),
    ('openai', 'gpt-6-sol', 'reasoning', {'effort': 'low'}),
    ('openai', 'gpt-4.1', 'reasoning', None),
])
def test_api_requests_carry_stage_effort_only_where_supported(provider, model, field, value):
    _, body = APIProvider(provider, None, None).body(model, 'rules', [{'role': 'user', 'content': 'x'}], None, 'low')
    assert body.get(field) == value


def test_glossary_candidates_find_acronyms_and_phrases_with_counts():
    texts = ['We train <b>Rex-Omni</b> on COCO with next point prediction.',
             'Next point prediction beats DINO on COCO.', None, 'The model uses next point prediction.']
    found = dict(map(tuple, glossary_candidates(texts)))
    assert found['next point prediction'] == 3 and found['COCO'] == 2 and found['Rex-Omni'] == 1
    assert 'the model' not in {t.casefold() for t in found}  # stop words never start a phrase


def test_annotation_batches_follow_top_level_sections_only():
    from paperduet.annotations import section_batches
    from paperduet.models import Block
    blocks = [Block(id=f'b{i}', doc_id='d', order=i, type='p', section_path=path, en='text')
              for i, path in enumerate([['1'], ['1', '1.1'], ['1', '1.2'], ['2'], ['2', '2.1']])]
    sources = {b.id: {'references': False} for b in blocks}
    assert [[b.id for b in batch] for _, batch in section_batches(blocks, sources)] == [['b0', 'b1', 'b2'], ['b3', 'b4']]


def test_translate_keeps_valid_blocks_from_a_partial_batch(tmp_path):
    from m2_sample import seed
    from paperduet.models import PipelineOptions
    from paperduet.pipeline import Pipeline
    store, doc_id = seed(tmp_path)
    doc = store.document(doc_id); sources = store.sources(doc_id)
    blocks = [b.model_copy(update={'ko': None}) for b in doc.blocks if b.id in {'b0001', 'b0002'}]
    ko = {'b0001': 'Nova를 제안하고 42.0을 보고한다. Table 1 참조.', 'b0002': '결과'}
    class Partial:
        calls = []
        def connected(self): return True
        async def json(self, stage, model, payload, image=None):
            ids = [b['id'] for b in payload['blocks']]
            self.calls.append(ids)
            # First answer drops a block and invents one; only valid IDs are kept.
            answer = [{'id': i, 'ko': ko[i]} for i in ids[:1]] + [{'id': 'b9999', 'ko': '없는 블록'}]
            return {'blocks': answer if len(self.calls) == 1 else [{'id': i, 'ko': ko[i]} for i in ids]}, {}
    provider = Partial()
    asyncio.run(Pipeline(store, provider).translate(doc_id, blocks, [], sources, PipelineOptions()))
    assert provider.calls == [['b0001', 'b0002'], ['b0002']]
    assert [b.ko for b in blocks] == [ko['b0001'], ko['b0002']]


def test_translate_halves_a_batch_that_overflows_the_output_cap(tmp_path):
    from m2_sample import seed
    from paperduet.models import PipelineOptions
    from paperduet.pipeline import Pipeline
    from paperduet.provider import ProviderError
    store, doc_id = seed(tmp_path)
    doc = store.document(doc_id); sources = store.sources(doc_id)
    blocks = [b.model_copy(update={'ko': None}) for b in doc.blocks if b.id in {'b0001', 'b0002'}]
    ko = {'b0001': 'Nova를 제안하고 42.0을 보고한다. Table 1 참조.', 'b0002': '결과'}
    class Overflow:
        calls = []
        def connected(self): return True
        async def json(self, stage, model, payload, image=None):
            ids = [b['id'] for b in payload['blocks']]
            self.calls.append(ids)
            if len(ids) > 1: raise ProviderError('AI_OUTPUT_LIMIT', usage={'tokens_in': 5, 'tokens_out': 16000})
            return {'blocks': [{'id': i, 'ko': ko[i]} for i in ids]}, {}
    provider = Overflow()
    asyncio.run(Pipeline(store, provider).translate(doc_id, blocks, [], sources, PipelineOptions()))
    # Without the split this batch would pause the job on every resume.
    assert provider.calls == [['b0001', 'b0002'], ['b0001'], ['b0002']]
    assert [b.ko for b in blocks] == [ko['b0001'], ko['b0002']]


def test_glossary_keeps_usable_entries_instead_of_failing_the_job():
    from paperduet.pipeline import parse_glossary
    entry = {'term': 'Nova', 'ko': '노바', 'keep_english': True, 'definition_ko': '제안 모델.'}
    answer = {'glossary': [entry, {**entry, 'term': 'nova'}, {'term': '', 'ko': 'x'}, 'noise',
                           {'term': 'point', 'ko': '점', 'keep_english': False, 'definition_ko': '좌표.'}]}
    assert [g.term for g in parse_glossary(answer)] == ['Nova', 'point']
    assert parse_glossary([{**entry, 'term': f't{i}'} for i in range(90)])[-1].term == 't79'
    assert parse_glossary('not json') == []


def test_gemini_three_gets_thinking_level():
    _, body = APIProvider('google', None, None).body('gemini-3.8-flash', 'rules', [{'role': 'user', 'content': 'x'}], None, 'low')
    assert body['generationConfig']['thinkingConfig'] == {'thinkingLevel': 'low'}

TARGET_TOKENS = 200_000
# Phase 0 baseline (2026-09-28): 179 calls, ~1.22M tokens before CLI overhead
# and thinking. Phase 1 (filtered glossary, slim annotation context, parsed
# tables skip image restore): 118 calls, ~307k. Phase 2 (12k-char translate
# batches, one annotation call per top-level section, candidate-based
# glossary): 36 calls, ~206k. Lower with every phase; never raise.
CEILING_TOKENS = 215_000
CEILING_CALLS = 40


def test_full_paper_token_budget(tmp_path):
    report = run_audit(SAMPLE_PDF, tmp_path)
    print('\n' + format_report(report))
    assert report['status'] in {'complete', 'review'}
    assert report['pages'] == 48
    total = report['tokens_in'] + report['tokens_out']
    assert total <= CEILING_TOKENS, f'{total:,} tokens regressed past the {CEILING_TOKENS:,} ceiling'
    assert report['calls'] <= CEILING_CALLS
    # Every AI call is persisted with its size so real runs can be audited too.
    recorded = report['recorded']
    assert recorded['calls'] == report['calls']
    assert (recorded['tokens_in'], recorded['tokens_out']) == (report['tokens_in'], report['tokens_out'])
    assert recorded['min_payload'] > 0
