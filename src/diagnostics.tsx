import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from './api';
import { combineSignals } from './abort';
import { ReaderBlock } from './blocks';
import { Icon } from './icons';
import { useDialogDismissal } from './dialog-dismiss';
import type { Document } from './types';
import './styles/diagnostics.css';

export type DiagnosticKind = 'missing_image' | 'restore_failed' | 'restore_pending' | 'untranslated' | 'translation_rejected' | 'unmatched_source' | 'page_without_text' | 'visual_pdf_fallback' | 'ocr_recognized';
export interface DiagnosticIssue { id:string; kind:DiagnosticKind; severity:'attention'|'info'; block_id:string|null; page:number|null; title:string; detail:string; source_available:boolean; region?:number[]|null }
export interface DiagnosticReport {
  doc_id:string; generated_at:string; busy:boolean;
  summary:{ total:number; by_kind:Record<string,number>; blocks:{paragraphs:number;figures:number;tables:number;equations:number}; pages:{total:number;with_text:number;without_text:number[]} };
  issues:DiagnosticIssue[]; notices:string[];
}
export interface SourceTarget { blockId?:string; page?:number; region?:number[]; title?:string }
export interface BlockSource {
  block_id:string|null; page:number|null; page_count:number; bbox:number[]|null; region:number[]|null;
  page_size:{width:number;height:number}|null; source_kind:string; source_url:string; source_anchor:string|null;
  available:boolean; reason:string|null;
}

function useModal(ref:React.RefObject<HTMLDialogElement|null>) {
  useEffect(()=>{const dialog=ref.current,opener=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow;dialog?.showModal();document.body.style.overflow='hidden';return()=>{dialog?.close();document.body.style.overflow=overflow;opener?.focus({preventScroll:true});};},[ref]);
}

export function DiagnosticsDialog({doc,onClose,onCompare,onJump,onProcess,onReanalysis,onOcr}:{doc:Document;onClose:()=>void;onCompare:(target:SourceTarget)=>void;onJump:(id:string)=>void;onProcess?:()=>void;onReanalysis?:()=>void;onOcr?:(page?:number)=>void}) {
  const ref=useRef<HTMLDialogElement>(null),controller=useRef<AbortController|null>(null),alive=useRef(true);
  const [report,setReport]=useState<DiagnosticReport|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[attentionOnly,setAttentionOnly]=useState(false);
  useModal(ref);const dismiss=useDialogDismissal(onClose);
  const load=useCallback(async()=>{
    controller.current?.abort();const abort=new AbortController();controller.current=abort;setLoading(true);setError('');
    try{const value=await request<DiagnosticReport>(`/documents/${encodeURIComponent(doc.id)}/diagnostics`,{signal:combineSignals([abort.signal,AbortSignal.timeout(120000)])});if(alive.current&&!abort.signal.aborted)setReport(value);}
    catch(failure){if(alive.current&&!abort.signal.aborted)setError((failure as Error).message);}
    finally{if(alive.current&&!abort.signal.aborted)setLoading(false);}
  },[doc.id]);
  useEffect(()=>{alive.current=true;void load();return()=>{alive.current=false;controller.current?.abort();};},[load]);
  const attention=report?.issues.filter(issue=>issue.severity==='attention').length||0;
  const issues=report?.issues.filter(issue=>!attentionOnly||issue.severity==='attention')||[];
  const ids=useMemo(()=>new Set(doc.blocks.map(block=>block.id)),[doc.blocks]);
  return <dialog ref={ref} {...dismiss} className="diagnostics-dialog" aria-label="누락 진단" onCancel={event=>{event.preventDefault();onClose();}}>
    <header className="diagnostics-heading"><div><h2>누락 진단</h2><p>추출한 내용과 저장된 상태를 확인합니다.</p></div><button className="icon-button" aria-label="누락 진단 닫기" onClick={onClose}><Icon name="close"/></button></header>
    <div className="diagnostics-content" aria-busy={loading}>
      <p className="diagnostics-explanation">확인할 수 있는 누락과 처리 상태를 보여 줍니다. 원문과 내용이 정확히 일치하는지는 원문 대조로 확인하세요.</p>
      {loading&&<p role="status" className="diagnostics-loading"><span className="spinner"/>저장된 논문을 점검하고 있습니다…</p>}
      {error&&<div role="alert" className="diagnostics-error"><p>{error}</p><button onClick={()=>void load()}>다시 확인</button></div>}
      {report&&<>
        <div className="diagnostics-overview" aria-label="추출된 내용"><span><b>{report.summary.blocks.paragraphs}</b>본문</span><span><b>{report.summary.blocks.figures}</b>그림</span><span><b>{report.summary.blocks.tables}</b>표</span><span><b>{report.summary.blocks.equations}</b>수식</span><span><b>{report.summary.pages.total}</b>쪽</span></div>
        {report.busy&&<p className="diagnostics-notice" role="status">논문을 처리하고 있습니다. 완료 후 다시 확인하면 최신 상태를 볼 수 있습니다.</p>}
        <div className="diagnostics-notices">{report.notices.map((notice,index)=><p key={index}>{notice}</p>)}</div>
        <div className="diagnostics-filter" role="group" aria-label="진단 항목 보기"><button aria-pressed={!attentionOnly} onClick={()=>setAttentionOnly(false)}>전체 {report.issues.length}</button><button aria-pressed={attentionOnly} onClick={()=>setAttentionOnly(true)}>확인 필요 {attention}</button><button className="diagnostics-refresh" disabled={loading} onClick={()=>void load()}>다시 확인</button></div>
        <div className="diagnostics-issues">{issues.map(issue=><article className="diagnostics-issue" key={issue.id}><header><span className={`diagnostics-severity ${issue.severity}`}>{issue.severity==='attention'?'확인 필요':'참고'}</span><h3>{issue.title}</h3>{issue.page!==null&&<small>{issue.page}쪽</small>}</header><p>{issue.detail}</p><div className="diagnostics-issue-actions">{(issue.source_available||issue.block_id||issue.page!==null)&&<button onClick={()=>onCompare({blockId:issue.block_id||undefined,page:issue.page||undefined,region:issue.region||undefined,title:issue.title})}>{issue.source_available?'원문 대조':'원문 위치 확인'}</button>}{issue.kind==='page_without_text'&&issue.page!==null&&issue.source_available&&onOcr&&<button disabled={report.busy} onClick={()=>onOcr(issue.page!)}>이 쪽 글자 읽기</button>}{issue.block_id&&ids.has(issue.block_id)&&<button onClick={()=>onJump(issue.block_id!)}>본문 이동</button>}</div></article>)}</div>
        {!issues.length&&<p className="diagnostics-empty">{report.issues.length?'이 필터에 해당하는 항목이 없습니다.':'확인할 항목이 없습니다.'}</p>}
      </>}
    </div><footer className="diagnostics-footer"><span>진단과 원문 대조는 내용을 바꾸거나 AI를 호출하지 않습니다.</span><div>{onProcess&&<button onClick={onProcess}>처리 상태 보기</button>}{onReanalysis&&<button disabled={!report||loading||report.busy} onClick={onReanalysis}>원문 다시 분석</button>}<button onClick={onClose}>닫기</button></div></footer>
  </dialog>;
}

function safeSourceUrl(url:string|undefined,anchor:string|null|undefined,kind:string|undefined) {
  if(!url)return null;try{const value=new URL(url);if(!['https:','http:'].includes(value.protocol)||value.username||value.password)return null;if((kind==='arxiv_html'||anchor)&&['arxiv.org','www.arxiv.org','export.arxiv.org'].includes(value.hostname))value.pathname=value.pathname.replace(/^\/abs\//,'/html/');if(anchor)value.hash=anchor;return value.href;}catch{return null;}
}

export function SourceComparison({doc,target,onClose,onJump}:{doc:Document;target:SourceTarget;onClose:()=>void;onJump:(id:string)=>void}) {
  const ref=useRef<HTMLDialogElement>(null),viewport=useRef<HTMLDivElement>(null),highlight=useRef<HTMLDivElement>(null);
  const [source,setSource]=useState<BlockSource|null>(null),[metadataLoading,setMetadataLoading]=useState(true),[metadataError,setMetadataError]=useState(''),[generation,setGeneration]=useState(0);
  const [page,setPage]=useState(target.page||1),[url,setUrl]=useState(''),[pageError,setPageError]=useState(''),[zoom,setZoom]=useState(100),[showHighlight,setShowHighlight]=useState(true),[pageSize,setPageSize]=useState<{width:number;height:number}|null>(null);
  const block=doc.blocks.find(value=>value.id===target.blockId);
  useModal(ref);const dismiss=useDialogDismissal(onClose);
  useEffect(()=>{
    const abort=new AbortController();setSource(null);setMetadataLoading(true);setMetadataError('');setUrl('');setPageError('');setZoom(100);
    const path=target.blockId?`/documents/${encodeURIComponent(doc.id)}/blocks/${encodeURIComponent(target.blockId)}/source`:`/documents/${encodeURIComponent(doc.id)}/source`;
    void request<BlockSource>(path,{signal:combineSignals([abort.signal,AbortSignal.timeout(120000)])}).then(value=>{
      if(abort.signal.aborted)return;setSource(value);setPage(Math.max(1,Math.min(value.page_count,Math.trunc(target.page||value.page||block?.page||1))));setMetadataLoading(false);
    }).catch(failure=>{if(!abort.signal.aborted){setMetadataError((failure as Error).message);setMetadataLoading(false);}});
    return()=>abort.abort();
  },[doc.id,doc.page_count,doc.source_kind,doc.source_url,target.blockId,target.page,block?.page,generation]);
  useEffect(()=>{
    const abort=new AbortController();setUrl('');setPageError('');setPageSize(null);
    if(source?.available&&source.page_count>0)void request<{data_url:string;page_size?:{width:number;height:number}}>(`/documents/${encodeURIComponent(doc.id)}/pages/${page}/image`,{signal:abort.signal}).then(value=>{if(!abort.signal.aborted){setUrl(value.data_url);setPageSize(value.page_size||null);}}).catch(()=>{if(!abort.signal.aborted)setPageError('원본 페이지를 불러오지 못했습니다.');});
    return()=>abort.abort();
  },[doc.id,source,page]);
  const locationPage=target.region?target.page:source?.page;
  const locationRegion=target.region||source?.region||source?.bbox;
  const area=useMemo(()=>{
    const bounds=locationRegion,size=target.region?pageSize:source?.page_size||pageSize;if(!showHighlight||locationPage!==page||!size||size.width<=0||size.height<=0||bounds?.length!==4||!bounds.every(Number.isFinite))return null;
    const left=Math.max(0,Math.min(100,bounds[0]/size.width*100)),top=Math.max(0,Math.min(100,bounds[1]/size.height*100));
    const right=Math.max(left,Math.min(100,bounds[2]/size.width*100)),bottom=Math.max(top,Math.min(100,bounds[3]/size.height*100));if(right<=left||bottom<=top)return null;
    return{left:`${left}%`,top:`${top}%`,width:`${right-left}%`,height:`${bottom-top}%`};
  },[locationRegion,locationPage,source,page,pageSize,showHighlight,target.region]);
  const center=useCallback(()=>{const view=viewport.current,mark=highlight.current;if(view&&mark){view.scrollTop=Math.max(0,mark.offsetTop-(view.clientHeight-mark.offsetHeight)/2);view.scrollLeft=Math.max(0,mark.offsetLeft-(view.clientWidth-mark.offsetWidth)/2);}},[]);
  useEffect(()=>{const frame=requestAnimationFrame(center);return()=>cancelAnimationFrame(frame);},[url,zoom,area,center]);
  const originalUrl=safeSourceUrl(source?.source_url||doc.source_url,source?.source_anchor,source?.source_kind||doc.source_kind);
  const current=block?[block]:doc.blocks.filter(value=>value.page===page&&!value.note&&!value.superseded_by);
  const count=source?.page_count||0;
  return <dialog ref={ref} {...dismiss} className="source-comparison-dialog" aria-label="원문 대조" onCancel={event=>{event.preventDefault();onClose();}}>
    <header className="diagnostics-heading"><div><h2>원문 대조{(target.title||block?.n)&&` · ${target.title||block?.n}`}</h2><p>원본 PDF와 현재 저장된 원문·번역을 나란히 확인합니다.</p></div><button className="icon-button" aria-label="원문 대조 닫기" onClick={onClose}><Icon name="close"/></button></header>
    <div className="source-comparison-body"><section className="source-original" aria-label="원본 PDF 대조"><header><h3>원본 PDF</h3>{originalUrl&&<a href={originalUrl} target="_blank" rel="noreferrer">{source?.source_kind==='arxiv_html'||source?.source_anchor?'HTML 원문 열기':'원문 링크 열기'}</a>}</header>
      {metadataLoading&&<p role="status" className="diagnostics-loading">원문 위치를 확인하고 있습니다…</p>}
      {metadataError&&<div role="alert" className="diagnostics-error"><p>{metadataError}</p><button onClick={()=>setGeneration(value=>value+1)}>위치 다시 확인</button></div>}
      {source&&!source.available&&<p className="source-location-notice">{source.reason||'저장된 원본 PDF를 찾을 수 없습니다.'}</p>}
      {source?.available&&<>
        <div className="source-page-tools"><button className="icon-button" disabled={page<=1} aria-label="대조 이전 페이지" onClick={()=>setPage(value=>Math.max(1,value-1))}><Icon name="chevronLeft"/></button><label>페이지<input aria-label="대조 PDF 페이지" type="number" min={1} max={count} value={page} onChange={event=>setPage(Math.max(1,Math.min(count,Math.trunc(Number(event.target.value))||1)))}/></label><span>/ {count}</span><button className="icon-button" disabled={page>=count} aria-label="대조 다음 페이지" onClick={()=>setPage(value=>Math.min(count,value+1))}><Icon name="chevronRight"/></button><select aria-label="대조 PDF 확대" value={zoom} onChange={event=>setZoom(Number(event.target.value))}><option value={100}>폭 맞춤</option><option value={150}>150%</option><option value={200}>200%</option></select></div>
        {locationPage!=null&&locationRegion&&(source.page_size||target.region)?<div className="source-location-controls"><label><input type="checkbox" checked={showHighlight} onChange={event=>setShowHighlight(event.target.checked)}/>원문 위치 표시</label><button onClick={()=>{setPage(locationPage);setShowHighlight(true);requestAnimationFrame(center);}}>이 항목의 원문 위치</button></div>:target.blockId&&<p className="source-location-notice">{source.reason||'정확한 PDF 위치가 저장되어 있지 않습니다. 페이지를 직접 찾아 대조하세요.'}</p>}
        <div ref={viewport} className="source-page-viewport" tabIndex={0} role="region" aria-label="대조 PDF 페이지 스크롤">{url?<div className="source-page-canvas" style={{width:`${zoom}%`}}><img src={url} alt={`대조 원본 PDF ${page}쪽`} onLoad={center}/>{area&&<div ref={highlight} className="source-page-highlight" role="img" aria-label="대조 항목의 원본 영역" style={area}/>}</div>:<p role={pageError?'alert':'status'}>{pageError||'원본 페이지를 불러오는 중…'}</p>}</div>
      </>}
    </section><section className="source-current" aria-label="현재 리더 대조"><header><h3>{block?.superseded_by?'보존된 이전 원문 · 기존 번역':'현재 리더'}</h3>{block&&<button onClick={()=>onJump(block.id)}>본문 이동</button>}</header><div className="source-current-scroll">
      {!current.length&&<p className="source-empty">{doc.source_kind==='arxiv_html'?'HTML 원문은 PDF 페이지와 연결되어 있지 않습니다. 본문 항목의 원문 대조에서 나란히 확인하세요.':'이 페이지에 연결된 본문 항목이 없습니다. 원본 페이지를 직접 확인하세요.'}</p>}
      {current.map(value=><article key={value.id} className="source-current-block">{!block&&<header><strong>{value.n||'본문'}</strong><button onClick={()=>onJump(value.id)}>본문 이동</button></header>}<div className="source-reader"><ReaderBlock block={value} historical/></div></article>)}
    </div></section></div><footer className="diagnostics-footer"><span>대조 중에는 원문·번역·메모를 변경하지 않습니다.</span><button onClick={onClose}>닫기</button></footer>
  </dialog>;
}
