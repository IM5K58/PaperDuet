"""Paper summary and flow, made on request in two steps: one digest per
top-level section, then one synthesis from the digests alone. Every point keeps
its evidence blocks and gets the same number check as AI notes."""
import asyncio
from fastapi import HTTPException
from pydantic import Field

from .adapter import plain
from .annotations import section_batches, source_text
from .models import Model
from .pipeline import AIRequest, compact, tracked
from .provider import ProviderError
from .validation import number_tokens

FIELDS = ('problem', 'gap', 'method', 'results', 'limits')
PER_FIELD, MAX_FLOW, MAX_VISUALS = 3, 16, 4


class Point(Model):
    text: str = Field(min_length=1, max_length=2000)
    refs: list[str] = Field(default_factory=list, max_length=16)
    flags: list[str] = Field(default_factory=list, max_length=4)  # V5: a number not in its evidence
    edited: bool = False


class FlowStep(Model):
    section: str = Field(min_length=1, max_length=300)
    role: str = Field(default='', max_length=100)
    summary: str = Field(default='', max_length=2000)
    why_next: str = Field(default='', max_length=2000)
    block_id: str | None = Field(default=None, max_length=120)
    edited: bool = False


class Visual(Model):
    block_id: str = Field(min_length=1, max_length=120)
    why: str = Field(default='', max_length=2000)
    edited: bool = False


class Structured(Model):
    problem: list[Point] = Field(default_factory=list, max_length=8)
    gap: list[Point] = Field(default_factory=list, max_length=8)
    method: list[Point] = Field(default_factory=list, max_length=8)
    results: list[Point] = Field(default_factory=list, max_length=8)
    limits: list[Point] = Field(default_factory=list, max_length=8)


class Summary(Model):
    structured: Structured = Field(default_factory=Structured)
    flow: list[FlowStep] = Field(default_factory=list, max_length=30)
    visuals: list[Visual] = Field(default_factory=list, max_length=8)


class SummaryRequest(Model):
    regenerate: bool = False


def text_of(value, limit=2000):
    return plain(value if isinstance(value, str) else '').strip()[:limit]


def evidence_blocks(doc, sources):
    """Blocks a summary may cite: the paper's own content, not references or AI notes."""
    return {b.id: b for b in doc.blocks if not b.note and b.id in sources and not sources[b.id].get('references')}


def check_point(item, evidence):
    if not isinstance(item, dict):
        return None
    text = text_of(item.get('text'))
    refs = [r for r in dict.fromkeys(item.get('refs') or []) if isinstance(r, str) and r in evidence][:16]
    if not text or not refs:
        return None  # an unsupported statement is dropped, not shown
    cited = ' '.join(source_text(evidence[r]) for r in refs)
    return {'text': text, 'refs': refs, 'flags': ['V5'] if set(number_tokens(text)) - set(number_tokens(cited)) else []}


def check_visuals(items, evidence, limit):
    result, seen = [], set()
    for item in items if isinstance(items, list) else []:
        bid = item.get('block_id') if isinstance(item, dict) else None
        if bid in evidence and evidence[bid].type in {'fig', 'tab'} and bid not in seen:
            seen.add(bid)
            result.append({'block_id': bid, 'why': text_of(item.get('why'))})
    return result[:limit]


def parse_digest(value, evidence):
    if not isinstance(value, dict):
        return None
    claims = [p for p in (check_point(c, evidence) for c in value.get('claims') or []) if p][:6]
    return {'role': text_of(value.get('role'), 100), 'summary': text_of(value.get('summary')), 'claims': claims,
            'visuals': check_visuals(value.get('visuals'), evidence, 2)}


def build_summary(value, evidence, sections):
    """The synthesis answer after the deterministic checks: refs must be paper
    blocks, numbers must occur in them (else flagged), flow links must exist."""
    if not isinstance(value, dict):
        raise ValueError('SUMMARY_INVALID')
    structured = value.get('structured') if isinstance(value.get('structured'), dict) else {}
    result = {'structured': {f: [p for p in (check_point(i, evidence) for i in structured.get(f) or []) if p][:PER_FIELD] for f in FIELDS},
              'flow': [], 'visuals': check_visuals(value.get('visuals'), evidence, MAX_VISUALS)}
    firsts = {s['first_block_id'] for s in sections}
    for step in value.get('flow') if isinstance(value.get('flow'), list) else []:
        if not isinstance(step, dict) or not text_of(step.get('section'), 300):
            continue
        bid = step.get('block_id')
        result['flow'].append({'section': text_of(step.get('section'), 300), 'role': text_of(step.get('role'), 100),
                               'summary': text_of(step.get('summary')), 'why_next': text_of(step.get('why_next')),
                               'block_id': bid if bid in firsts or bid in evidence else None})
    result['flow'] = result['flow'][:MAX_FLOW]
    if not result['flow'] and not any(result['structured'].values()):
        raise ValueError('SUMMARY_INVALID')
    return result


def section_name(doc, batch):
    path = batch[0].section_path
    if not path:
        return '앞부분'
    heading = next((b for b in doc.blocks if b.type == 'sec' and b.section_path == path[:1]), None)
    title = text_of(heading.en, 200) if heading else ''
    return f'{path[0]} {title}'.strip() if title and title.lower() != path[0].lower() else path[0]


class Summarizer:
    def __init__(self, store, pipeline):
        self.store, self.pipeline = store, pipeline
        self.tasks = pipeline.summary_tasks  # the pipeline cancels them on delete and shutdown

    def running(self, doc_id):
        task = self.tasks.get(doc_id)
        return bool(task and not task.done())

    def plan(self, doc_id):
        doc = self.store.document(doc_id)
        sources = self.store.sources(doc_id) if doc else {}
        if not doc or not sources:
            raise ValueError('DOCUMENT_NOT_READY')
        evidence = evidence_blocks(doc, sources)
        groups = section_batches(doc.blocks, sources)
        if not groups:
            raise ValueError('DOCUMENT_NOT_READY')
        return doc, evidence, groups

    def estimate(self, doc_id):
        doc, _, groups = self.plan(doc_id)
        chars = sum(len(str(compact(b, 1500))) for _, batch in groups for b in batch)
        calls = len(groups) + 1
        return {'approximate': True, 'calls': calls, 'input_tokens': int(chars / 3) + calls * 700 + len(groups) * 500,
                'output_tokens': len(groups) * 600 + 1800, 'model': self.pipeline.job_options(doc_id).annotate_model}

    def start(self, doc_id, regenerate=False):
        self.plan(doc_id)
        if self.running(doc_id):
            raise ValueError('SUMMARY_RUNNING')
        current = self.store.summary(doc_id)
        if current and current['status'] == 'ready' and not regenerate:
            return
        options = self.pipeline.job_options(doc_id)
        # A new generation asks afresh; retrying a failed one reuses the answers it already paid for.
        generation = (current['generation'] if current else 0) + (1 if not current or current['status'] == 'ready' else 0)
        self.store.save_summary(doc_id, status='running', progress=0, error=None, model=options.annotate_model, generation=generation)
        self.tasks[doc_id] = asyncio.create_task(self.run(doc_id, generation, options))

    def state(self, doc_id):
        current = self.store.summary(doc_id)
        if current and current['status'] == 'running' and not self.running(doc_id):
            # The app closed mid-way; the next attempt resumes from saved answers.
            self.store.save_summary(doc_id, status='failed', error='SUMMARY_INTERRUPTED')
            current = self.store.summary(doc_id)
        return current or {'doc_id': doc_id, 'status': 'none', 'progress': 0, 'payload': None, 'edited': False, 'error': None}

    async def run(self, doc_id, generation, options):
        try:
            doc, evidence, groups = self.plan(doc_id)
            model, total = options.annotate_model, len(groups) + 1
            digests, done = [None] * len(groups), [0]

            def progressed():
                done[0] += 1
                self.store.save_summary(doc_id, progress=round(done[0] / total, 3))

            def section_unit(index, key, batch):
                payload = {'title': doc.title, 'section': section_name(doc, batch), 'blocks': [compact(b, 1500) for b in batch]}
                value = yield AIRequest(f'summary:{generation}:{key}', 'summary_section', model, payload)
                # A malformed digest leaves the section out rather than failing the summary.
                digests[index] = None if isinstance(value, ProviderError) else parse_digest(value, evidence)

            immediate = options.model_copy(update={'batch': False})
            units = [tracked(section_unit(i, key, batch), progressed) for i, (key, batch) in enumerate(groups)]
            await self.pipeline.drive(doc_id, units, immediate, pausable=False)
            sections = [{'section': section_name(doc, batch), 'first_block_id': batch[0].id, **digest}
                        for (_, batch), digest in zip(groups, digests) if digest]
            if not sections:
                raise ValueError('SUMMARY_INVALID')
            abstract = [compact(b, 2000) for b in doc.blocks if b.id in evidence and any(p.lower() == 'abstract' for p in b.section_path)]
            candidates = [{'id': b.id, 'n': b.n, 'caption_en': text_of(b.caption_en, 300)} for b in evidence.values() if b.type in {'fig', 'tab'}]
            result = {}

            def paper_unit():
                payload = {'title': doc.title, 'abstract': abstract, 'sections': sections, 'visual_candidates': candidates}
                value = yield AIRequest(f'summary:{generation}:paper', 'summary_paper', model, payload)
                if isinstance(value, ProviderError):
                    raise value
                result['summary'] = build_summary(value, evidence, sections)

            await self.pipeline.drive(doc_id, [paper_unit()], immediate, pausable=False)
            self.store.save_summary(doc_id, status='ready', progress=1, payload=result['summary'], edited=False, error=None)
        except asyncio.CancelledError:
            raise
        except ProviderError as error:
            self.store.save_summary(doc_id, status='failed', error=str(error))
        except ValueError as error:
            self.store.save_summary(doc_id, status='failed', error=str(error) if str(error).startswith(('SUMMARY_', 'DOCUMENT_')) else 'SUMMARY_FAILED')
        except Exception:
            self.store.save_summary(doc_id, status='failed', error='SUMMARY_FAILED')

    def save_edit(self, doc_id, value: Summary):
        """The reader's own wording. Refs still have to be paper blocks; flags are
        dropped for edited points (the reader vouches for them)."""
        doc, evidence, _ = self.plan(doc_id)
        current = self.store.summary(doc_id)
        if not current or not current['payload']:
            raise LookupError('SUMMARY_NOT_FOUND')
        if self.running(doc_id):
            raise ValueError('SUMMARY_RUNNING')
        payload = value.model_dump()
        for field in FIELDS:
            points = []
            for p in payload['structured'][field]:
                p['refs'] = [r for r in dict.fromkeys(p['refs']) if r in evidence]
                if p['edited']:
                    p['flags'] = []
                points.append(p)
            payload['structured'][field] = points
        for step in payload['flow']:
            if step['block_id'] not in evidence:
                step['block_id'] = None
        payload['visuals'] = [v for v in payload['visuals'] if v['block_id'] in evidence and evidence[v['block_id']].type in {'fig', 'tab'}]
        self.store.save_summary(doc_id, payload=payload, edited=True)


def register(app, store, pipeline):
    summarizer = Summarizer(store, pipeline)
    app.state.summarizer = summarizer

    def known(doc_id):
        if not store.document(doc_id):
            raise HTTPException(404, 'DOCUMENT_NOT_FOUND')

    @app.get('/documents/{doc_id}/summary')
    def get_summary(doc_id: str):
        known(doc_id)
        return summarizer.state(doc_id)

    @app.get('/documents/{doc_id}/summary/estimate')
    def estimate_summary(doc_id: str):
        known(doc_id)
        try:
            return summarizer.estimate(doc_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/documents/{doc_id}/summary', status_code=202)
    async def start_summary(doc_id: str, value: SummaryRequest):  # async: it starts a task on this loop
        known(doc_id)
        try:
            summarizer.start(doc_id, value.regenerate)
        except ValueError as error:
            raise HTTPException(409 if str(error) == 'SUMMARY_RUNNING' else 422, str(error)) from None
        return summarizer.state(doc_id)

    @app.put('/documents/{doc_id}/summary', status_code=204)
    def edit_summary(doc_id: str, value: Summary):
        known(doc_id)
        try:
            summarizer.save_edit(doc_id, value)
        except LookupError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(409 if str(error) == 'SUMMARY_RUNNING' else 422, str(error)) from None

    return summarizer
