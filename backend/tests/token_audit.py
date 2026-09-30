"""Offline token audit: runs the real pipeline on a PDF with an AI double that
answers every stage with realistically sized, valid output, then reports how
many calls each stage makes and roughly how many tokens they would cost.

No network, no AI. Token counts are estimates from character counts; use
them to compare pipeline designs, not to predict an invoice.

    .venv/Scripts/python.exe backend/tests/token_audit.py [paper.pdf]
"""
import json
import re
import secrets
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient

from paperduet.adapter import plain
from paperduet.app import create_app
from paperduet.validation import number_tokens
from paperduet.exports import table_html
from paperduet.models import PipelineOptions

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'fixtures/rex-omni.blocks.json'
SAMPLE_PDF = ROOT / 'artifacts/m1/rex-omni.pdf'
PROMPTS = ROOT / 'backend/paperduet/prompts'
IMAGE_TOKENS = 1600  # upper bound Claude charges for one cropped region
KO_RATIO = .55       # Korean characters produced per English source character


def estimate_tokens(text: str) -> int:
    """~3.6 chars/token for English and JSON, ~1.3 chars/token for Hangul."""
    wide = sum(1 for c in text if ord(c) > 0x2FF)
    return round((len(text) - wide) / 3.6 + wide / 1.3)


def korean(chars: int) -> str:
    return ('번역된 문장입니다 ' * (chars // 9 + 1))[:max(1, chars)]


class Vault:
    def get(self): return 'audit-key'
    def set(self, value): pass
    def delete(self): pass


class AuditProvider:
    """Deliberate test double; answers are valid on the first attempt so the
    audit measures pipeline structure, not retry luck."""
    def __init__(self):
        self.calls = []
        self.store = None

    def connected(self): return True
    async def health_check(self): return {'status': 'ok', 'models': []}

    def _source(self, payload):
        for doc in self.store.library():
            for item in self.store.sources(doc['id']).values():
                block = item['block']
                if block.table and (block.en or '') == payload['source_text'] and (block.caption_en or '') == payload['caption']:
                    return block
        return None

    def _answer(self, stage, payload):
        if stage == 'glossary':
            # Real terms from the paper so glossary filtering is measured honestly.
            if 'candidates' in payload:
                terms = [term for term, _ in payload['candidates']]
            else:
                words = Counter(w.lower() for b in payload['blocks'] for w in re.findall(r'[A-Za-z][A-Za-z-]{5,}', (b.get('en') or '')))
                terms = [term for term, _ in words.most_common(60)]
            unique = list({t.casefold(): t for t in reversed(terms)}.values())[::-1][:60]
            return [{'term': term, 'ko': f'용어 {i}', 'keep_english': i % 3 == 0,
                     'definition_ko': korean(90)} for i, term in enumerate(unique)]
        if stage == 'translate':
            def ko(en):
                # Keep numbers and glossary renderings so validation passes first time.
                terms = [g['ko'] for g in payload['glossary'] if re.search(r'(?<!\w)' + re.escape(g['term']) + r'(?!\w)', plain(en), re.I)]
                return korean(int(len(en) * KO_RATIO)) + ' ' + ' '.join(list(number_tokens(en).elements()) + terms)
            return {'blocks': [{'id': b['id'], 'ko': ko(b.get('en') or ''),
                                **({'caption_ko': ko(b['caption_en'])} if b.get('caption_en') else {}),
                                'headers': [{'row': h['row'], 'col': h['col'], 'text_ko': korean(len(h['text_en']))} for h in b.get('headers', [])]}
                               for b in payload['blocks']]}
        if stage == 'annotate':
            targets = [b for b in payload['target_blocks'] if b.get('en') or b.get('caption_en')]
            return {'notes': [{'after_block_id': b['id'], 'kind': 'key', 'title': '핵심 아이디어', 'body_md': korean(350),
                               'claim': 'interpretation', 'refs': [b['id']]} for b in targets[::4]]}
        if stage == 'table':
            block = self._source(payload)
            return {'html': table_html(block.table) if block else '<table><tr><td>x</td></tr></table>'}
        if stage == 'equation':
            return {'latex': r'\mathcal{L}=-\sum_i y_i\log p_i'}
        raise AssertionError(stage)

    async def json(self, stage, model, payload, image=None):
        system = (PROMPTS / f'{stage}.md').read_text(encoding='utf-8')
        sent = json.dumps(payload, ensure_ascii=False)
        answer = self._answer(stage, payload)
        received = json.dumps(answer, ensure_ascii=False)
        tokens_in = estimate_tokens(system) + estimate_tokens(sent) + (IMAGE_TOKENS if image else 0)
        tokens_out = estimate_tokens(received)
        self.calls.append({'stage': stage, 'tokens_in': tokens_in, 'tokens_out': tokens_out, 'image': bool(image),
                           'payload_chars': len(sent), 'payload': payload})
        return answer, {'tokens_in': tokens_in, 'tokens_out': tokens_out, 'requests': 1}


def wait(client, doc_id, statuses, timeout=600):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f'/documents/{doc_id}/job').json()
        if job['status'] in statuses:
            return job
        time.sleep(.05)
    raise TimeoutError(job)


def glossary_share(calls):
    """Input tokens spent sending glossary entries with translate/annotate calls."""
    total = 0
    for call in calls:
        glossary = call['payload'].get('glossary') if call['stage'] != 'glossary' else None
        if glossary:
            total += estimate_tokens(json.dumps(glossary, ensure_ascii=False))
    return total


def run_audit(pdf: Path = SAMPLE_PDF, data_dir: Path | None = None):
    data_dir = data_dir or Path(tempfile.mkdtemp(prefix='PaperDuet audit '))
    provider = AuditProvider()
    token = secrets.token_hex(32)
    app = create_app(token, data_dir, FIXTURE, vault=Vault(), provider=provider)
    with TestClient(app, headers={'Authorization': 'Bearer ' + token}) as client:
        provider.store = app.state.store
        doc_id = client.post('/documents', content=pdf.read_bytes(), headers={'Content-Type': 'application/pdf'}).json()['doc_id']
        wait(client, doc_id, {'ready_to_translate', 'awaiting_ai'})
        options = PipelineOptions(glossary_model='audit', translate_model='audit', restore_model='audit', annotate_model='audit')
        assert client.post(f'/documents/{doc_id}/resume', json=options.model_dump()).status_code == 202
        job = wait(client, doc_id, {'complete', 'review', 'failed', 'paused'})
        doc = client.get(f'/documents/{doc_id}').json()
        with app.state.store.connect() as db:
            recorded = dict(db.execute('SELECT COUNT(*) AS calls,SUM(tokens_in) AS tokens_in,SUM(tokens_out) AS tokens_out,'
                                       'MIN(payload_chars) AS min_payload FROM ai_usage WHERE doc_id=?', (doc_id,)).fetchone())
    stages = defaultdict(lambda: {'calls': 0, 'images': 0, 'tokens_in': 0, 'tokens_out': 0})
    for call in provider.calls:
        row = stages[call['stage']]
        row['calls'] += 1; row['images'] += call['image']
        row['tokens_in'] += call['tokens_in']; row['tokens_out'] += call['tokens_out']
    source_chars = sum(len(b.get('en') or '') + len(b.get('caption_en') or '') for b in doc['blocks'] if b['type'] != 'note')
    return {
        'status': job['status'],
        'pages': doc['page_count'],
        'source_chars': source_chars,
        'source_tokens': estimate_tokens('a' * source_chars),
        'stages': dict(stages),
        'calls': len(provider.calls),
        'tokens_in': sum(r['tokens_in'] for r in stages.values()),
        'tokens_out': sum(r['tokens_out'] for r in stages.values()),
        'glossary_resend_tokens': glossary_share(provider.calls),
        'largest_call': max((c['tokens_in'] for c in provider.calls), default=0),
        'recorded': recorded,
    }


def format_report(report, per_call_overhead=0):
    lines = [f"status={report['status']} pages={report['pages']} source≈{report['source_tokens']:,} tok ({report['source_chars']:,} chars)", '',
             f"{'stage':<10}{'calls':>6}{'images':>7}{'in':>11}{'out':>10}{'total':>11}"]
    order = ['table', 'equation', 'glossary', 'translate', 'annotate']
    for stage in sorted(report['stages'], key=lambda s: order.index(s) if s in order else 99):
        r = report['stages'][stage]
        lines.append(f"{stage:<10}{r['calls']:>6}{r['images']:>7}{r['tokens_in']:>11,}{r['tokens_out']:>10,}{r['tokens_in'] + r['tokens_out']:>11,}")
    total = report['tokens_in'] + report['tokens_out']
    lines += ['', f"total      {report['calls']:>6} calls  in {report['tokens_in']:,}  out {report['tokens_out']:,}  = {total:,} tokens",
              f"glossary re-sent in calls: {report['glossary_resend_tokens']:,} tokens (included above)",
              f"largest single call input: {report['largest_call']:,} tokens"]
    if per_call_overhead:
        lines.append(f"+ fixed overhead {per_call_overhead:,}/call (CLI system prompt, thinking) = {total + per_call_overhead * report['calls']:,} tokens")
    return '\n'.join(lines)


if __name__ == '__main__':
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else SAMPLE_PDF
    report = run_audit(pdf)
    print(format_report(report))
    print(json.dumps({k: v for k, v in report.items() if k != 'stages'}, ensure_ascii=False))
