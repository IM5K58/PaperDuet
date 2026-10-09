import { useCallback, useEffect, useState } from 'react';
import { request } from './api';
import { Crop } from './blocks';
import { Icon } from './icons';
import type { Anchor } from './ask';
import type { Document } from './types';

type Field = 'problem' | 'gap' | 'method' | 'results' | 'limits';
type Point = { text: string; refs: string[]; flags: string[]; edited?: boolean };
type FlowStep = { section: string; role: string; summary: string; why_next: string; block_id: string | null; edited?: boolean };
type Visual = { block_id: string; why: string; edited?: boolean };
type Payload = { structured: Record<Field, Point[]>; flow: FlowStep[]; visuals: Visual[] };
type State = { status: 'none' | 'running' | 'ready' | 'failed'; progress: number; payload: Payload | null; edited: boolean; error: string | null; model?: string };
type Estimate = { calls: number; input_tokens: number; output_tokens: number; model: string };

// Each part borrows the colour of the matching note kind.
const FIELDS: [Field, string, string][] = [['problem', '문제', 'key'], ['gap', '기존 한계', 'ins'], ['method', '제안 방법', 'mth'], ['results', '핵심 결과', 'res'], ['limits', '한계·향후 과제', 'lim']];
const FAILURES: Record<string, string> = {
  SUMMARY_INVALID: 'AI 답변에서 근거를 갖춘 요약을 찾지 못했습니다. 다시 만들어 보세요.',
  SUMMARY_INTERRUPTED: '앱이 닫혀 요약이 중단됐습니다. 다시 시도하면 이미 정리한 섹션은 건너뛰고 이어서 만듭니다.',
  AI_OVERLOADED: 'AI 제공자 서버가 혼잡합니다. 잠시 후 다시 시도하면 이어서 만듭니다.',
  AI_RATE_LIMIT: '사용량 한도에 도달했습니다. 잠시 후 다시 시도하면 이어서 만듭니다.',
  AI_CONNECTION_REQUIRED: 'AI 연결 설정에서 API 키 또는 공식 CLI를 연결해 주세요.',
};
const tokens = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}천` : String(n);

export function SummaryView({ doc, labels, onJump, onAsk }: { doc: Document; labels: Map<string, string>; onJump: (id: string) => void; onAsk: (anchor: Anchor) => void }) {
  const [state, setState] = useState<State | null>(null);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const base = `/documents/${doc.id}/summary`;
  const load = useCallback(async () => {
    const next = await request<State>(base);
    setState(next);
    if (next.status === 'none' || next.status === 'failed') void request<Estimate>(base + '/estimate').then(setEstimate).catch(() => setEstimate(null));
  }, [base]);
  useEffect(() => { void load().catch(e => setError((e as Error).message)); }, [load]);
  useEffect(() => {
    if (state?.status !== 'running') return;
    const timer = setInterval(() => { void request<State>(base).then(setState).catch(() => {}); }, 1000);
    return () => clearInterval(timer);
  }, [state?.status, base]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 3000); return () => clearTimeout(timer); }, [notice]);

  const start = async (regenerate = false) => {
    setBusy(true); setError(''); setConfirming(false);
    try { setState(await request<State>(base, { method: 'POST', body: JSON.stringify({ regenerate }) })); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const save = async (change: (payload: Payload) => void) => {
    if (!state?.payload) return;
    const payload: Payload = structuredClone(state.payload);
    change(payload);
    setError('');
    try { await request(base, { method: 'PUT', body: JSON.stringify(payload) }); await load(); }
    catch (e) { setError((e as Error).message); throw e; }
  };
  const toNotes = async (title: string, body: string, blockId?: string | null) => {
    try {
      await request(`/documents/${doc.id}/presentation`, { method: 'POST', body: JSON.stringify({ title, body_md: body, block_id: blockId ?? null }) });
      setNotice(`‘${title}’을(를) 발표 노트에 추가했습니다.`);
    } catch (e) { setError((e as Error).message); }
  };
  const ask = (text: string, blockId: string) => onAsk({ block_id: blockId, field: 'summary', start: 0, end: text.length, text });

  if (!state) return <section className="summary-view" aria-label="논문 요약"><p role="status">{error || '요약을 불러오고 있습니다…'}</p></section>;
  const status = <>{error && <p className="summary-error" role="alert">{error}</p>}{notice && <p className="summary-notice" role="status">{notice}</p>}</>;

  if (state.status === 'running') return <section className="summary-view" aria-label="논문 요약">
    <div className="summary-progress" role="status"><b>요약을 만들고 있습니다</b>
      <span>{state.progress < 1 && state.progress > 0 ? `섹션별로 정리한 뒤 전체를 종합합니다 · ${Math.round(state.progress * 100)}%` : '섹션별로 정리한 뒤 전체를 종합합니다'}</span>
      <div className="summary-bar"><i style={{ width: `${Math.max(4, Math.round(state.progress * 100))}%` }}/></div></div>{status}</section>;

  if (state.status !== 'ready' || !state.payload) return <section className="summary-view" aria-label="논문 요약">
    <div className="summary-empty">
      <h2>논문 요약 · 흐름 정리</h2>
      <p>섹션마다 핵심 주장을 정리한 뒤, 그것만 모아 <b>문제 → 기존 한계 → 제안 방법 → 핵심 결과 → 한계</b>와 섹션 흐름, 핵심 그림·표를 만듭니다. 항목마다 원문 근거로 바로 이동할 수 있습니다.</p>
      {state.status === 'failed' && <p className="summary-error" role="alert">{FAILURES[state.error ?? ''] ?? '요약을 만들지 못했습니다. 다시 시도해 주세요.'}</p>}
      {estimate && <p className="summary-estimate">예상 · AI 호출 {estimate.calls}회 · 입력 약 {tokens(estimate.input_tokens)} 토큰 · 출력 약 {tokens(estimate.output_tokens)} 토큰 · {estimate.model}</p>}
      <button className="primary-button" disabled={busy} onClick={() => void start()}>{state.status === 'failed' ? '이어서 만들기' : '요약 만들기'}</button>
    </div>{status}</section>;

  const { structured, flow, visuals } = state.payload;
  const blocks = new Map(doc.blocks.map(b => [b.id, b]));
  return <section className="summary-view" aria-label="논문 요약">
    <div className="summary-head">
      <span>AI 요약{state.model ? ` · ${state.model}` : ''}{state.edited ? ' · 직접 수정함' : ''}</span>
      {confirming ? <span className="summary-confirm" role="alert">{state.edited ? '직접 고친 내용이 사라집니다. ' : ''}다시 만들까요?
        <button className="primary-button" disabled={busy} onClick={() => void start(true)}>다시 만들기</button><button onClick={() => setConfirming(false)}>취소</button></span>
        : <button disabled={busy} onClick={() => setConfirming(true)}>다시 만들기</button>}
    </div>
    {status}
    <h2 className="summary-title">구조화 요약</h2>
    <div className="summary-structured">{FIELDS.filter(([field]) => structured[field]?.length).map(([field, label, kind]) =>
      <div key={field} className={`summary-field kind-${kind}`}><h3>{label}</h3><ul>{structured[field].map((point, i) =>
        <PointItem key={`${field}-${i}-${point.text.length}`} point={point} label={label} labels={labels} onJump={onJump}
          onAsk={point.refs[0] ? () => ask(point.text, point.refs[0]) : undefined}
          onNote={() => void toNotes(label, point.text, point.refs[0])}
          onSave={text => save(p => { p.structured[field][i] = { ...point, text, edited: true, flags: [] }; })}/>)}</ul></div>)}</div>
    {flow.length > 0 && <><h2 className="summary-title">섹션 흐름</h2><ol className="summary-flow">{flow.map((step, i) =>
      <FlowItem key={`${i}-${step.section}`} step={step} last={i === flow.length - 1} onJump={onJump}
        onAsk={step.block_id ? () => ask(`${step.section}: ${step.summary}`, step.block_id!) : undefined}
        onNote={() => void toNotes(step.section, [step.summary, step.why_next && `→ ${step.why_next}`].filter(Boolean).join('\n\n'), step.block_id)}
        onSave={summary => save(p => { p.flow[i] = { ...step, summary, edited: true }; })}/>)}</ol></>}
    {visuals.length > 0 && <><h2 className="summary-title">핵심 그림·표</h2><div className="summary-visuals">{visuals.map(visual => {
      const block = blocks.get(visual.block_id);
      if (!block) return null;
      return <figure key={visual.block_id} className="summary-visual">
        {block.image_path ? <Crop block={block}/> : <p className="summary-caption">{block.caption_en}</p>}
        <figcaption><b>{block.n || labels.get(block.id)}</b> {visual.why}
          <span className="summary-actions"><button onClick={() => onJump(block.id)}>본문에서 보기</button>
            <button onClick={() => void toNotes(block.n || '핵심 그림·표', visual.why, block.id)}>발표 노트에 추가</button>
            <button onClick={() => ask(`${block.n ?? ''} ${visual.why}`.trim(), block.id)}>AI에게 질문</button></span></figcaption></figure>;
    })}</div></>}
    <p className="summary-foot">AI가 원문에서 정리한 요약입니다. 숫자가 근거 블록에서 확인되지 않으면 ‘근거 확인 필요’로 표시합니다.</p>
  </section>;
}

function Editor({ value, label, onSave, onCancel }: { value: string; label: string; onSave: (text: string) => Promise<void>; onCancel: () => void }) {
  const [text, setText] = useState(value);
  const [saving, setSaving] = useState(false);
  return <div className="summary-edit"><textarea aria-label={`${label} 고치기`} value={text} rows={3} onChange={e => setText(e.target.value)}/>
    <div><button className="primary-button" disabled={saving || !text.trim()} onClick={() => { setSaving(true); void onSave(text.trim()).then(onCancel, () => setSaving(false)); }}>저장</button><button onClick={onCancel}>취소</button></div></div>;
}

function PointItem({ point, label, labels, onJump, onAsk, onNote, onSave }: { point: Point; label: string; labels: Map<string, string>; onJump: (id: string) => void; onAsk?: () => void; onNote: () => void; onSave: (text: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  return <li className="summary-point">
    {editing ? <Editor value={point.text} label={label} onSave={onSave} onCancel={() => setEditing(false)}/>
      : <p>{point.text}{point.flags.includes('V5') && <span className="qa-label" title="요약 속 숫자를 근거 블록에서 찾지 못했습니다">근거 확인 필요</span>}{point.edited && <span className="summary-edited">직접 수정</span>}</p>}
    <div className="summary-meta">{point.refs.map(id => <button key={id} className="summary-ref" title="본문에서 보기" onClick={() => onJump(id)}>{labels.get(id) || id}</button>)}
      <span className="summary-actions"><button onClick={() => setEditing(true)} disabled={editing}>고치기</button><button onClick={onNote}>발표 노트에 추가</button>
        {onAsk && <button onClick={onAsk}><Icon name="message"/>AI에게 질문</button>}</span></div>
  </li>;
}

function FlowItem({ step, last, onJump, onAsk, onNote, onSave }: { step: FlowStep; last: boolean; onJump: (id: string) => void; onAsk?: () => void; onNote: () => void; onSave: (text: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  return <li className="summary-step">
    <div className="summary-step-head"><b>{step.section}</b>{step.role && <span className="summary-role">{step.role}</span>}{step.edited && <span className="summary-edited">직접 수정</span>}</div>
    {editing ? <Editor value={step.summary} label={step.section} onSave={onSave} onCancel={() => setEditing(false)}/> : step.summary && <p>{step.summary}</p>}
    {!last && step.why_next && <p className="summary-next"><Icon name="arrowRight"/>{step.why_next}</p>}
    <span className="summary-actions">{step.block_id && <button onClick={() => onJump(step.block_id!)}>본문으로 이동</button>}
      <button onClick={() => setEditing(true)} disabled={editing}>고치기</button><button onClick={onNote}>발표 노트에 추가</button>
      {onAsk && <button onClick={onAsk}><Icon name="message"/>AI에게 질문</button>}</span>
  </li>;
}
