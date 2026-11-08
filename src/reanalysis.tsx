import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import katex from 'katex';
import { ERROR_TEXT, request } from './api';
import { combineSignals } from './abort';
import { Inline } from './blocks';
import { Icon } from './icons';
import { useDialogDismissal } from './dialog-dismiss';
import type { Block, Document } from './types';
import './styles/reanalysis.css';

export interface ReanalysisChange {
  id: string; kind: 'added' | 'changed' | 'missing' | 'unchanged';
  old_block: Block | null; new_block: Block | null;
  translation_kept: boolean; requires_translation: boolean; selectable: boolean; reason?: string;
}
export interface ReanalysisPreview {
  id: string; doc_id: string; method: 'pdf' | 'arxiv_html' | 'mixed' | 'ocr'; created_at: string;
  changes: ReanalysisChange[]; counts: Record<ReanalysisChange['kind'], number>; notices: string[]; applied: boolean;
  ocr?:{pages:number[];language:string;results:{page:number;status:'recognized'|'empty'|'failed';blocks:number;error?:string}[]};
}
export interface ReanalysisApplied { applied: number; document: Document }
const KINDS = { added: '추가', changed: '변경', missing: '현재 원문에 없음', unchanged: '동일' };

function SourceImage({ path, label }: { path: string; label: string }) {
  const ref = useRef<HTMLDivElement>(null), [url, setUrl] = useState(''), [error, setError] = useState(false), [zoom, setZoom] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setUrl(''); setError(false); setZoom(false);
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      observer.disconnect();
      void request<{ data_url: string }>(path, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setUrl(value.data_url); }).catch(() => { if (!controller.signal.aborted) setError(true); });
    }, { rootMargin: '200px' });
    if (ref.current) observer.observe(ref.current);
    return () => { controller.abort(); observer.disconnect(); };
  }, [path]);
  return <div ref={ref} className="reanalysis-image">{url ? <><div className={zoom ? 'reanalysis-image-scroll zoomed' : 'reanalysis-image-scroll'}><img src={url} alt={label} onError={()=>{setUrl('');setError(true);}}/></div><button type="button" aria-pressed={zoom} onClick={() => setZoom(value => !value)}>{zoom ? '이미지 폭 맞춤' : '이미지 확대'}</button></> : <p role={error?'alert':'status'}>{error ? '이미지를 불러오지 못했습니다.' : '이미지를 불러오는 중…'}</p>}</div>;
}

function SourceMath({ latex }: { latex: string }) {
  const html = useMemo(() => { try { return katex.renderToString(latex, { displayMode: true, throwOnError: true, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20 }); } catch { return null; } }, [latex]);
  return html ? <div className="reanalysis-math" dangerouslySetInnerHTML={{ __html: html }}/> : <pre className="reanalysis-extraction">{latex}</pre>;
}

function SourceBlock({ block, imagePath, side }: { block: Block | null; imagePath: string; side: '이전' | '새' }) {
  if (!block) return <p className="reanalysis-absent">{side === '이전' ? '기존 원문에 없던 항목입니다.' : '다시 읽은 원문에서 찾지 못했습니다. 기존 내용은 유지합니다.'}</p>;
  return <>
    {block.image_path && <SourceImage path={imagePath} label={`${side} 원문 이미지`}/>}
    {block.caption_en && <p className="reanalysis-source-caption"><Inline text={block.caption_en}/></p>}
    {block.latex && <SourceMath latex={block.latex}/>}
    {block.table && <div className="reanalysis-table" tabIndex={0} role="region" aria-label={`${side} 표 구조`}><table><tbody>{[...block.table.header, ...block.table.body].map((row, ri) => <tr key={ri}>{row.map((cell, ci) => {
      const Tag = cell.is_header ? 'th' : 'td';
      return <Tag key={ci} rowSpan={cell.rowspan} colSpan={cell.colspan}><Inline text={cell.text_en}/></Tag>;
    })}</tr>)}</tbody></table></div>}
    {block.en && <div className={block.type === 'tab' || block.type === 'eq' ? 'reanalysis-extraction' : 'reanalysis-source-text'}><Inline text={block.en}/></div>}
    {block.card && <pre className="reanalysis-extraction">{block.card.body}</pre>}
    {!block.image_path && !block.caption_en && !block.latex && !block.table && !block.en && !block.card && <p className="reanalysis-absent">표시할 원문 내용이 없습니다.</p>}
  </>;
}

function ChangeCard({ change, base, selected, disabled, onSelect, ocr=false }: { change: ReanalysisChange; base: string; selected: boolean; disabled: boolean; onSelect: (checked: boolean) => void; ocr?:boolean }) {
  const block = change.new_block || change.old_block;
  const title = block?.n || (block?.type === 'fig' ? '그림' : block?.type === 'tab' ? '표' : block?.type === 'eq' ? '수식' : block?.section_path.at(-1) || '본문');
  const translations = [...new Set([change.old_block?.ko, change.old_block?.caption_ko, change.old_block?.card?.explain_ko,
    ...(change.old_block?.table?.header.flat().map(cell => cell.text_ko) || [])].filter((text): text is string => !!text?.trim()))];
  const excerpt = block?.caption_en || block?.en;
  return <article className={`reanalysis-change kind-${change.kind}${selected ? ' is-selected' : ''}`}>
    <header><span className={`reanalysis-kind kind-${change.kind}`}>{KINDS[change.kind]}</span><strong>{title}</strong>{block?.page && <small>{block.page}쪽</small>}
      {change.selectable ? <label className="reanalysis-select"><input type="checkbox" checked={selected} disabled={disabled} aria-label={`${title} 반영 선택 (${change.id})`} onChange={event => onSelect(event.target.checked)}/>반영</label> : <span className="reanalysis-retained">{change.kind === 'missing' ? '기존 내용 유지' : change.kind === 'unchanged' ? '변경 없음' : '반영할 수 없음'}</span>}</header>
    {excerpt && <div className="reanalysis-excerpt"><Inline text={excerpt}/></div>}
    {change.reason && !/^[A-Z_]+$/.test(change.reason) && <p className="reanalysis-reason">{change.reason}</p>}
    {change.kind === 'missing' && <p className="reanalysis-preserved">현재 원문에서 찾지 못한 항목은 삭제하지 않습니다.</p>}
    <details className="reanalysis-compare"><summary>이전·새 원문 비교</summary><div className="reanalysis-columns">
      <section aria-label={ocr?'원본 PDF 페이지':'이전 원문'}><h3>{ocr?`원본 PDF ${block?.page||''}쪽 · 전체 페이지`:'이전 원문'}</h3>{ocr&&block?.page?<SourceImage path={`/documents/${encodeURIComponent(block.doc_id)}/pages/${block.page}/image`} label={`원본 PDF ${block.page}쪽 · 전체 페이지`}/>:<SourceBlock block={change.old_block} imagePath={`${base}/changes/${encodeURIComponent(change.id)}/image?side=before`} side="이전"/>}</section>
      <section aria-label={ocr?'읽은 글자':'새 원문'}><h3>{ocr?'이미지에서 읽은 글자':'새 원문'}</h3><SourceBlock block={change.new_block} imagePath={`${base}/changes/${encodeURIComponent(change.id)}/image?side=after`} side="새"/></section>
    </div></details>
    {change.translation_kept && <div className="reanalysis-translation"><strong>기존 번역 보존</strong>{translations.map((text, index) => <p key={index}><Inline text={text}/></p>)}{!translations.length && <p>저장된 번역을 그대로 유지합니다.</p>}</div>}
    {change.requires_translation && <p className="reanalysis-translation-notice">{change.translation_kept ? '바뀐 원문은 미번역으로 추가됩니다. 기존 번역은 이전 원문에 보존됩니다.' : '새 원문은 번역이 필요합니다.'}</p>}
  </article>;
}

export function ReanalysisDialog({ doc, onClose, onApplied, initialPreview, onRestart }: { doc: Document; onClose: () => void; onApplied: (value: ReanalysisApplied) => void; initialPreview?:ReanalysisPreview; onRestart?:()=>void }) {
  const ref = useRef<HTMLDialogElement>(null), controller = useRef<AbortController | null>(null), alive = useRef(true);
  const [preview, setPreview] = useState<ReanalysisPreview | null>(initialPreview||null), [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(!initialPreview), [applying, setApplying] = useState(false), [confirm, setConfirm] = useState(false), [showSame, setShowSame] = useState(false), [error, setError] = useState('');
  const close = () => { if (!applying) { controller.current?.abort(); onClose(); } };
  const dismiss = useDialogDismissal(close);
  const start = useCallback(async () => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setLoading(true); setError(''); setPreview(null); setSelected(new Set()); setConfirm(false);
    try {
      const value = await request<ReanalysisPreview>(`/documents/${encodeURIComponent(doc.id)}/reanalysis`, { method: 'POST', signal: combineSignals([abort.signal, AbortSignal.timeout(900000)]) });
      if (alive.current && !abort.signal.aborted) setPreview(value);
    } catch (failure) { if (alive.current && !abort.signal.aborted) setError((failure as Error).message); }
    finally { if (alive.current && !abort.signal.aborted) setLoading(false); }
  }, [doc.id]);
  useEffect(() => {
    alive.current = true; const dialog = ref.current, opener = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow; dialog?.showModal(); document.body.style.overflow = 'hidden'; if(!initialPreview)void start();
    return () => { alive.current = false; controller.current?.abort(); dialog?.close(); document.body.style.overflow = overflow; opener?.focus({ preventScroll: true }); };
  }, [start,initialPreview]);
  const selectable = preview?.changes.filter(change => change.selectable) || [];
  const chosen = preview?.changes.filter(change => selected.has(change.id) && change.selectable) || [];
  const apply = async () => {
    if (!preview || !chosen.length || applying) return;
    setApplying(true); setError('');
    try {
      const value = await request<ReanalysisApplied>(`/documents/${encodeURIComponent(doc.id)}/reanalysis/${encodeURIComponent(preview.id)}/apply`, { method: 'POST', body: JSON.stringify({ change_ids: chosen.map(change => change.id) }), signal: AbortSignal.timeout(120000) });
      if (alive.current) onApplied(value);
    } catch (failure) { if (alive.current) setError((failure as Error).message); }
    finally { if (alive.current) setApplying(false); }
  };
  const isOcr=preview?.method==='ocr';
  return <dialog ref={ref} {...dismiss} className="reanalysis-dialog" aria-label={isOcr?'글자 읽기 결과':'원문 다시 분석'} onCancel={event => { event.preventDefault(); close(); }}>
    <header className="reanalysis-heading"><div><h2>{isOcr?'글자 읽기 결과':'원문 다시 분석'}</h2><p>{confirm ? '선택한 변경 사항을 마지막으로 확인합니다.' : isOcr?'이미지에서 읽은 글자를 원본과 비교하고 필요한 내용만 반영하세요.':'바뀐 원문과 이미지를 비교하고 필요한 부분만 반영하세요.'}</p></div><button className="icon-button" type="button" disabled={applying} onClick={close} aria-label="다시 분석 닫기"><Icon name="close"/></button></header>
    <div className="reanalysis-content" aria-busy={loading || applying}>
      <p className="reanalysis-preservation">바뀐 원문은 새 항목으로 추가하고, 기존 번역·메모·AI 대화는 이전 원문에 보존합니다. 선택하지 않은 항목은 그대로 유지합니다.</p>
      {loading && <div className="reanalysis-loading" role="status"><span className="spinner"/>원문과 그림을 다시 읽고 있습니다…<small>미리보기만 만들며 저장된 논문은 변경하지 않습니다.</small></div>}
      {error && <div className="reanalysis-error" role="alert"><p>{error}</p>{!applying && <button type="button" onClick={isOcr?(onRestart||onClose):()=>void start()}>{isOcr?'글자 읽기 다시 선택':'다시 분석'}</button>}</div>}
      {preview && !confirm && <>
        <div className="reanalysis-counts" aria-label="분석 결과">{(Object.keys(KINDS) as ReanalysisChange['kind'][]).map(kind => <span key={kind}><b>{preview.counts[kind]}</b>{KINDS[kind]}</span>)}</div>
        {preview.ocr&&<section className="reanalysis-ocr-results"><h3>읽은 쪽 {preview.ocr.results.filter(result=>result.status==='recognized').length} · 글자 없음 {preview.ocr.results.filter(result=>result.status==='empty').length} · 실패 {preview.ocr.results.filter(result=>result.status==='failed').length}</h3><p>이미지에서 읽은 글자에는 숫자·수식 오류가 있을 수 있습니다. 원본 PDF와 대조해 주세요.</p><ul>{preview.ocr.results.map(result=><li key={result.page}>{result.page}쪽 · {result.status==='recognized'?`읽은 원문 ${result.blocks}개`:result.status==='empty'?'읽을 글자를 찾지 못했습니다.':ERROR_TEXT[result.error||'']||'이 페이지의 글자를 읽지 못했습니다.'}</li>)}</ul></section>}
        {preview.notices.length > 0 && <div className="reanalysis-notices">{preview.notices.map((notice, index) => <p key={index}>{notice}</p>)}</div>}
        <div className="reanalysis-options"><label><input type="checkbox" checked={!!selectable.length && selected.size === selectable.length} disabled={!selectable.length} onChange={event => setSelected(new Set(event.target.checked ? selectable.map(change => change.id) : []))}/>반영 가능한 항목 모두 선택 ({selectable.length})</label><label><input type="checkbox" checked={showSame} onChange={event => setShowSame(event.target.checked)}/>같은 항목도 보기 ({preview.counts.unchanged})</label></div>
        <div className="reanalysis-changes">{preview.changes.filter(change => showSame || change.kind !== 'unchanged').map(change => <ChangeCard key={change.id} change={change} base={`/documents/${encodeURIComponent(doc.id)}/reanalysis/${encodeURIComponent(preview.id)}`} selected={selected.has(change.id)} disabled={applying} ocr={isOcr} onSelect={checked => setSelected(current => { const next = new Set(current); if (checked) next.add(change.id); else next.delete(change.id); return next; })}/>)}</div>
        {!preview.changes.some(change => change.kind !== 'unchanged') && <p className="reanalysis-empty">새로 반영할 변경 사항이 없습니다.</p>}
      </>}
      {preview && confirm && <section className="reanalysis-confirm"><h3>선택한 {chosen.length}개를 반영할까요?</h3><p>{isOcr?'선택한 글자 읽기 결과를 반영합니다. 원본 PDF와 기존 번역·메모·대화는 유지합니다.':'그림이나 배치만 달라진 항목은 그대로 개선합니다. 원문 내용이 달라진 항목은 새로 추가하고, 기존 번역·메모·대화는 이전 원문에서 유지합니다.'}</p><ul>{chosen.map(change => <li key={change.id}>{KINDS[change.kind]} · {change.new_block?.n || change.old_block?.n || '본문'}</li>)}</ul>{chosen.some(change => change.requires_translation) && <p className="reanalysis-translation-notice">새로 추가된 원문은 번역이 필요합니다.</p>}</section>}
    </div>
    <footer className="reanalysis-footer"><span role="status">{applying ? '선택한 내용을 반영하고 있습니다…' : preview ? `${chosen.length}개 선택 · ${preview.method==='ocr'?'글자 읽기':preview.method === 'mixed' ? 'HTML + PDF' : preview.method === 'arxiv_html' ? 'arXiv HTML' : 'PDF'} 원문` : ''}</span><div>{confirm ? <><button type="button" disabled={applying} onClick={() => { setConfirm(false); setError(''); }}>돌아가서 비교</button><button className="primary-button" type="button" disabled={applying || !chosen.length} onClick={() => void apply()}>{applying ? '반영 중…' : `${chosen.length}개 반영`}</button></> : <>{isOcr&&onRestart&&<button type="button" onClick={onRestart}>다시 선택</button>}<button type="button" onClick={close}>취소</button><button className="primary-button" type="button" disabled={loading || !chosen.length} onClick={() => setConfirm(true)}>선택한 {chosen.length}개 검토</button></>}</div></footer>
  </dialog>;
}
