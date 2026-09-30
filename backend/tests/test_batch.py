"""Saving mode: pipeline stages run as provider batches at half price."""
import asyncio
import json
import secrets
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.models import PipelineOptions
from paperduet.provider import ProviderError
from paperduet.providers import APIProvider
from test_m1 import make_pdf, wait_job
from token_audit import FIXTURE, SAMPLE_PDF, AuditProvider, Vault

AUDIT = dict(glossary_model='audit', translate_model='audit', restore_model='audit', annotate_model='audit')


class BatchDouble(AuditProvider):
    """Audit answers behind a batch API. `world` is the provider's side and
    survives an app restart; a batch ends once `finish` is set or it is cancelled."""
    supports_batch = True

    def __init__(self, world, finish=True, expire=False):
        super().__init__()
        self.world, self.finish, self.expire = world, finish, expire
        self.submitted, self.cancelled, self.forgotten = [], [], []

    async def submit_batch(self, requests):
        batch_id = f'batch{len(self.world)}'
        self.world[batch_id] = {'requests': requests, 'cancelled': False}
        self.submitted.append(sorted(r[0] for r in requests))
        return {'provider': 'anthropic', 'jobs': [{'id': batch_id, 'keys': [r[0] for r in requests]}]}

    async def batch_done(self, handle):
        return self.finish or self.world[handle['jobs'][0]['id']]['cancelled']

    async def batch_results(self, handle):
        batch = self.world[handle['jobs'][0]['id']]
        results = {}
        for key, stage, model, payload, image in batch['requests']:
            if batch['cancelled'] or self.expire:
                results[key] = (ProviderError('BATCH_RETRY'), {})
            else:
                results[key] = await self.json(stage, model, payload, image)
        return results

    async def cancel_batch(self, handle):
        self.world[handle['jobs'][0]['id']]['cancelled'] = True
        self.cancelled.append(handle['jobs'][0]['id'])

    async def forget_batch(self, handle):
        self.forgotten.append(handle['jobs'][0]['id'])


def start(tmp_path, provider):
    token = secrets.token_hex(32)
    app = create_app(token, tmp_path, FIXTURE, vault=Vault(), provider=provider)
    app.state.pipeline.poll = (.01, .02, .01)
    provider.store = app.state.store
    return app, TestClient(app, headers={'Authorization': 'Bearer ' + token})


def upload(client, pdf):
    doc_id = client.post('/documents', content=pdf, headers={'Content-Type': 'application/pdf'}).json()['doc_id']
    wait_job(client, doc_id, {'ready_to_translate', 'awaiting_ai'}, timeout=120)
    return doc_id


def resume(client, doc_id, **options):
    return client.post(f'/documents/{doc_id}/resume', json=PipelineOptions(**AUDIT, **options).model_dump())


def settle(check, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not check():
        time.sleep(.02)
    return check()


def usage_rows(app, doc_id):
    with app.state.store.connect() as db:
        return [dict(r) for r in db.execute('SELECT stage,batch FROM ai_usage WHERE doc_id=?', (doc_id,))]


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason='48-page sample PDF is not in this checkout')
def test_saving_mode_runs_a_whole_paper_in_a_few_batches(tmp_path):
    provider = BatchDouble({})
    app, client = start(tmp_path, provider)
    with client:
        doc_id = upload(client, SAMPLE_PDF.read_bytes())
        assert resume(client, doc_id, batch=True).status_code == 202
        job = wait_job(client, doc_id, {'complete', 'review', 'failed', 'paused'}, timeout=300)
        assert job['status'] in {'complete', 'review'}, job['checkpoint'].get('error')
        # Images + glossary, translation, annotation (+ repairs): not ~36 waits.
        assert len(provider.submitted) <= 5
        assert sum(map(len, provider.submitted)) == len(provider.calls)
        rows = usage_rows(app, doc_id)
        assert len(rows) == len(provider.calls) and all(r['batch'] == 1 for r in rows)
        assert sorted(provider.forgotten) == sorted(provider.world)  # the provider's copy is deleted
        # The choice was for this paper only; the next one starts immediate.
        assert client.get('/settings/providers').json()['options']['batch'] is False
        with app.state.store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM ai_results').fetchone()[0] == 0
        assert job['checkpoint'].get('pending_batch') is None


def test_restart_attaches_to_the_submitted_batch_instead_of_paying_twice(tmp_path):
    world = {}
    first = BatchDouble(world, finish=False)
    app, client = start(tmp_path, first)
    with client:
        doc_id = upload(client, make_pdf(3))
        resume(client, doc_id, batch=True)
        job = wait_job(client, doc_id, {'batch_waiting'})
        assert job['checkpoint']['pending_batch']['count'] == len(first.submitted[0])
    second = BatchDouble(world, finish=True)
    app, client = start(tmp_path, second)
    with client:  # startup recovery picks the job up again
        job = wait_job(client, doc_id, {'complete', 'review', 'failed', 'paused'}, timeout=60)
        assert job['status'] in {'complete', 'review'}, job['checkpoint'].get('error')
    assert first.submitted[0] not in second.submitted
    assert not first.cancelled and not second.cancelled


def test_switching_to_immediate_cancels_the_batch_and_finishes_the_rest_now(tmp_path):
    provider = BatchDouble({}, finish=False)
    app, client = start(tmp_path, provider)
    with client:
        doc_id = upload(client, make_pdf(3))
        resume(client, doc_id, batch=True)
        wait_job(client, doc_id, {'batch_waiting'})
        assert client.post(f'/documents/{doc_id}/realtime').status_code == 204
        job = wait_job(client, doc_id, {'complete', 'review', 'failed', 'paused'}, timeout=60)
        assert job['status'] in {'complete', 'review'}, job['checkpoint'].get('error')
        assert provider.cancelled and job['checkpoint']['options']['batch'] is False
        assert {r['batch'] for r in usage_rows(app, doc_id)} == {0}
        assert client.post(f'/documents/{doc_id}/realtime').status_code == 409


def test_pause_cancels_the_batch_and_a_later_resume_starts_clean(tmp_path):
    provider = BatchDouble({}, finish=False)
    app, client = start(tmp_path, provider)
    with client:
        doc_id = upload(client, make_pdf(3))
        resume(client, doc_id, batch=True)
        wait_job(client, doc_id, {'batch_waiting'})
        client.post(f'/documents/{doc_id}/pause')
        wait_job(client, doc_id, {'paused'})
        assert settle(lambda: provider.cancelled and client.get(f'/documents/{doc_id}/job').json()['checkpoint'].get('pending_batch') is None)
        provider.finish = True
        assert settle(lambda: resume(client, doc_id).status_code == 202)
        job = wait_job(client, doc_id, {'complete', 'review', 'failed', 'paused'}, timeout=60)
        assert job['status'] in {'complete', 'review'}


def test_deleting_a_waiting_paper_cancels_its_batch(tmp_path):
    provider = BatchDouble({}, finish=False)
    app, client = start(tmp_path, provider)
    with client:
        doc_id = upload(client, make_pdf(2))
        resume(client, doc_id, batch=True)
        wait_job(client, doc_id, {'batch_waiting'})
        assert client.delete(f'/documents/{doc_id}').status_code == 204
        assert provider.cancelled


def test_requests_left_unanswered_twice_pause_with_batch_expired(tmp_path):
    provider = BatchDouble({}, expire=True)
    app, client = start(tmp_path, provider)
    with client:
        doc_id = upload(client, make_pdf(2))
        resume(client, doc_id, batch=True)
        job = wait_job(client, doc_id, {'paused', 'failed', 'complete', 'review'}, timeout=30)
        assert job['status'] == 'paused' and job['checkpoint']['error'] == 'BATCH_EXPIRED'
        assert len(provider.submitted) == 2  # resubmitted once, then gave up


def test_saving_mode_needs_an_api_key_connection(tmp_path):
    app, client = start(tmp_path, BatchDouble({}))
    with client:
        doc_id = upload(client, make_pdf(2))
        assert resume(client, doc_id, batch=True, mode='cli').status_code == 409


# --- The three providers' batch APIs, over a mocked transport -----------------

class Key:
    def get(self): return 'sk-test-key-0123456789'


def run(provider, requests):
    async def go():
        handle = await provider.submit_batch(requests)
        assert await provider.batch_done(handle)
        results = await provider.batch_results(handle)
        await provider.forget_batch(handle)
        return handle, results
    return asyncio.run(go())


def test_anthropic_batch_round_trip():
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == '/v1/messages/batches':
            body = json.loads(request.content)
            assert [r['custom_id'] for r in body['requests']] == ['r1', 'r2'] and 'stream' not in body['requests'][0]['params']
            return httpx.Response(200, json={'id': 'msgbatch_01', 'processing_status': 'in_progress'})
        if request.url.path == '/v1/messages/batches/msgbatch_01' and request.method == 'GET':
            return httpx.Response(200, json={'processing_status': 'ended'})
        if request.url.path.endswith('/results'):
            lines = [{'custom_id': 'r1', 'result': {'type': 'succeeded', 'message': {'content': [{'type': 'text', 'text': '```json\n{"ok": 1}\n```'}],
                                                                                  'usage': {'input_tokens': 10, 'output_tokens': 5}, 'stop_reason': 'end_turn'}}},
                     {'custom_id': 'r2', 'result': {'type': 'expired'}}]
            return httpx.Response(200, text='\n'.join(map(json.dumps, lines)))
        return httpx.Response(200, json={})
    provider = APIProvider('anthropic', Key(), httpx.MockTransport(handler))
    _, results = run(provider, [('r1', 'translate', 'claude-haiku-4-5', {'blocks': []}, None), ('r2', 'translate', 'claude-haiku-4-5', {'blocks': []}, None)])
    assert results['r1'][0] == {'ok': 1} and results['r1'][1]['tokens_in'] == 10
    assert str(results['r2'][0]) == 'BATCH_RETRY'
    assert ('DELETE', '/v1/messages/batches/msgbatch_01') in seen


def test_openai_batch_uploads_one_file_per_model_and_reads_both_result_files():
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path))
        path = request.url.path
        if path == '/v1/files' and request.method == 'POST':
            assert b'"url": "/v1/responses"' in request.content and b'"stream"' not in request.content
            return httpx.Response(200, json={'id': f'file-in{seen.count(("POST", "/v1/files"))}'})
        if path == '/v1/batches':
            body = json.loads(request.content)
            assert body['endpoint'] == '/v1/responses' and body['completion_window'] == '24h'
            return httpx.Response(200, json={'id': 'batch_' + body['input_file_id'][-1]})
        if path.startswith('/v1/batches/'):
            return httpx.Response(200, json={'status': 'completed', 'output_file_id': 'file-out' + path[-1], 'error_file_id': 'file-err' if path.endswith('1') else None})
        if path == '/v1/files/file-out1/content':
            body = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"a": 1}'}]}],
                    'usage': {'input_tokens': 7, 'output_tokens': 3, 'input_tokens_details': {'cached_tokens': 2}}}
            return httpx.Response(200, text=json.dumps({'custom_id': 'r1', 'response': {'status_code': 200, 'body': body}}))
        if path == '/v1/files/file-err/content':
            return httpx.Response(200, text=json.dumps({'custom_id': 'r2', 'response': {'status_code': 400, 'body': {'error': {'type': 'invalid_request_error'}}}}))
        if path == '/v1/files/file-out2/content':
            body = {'status': 'incomplete', 'output': [], 'usage': {'input_tokens': 1, 'output_tokens': 16000}}
            return httpx.Response(200, text=json.dumps({'custom_id': 'r3', 'response': {'status_code': 200, 'body': body}}))
        return httpx.Response(200, json={})
    provider = APIProvider('openai', Key(), httpx.MockTransport(handler))
    handle, results = run(provider, [('r1', 'translate', 'gpt-5.4', {}, None), ('r2', 'translate', 'gpt-5.4', {}, None), ('r3', 'annotate', 'gpt-5.4-mini', {}, None)])
    assert len(handle['jobs']) == 2
    assert results['r1'][0] == {'a': 1} and results['r1'][1]['cache_read'] == 2
    assert str(results['r2'][0]) == 'AI_REQUEST_FAILED' and str(results['r3'][0]) == 'AI_OUTPUT_LIMIT'
    assert ('DELETE', '/v1/files/file-in1') in seen and ('DELETE', '/v1/files/file-out1') in seen


def test_gemini_batch_matches_answers_by_key_and_splits_large_submissions():
    submitted = []
    def handler(request):
        path = request.url.path
        if path.endswith(':batchGenerateContent'):
            body = json.loads(request.content)['batch']
            submitted.append([r['metadata']['key'] for r in body['input_config']['requests']['requests']])
            assert 'contents' in body['input_config']['requests']['requests'][0]['request']
            return httpx.Response(200, json={'name': f'batches/job{len(submitted)}', 'metadata': {'state': 'BATCH_STATE_PENDING'}})
        if path == '/v1beta/batches/job1' and request.method == 'GET':
            answer = lambda text, reason: {'candidates': [{'content': {'parts': [{'text': 'thinking', 'thought': True}, {'text': text}]}, 'finishReason': reason}],
                                           'usageMetadata': {'promptTokenCount': 7, 'candidatesTokenCount': 3, 'thoughtsTokenCount': 2}}
            return httpx.Response(200, json={'done': True, 'metadata': {'state': 'BATCH_STATE_SUCCEEDED'}, 'response': {'inlinedResponses': {'inlinedResponses': [
                {'response': answer('{"a": 1}', 'MAX_TOKENS')},
                {'metadata': {'key': 'r2'}, 'response': answer('{"b": 2}', 'STOP')}]}}})
        if path == '/v1beta/batches/job2' and request.method == 'GET':
            return httpx.Response(200, json={'done': True, 'metadata': {'state': 'BATCH_STATE_EXPIRED'}})
        return httpx.Response(200, json={})
    provider = APIProvider('google', Key(), httpx.MockTransport(handler))
    # Each request also carries the ~1 KB stage prompt: r1 + r2 fit, r3 starts a second job.
    provider.GEMINI_BATCH_BYTES = 6000
    big = {'text': 'x' * 3000}
    handle, results = run(provider, [('r1', 'translate', 'gemini-3.8-flash', big, None), ('r2', 'translate', 'gemini-3.8-flash', {}, None),
                                     ('r3', 'translate', 'gemini-3.8-flash', big, None)])
    assert submitted == [['r1', 'r2'], ['r3']]
    assert results['r2'][0] == {'b': 2} and results['r2'][1]['tokens_out'] == 5
    assert str(results['r1'][0]) == 'AI_OUTPUT_LIMIT'  # no key echoed: matched by position
    assert str(results['r3'][0]) == 'BATCH_RETRY'
