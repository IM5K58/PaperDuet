import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.provider import ProviderError
from m2_sample import FIXTURE, seed


class Vault:
    def get(self): return 'test-key'


class SummaryDouble:
    """Answers the two summary stages; each answer mixes valid and invalid evidence."""
    def __init__(self): self.calls = []; self.fail_paper = False

    def connected(self): return True

    async def json(self, stage, model, payload, image=None):
        self.calls.append((stage, payload))
        await asyncio.sleep(0)
        if stage == 'summary_section':
            ids = [b['id'] for b in payload['blocks']]
            return {'role': '핵심 결과', 'summary': f"{payload['section']} 요약",
                    'claims': [{'text': 'Nova는 42.0을 보고한다.', 'refs': ids[:2] + ['b9999']},
                               {'text': '참고문헌만 근거인 주장', 'refs': ['b0007']}],
                    'visuals': [{'block_id': i, 'why': '근거 그림'} for i in ids]}, {'tokens_in': 10, 'tokens_out': 5}
        assert stage == 'summary_paper'
        if self.fail_paper:
            raise ProviderError('AI_OVERLOADED')
        firsts = [s['first_block_id'] for s in payload['sections']]
        return {'structured': {
                    'problem': [{'text': 'Nova가 해결하려는 문제', 'refs': ['b0001']}],
                    'gap': [{'text': '참고문헌만 근거', 'refs': ['b0007']}],
                    'method': [{'text': '근거 없는 방법 설명', 'refs': []}],
                    'results': [{'text': 'Nova는 42.0을 기록했다.', 'refs': ['b0003']},
                                {'text': 'Nova는 99.9를 기록했다.', 'refs': ['b0003']}],
                    'limits': []},
                'flow': [{'section': s['section'], 'role': s['role'], 'summary': '한 줄', 'why_next': '다음으로', 'block_id': f}
                         for s, f in zip(payload['sections'], firsts)] + [{'section': '없는 섹션', 'block_id': 'b9999'}],
                'visuals': [{'block_id': 'b0003', 'why': '결과 표'}, {'block_id': 'b0001', 'why': '그림 아님'}]}, {'tokens_in': 20, 'tokens_out': 9}


@pytest.fixture
def api(tmp_path):
    seed(tmp_path)
    provider = SummaryDouble()
    app = create_app('x' * 64, tmp_path, FIXTURE, vault=Vault(), provider=provider)
    with TestClient(app, headers={'Authorization': 'Bearer ' + 'x' * 64}) as client:
        yield client, provider, app.state.store


def wait(client, status, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = client.get('/documents/m2-paper/summary').json()
        if state['status'] in status:
            return state
        time.sleep(.02)
    raise AssertionError(state)


def test_summary_is_made_on_request_with_checked_evidence(api):
    client, provider, store = api
    assert client.get('/documents/m2-paper/summary').json()['status'] == 'none'
    estimate = client.get('/documents/m2-paper/summary/estimate').json()
    assert estimate['calls'] == 3 and estimate['input_tokens'] > 0  # Abstract, section 1, synthesis
    assert client.post('/documents/m2-paper/summary', json={}).status_code == 202
    state = wait(client, {'ready', 'failed'})
    assert state['status'] == 'ready' and state['progress'] == 1 and not state['edited']
    stages = [s for s, _ in provider.calls]
    assert stages == ['summary_section', 'summary_section', 'summary_paper']
    # References never reach the model as evidence; the synthesis sees digests, not the paper.
    assert all(b['id'] not in {'b0006', 'b0007'} for _, p in provider.calls[:2] for b in p['blocks'])
    paper = provider.calls[2][1]
    assert 'blocks' not in paper and [s['first_block_id'] for s in paper['sections']] == ['b0000', 'b0002']
    assert all(c['refs'] != ['b0007'] and 'b9999' not in c['refs'] for s in paper['sections'] for c in s['claims'])
    s = state['payload']['structured']
    assert s['problem'] == [{'text': 'Nova가 해결하려는 문제', 'refs': ['b0001'], 'flags': []}]
    assert s['gap'] == [] and s['method'] == []  # reference-only or unsupported points are dropped
    assert [p['flags'] for p in s['results']] == [[], ['V5']]  # 99.9 is not in Table 1
    flow = state['payload']['flow']
    assert [f['block_id'] for f in flow] == ['b0000', 'b0002', None]
    assert state['payload']['visuals'] == [{'block_id': 'b0003', 'why': '결과 표'}]
    usage = {u['stage'] for u in store.job('m2-paper')['usage']}
    assert {'summary_section', 'summary_paper'} <= usage
    # Asking again returns the saved summary without new calls.
    client.post('/documents/m2-paper/summary', json={})
    assert len(provider.calls) == 3


def test_failed_summary_resumes_with_saved_answers_and_regenerate_asks_afresh(api):
    client, provider, _ = api
    provider.fail_paper = True
    client.post('/documents/m2-paper/summary', json={})
    state = wait(client, {'failed'})
    assert state['error'] == 'AI_OVERLOADED' and len(provider.calls) == 3
    provider.fail_paper = False
    client.post('/documents/m2-paper/summary', json={})
    assert wait(client, {'ready'})['status'] == 'ready'
    # The two section digests were already paid for; only the synthesis is asked again.
    assert [s for s, _ in provider.calls[3:]] == ['summary_paper']
    client.post('/documents/m2-paper/summary', json={'regenerate': True})
    wait(client, {'ready'})
    assert [s for s, _ in provider.calls[4:]] == ['summary_section', 'summary_section', 'summary_paper']


def test_edits_are_saved_checked_and_marked(api):
    client, _, _ = api
    assert client.put('/documents/m2-paper/summary', json={}).status_code == 404  # nothing to edit yet
    client.post('/documents/m2-paper/summary', json={})
    payload = wait(client, {'ready'})['payload']
    point = payload['structured']['results'][1]
    point.update(text='Nova는 42.0으로 가장 높다.', edited=True)
    payload['structured']['limits'] = [{'text': '직접 쓴 한계', 'refs': ['b0001', 'b0007', 'b9999'], 'edited': True}]
    payload['visuals'].append({'block_id': 'b0001', 'why': '표가 아님'})
    assert client.put('/documents/m2-paper/summary', json=payload).status_code == 204
    state = client.get('/documents/m2-paper/summary').json()
    assert state['edited']
    assert state['payload']['structured']['results'][1] == {'text': 'Nova는 42.0으로 가장 높다.', 'refs': ['b0003'], 'flags': [], 'edited': True}
    assert state['payload']['structured']['limits'][0]['refs'] == ['b0001']
    assert [v['block_id'] for v in state['payload']['visuals']] == ['b0003']
    assert client.put('/documents/m2-paper/summary', json={'structured': {'problem': [{'text': ''}]}}).status_code == 422


def test_summary_needs_an_extracted_paper_and_goes_with_it(api):
    client, _, store = api
    assert client.get('/documents/nope/summary').status_code == 404
    with store.connect() as db:
        db.execute("INSERT INTO documents(id,title,status) VALUES('empty','Empty','queued')")
    assert client.post('/documents/empty/summary', json={}).json()['detail'] == 'DOCUMENT_NOT_READY'
    client.post('/documents/m2-paper/summary', json={})
    wait(client, {'ready'})
    assert client.delete('/documents/m2-paper').status_code == 204
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM paper_summaries').fetchone()[0] == 0


def test_a_summary_point_can_anchor_an_ask_question(api):
    from paperduet.ask import Anchor, AskInput, AskSettings, build_context
    _, _, store = api
    anchor = Anchor(block_id='b0001', field='summary', text='Nova는 42.0을 보고한다.')
    context, _ = build_context(store, AskInput(doc_id='m2-paper', anchor=anchor, question='왜?'), AskSettings())
    assert context['selection']['field'] == 'summary' and context['block']['id'] == 'b0001'
