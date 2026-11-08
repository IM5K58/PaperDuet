import { useCallback, useEffect, useRef, useState } from 'react';
import { ERROR_TEXT, request } from './api';
import { combineSignals } from './abort';
import { Icon } from './icons';
import { useDialogDismissal } from './dialog-dismiss';
import { ReanalysisDialog, type ReanalysisApplied, type ReanalysisPreview } from './reanalysis';
import type { Document } from './types';
import './styles/ocr.css';

export interface OcrPage { page:number; eligible:boolean; has_text:boolean; recognized_blocks:number }
export interface OcrCatalog { available:boolean; languages:string[]; max_pages:number; pages:OcrPage[]; notices:string[]; busy:boolean; source_extraction_required?:boolean }
const LANGUAGES:Record<string,string>={eng:'영어',kor:'한국어','eng+kor':'영어 + 한국어'};

export function OcrDialog({doc,initialPages=[],onClose,onApplied}:{doc:Document;initialPages?:number[];onClose:()=>void;onApplied:(value:ReanalysisApplied)=>void}) {
  const [preview,setPreview]=useState<ReanalysisPreview|null>(null),[defaults,setDefaults]=useState({pages:initialPages,language:'eng'});
  return preview?<ReanalysisDialog doc={doc} initialPreview={preview} onClose={onClose} onApplied={onApplied} onRestart={()=>{setDefaults({pages:preview.ocr?.pages||defaults.pages,language:preview.ocr?.language||defaults.language});setPreview(null);}}/>:<OcrSelectionDialog doc={doc} initialPages={defaults.pages} initialLanguage={defaults.language} onClose={onClose} onResult={setPreview}/>;
}

function OcrSelectionDialog({doc,initialPages,initialLanguage,onClose,onResult}:{doc:Document;initialPages:number[];initialLanguage:string;onClose:()=>void;onResult:(preview:ReanalysisPreview)=>void}) {
  const ref=useRef<HTMLDialogElement>(null),alive=useRef(true),metadataRequest=useRef<AbortController|null>(null),ocrRequest=useRef<AbortController|null>(null),initialized=useRef(false);
  const [catalog,setCatalog]=useState<OcrCatalog|null>(null),[loading,setLoading]=useState(true),[running,setRunning]=useState(false),[error,setError]=useState('');
  const [selected,setSelected]=useState<Set<number>>(new Set()),[language,setLanguage]=useState(initialLanguage),[previewPage,setPreviewPage]=useState<number|null>(null),[image,setImage]=useState(''),[imageError,setImageError]=useState('');
  const close=()=>{metadataRequest.current?.abort();ocrRequest.current?.abort();onClose();};const dismiss=useDialogDismissal(close);
  const load=useCallback(async()=>{
    metadataRequest.current?.abort();const abort=new AbortController();metadataRequest.current=abort;setLoading(true);setError('');
    try{
      const value=await request<OcrCatalog>(`/documents/${encodeURIComponent(doc.id)}/ocr`,{signal:combineSignals([abort.signal,AbortSignal.timeout(120000)])});
      if(!alive.current||abort.signal.aborted)return;setCatalog(value);setLanguage(current=>value.languages.includes(current)?current:value.languages.includes('eng')?'eng':value.languages[0]||'eng');
      const allowed=new Set(value.pages.filter(page=>page.eligible&&!page.has_text).map(page=>page.page));
      if(!initialized.current){const pages=[...new Set(initialPages)].filter(page=>allowed.has(page)).slice(0,value.max_pages);setSelected(new Set(pages));setPreviewPage(pages[0]||null);initialized.current=true;}
      else setSelected(current=>new Set([...current].filter(page=>allowed.has(page)).slice(0,value.max_pages)));
    }catch(failure){if(alive.current&&!abort.signal.aborted)setError((failure as Error).message);}
    finally{if(alive.current&&!abort.signal.aborted)setLoading(false);}
  },[doc.id,initialPages]);
  useEffect(()=>{
    alive.current=true;const dialog=ref.current,opener=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow;dialog?.showModal();document.body.style.overflow='hidden';void load();
    return()=>{alive.current=false;metadataRequest.current?.abort();ocrRequest.current?.abort();dialog?.close();document.body.style.overflow=overflow;opener?.focus({preventScroll:true});};
  },[load]);
  useEffect(()=>{
    const abort=new AbortController();setImage('');setImageError('');if(previewPage!==null)void request<{data_url:string}>(`/documents/${encodeURIComponent(doc.id)}/pages/${previewPage}/image`,{signal:abort.signal}).then(value=>{if(!abort.signal.aborted)setImage(value.data_url);}).catch(()=>{if(!abort.signal.aborted)setImageError('원본 페이지를 불러오지 못했습니다.');});return()=>abort.abort();
  },[doc.id,previewPage]);
  const max=catalog?.max_pages||20,eligible=catalog?.pages.filter(page=>page.eligible&&!page.has_text)||[];
  const disabled=loading||running||!catalog?.available||catalog.busy||catalog.source_extraction_required;
  const run=async()=>{
    if(disabled||!selected.size||selected.size>max)return;const abort=new AbortController();ocrRequest.current=abort;setRunning(true);setError('');
    try{const value=await request<ReanalysisPreview>(`/documents/${encodeURIComponent(doc.id)}/ocr`,{method:'POST',body:JSON.stringify({pages:[...selected].sort((a,b)=>a-b),language}),signal:combineSignals([abort.signal,AbortSignal.timeout(900000)])});if(alive.current&&!abort.signal.aborted)onResult(value);}
    catch(failure){if(alive.current&&!abort.signal.aborted){const value=failure as Error;setError(value.name==='TimeoutError'||value.name==='AbortError'?ERROR_TEXT.OCR_TIMEOUT:value.message);}}
    finally{if(alive.current&&!abort.signal.aborted)setRunning(false);}
  };
  return <dialog ref={ref} {...dismiss} className="ocr-dialog" aria-label="스캔 페이지 글자 읽기" onCancel={event=>{event.preventDefault();close();}}>
    <header className="ocr-heading"><div><h2>스캔 페이지 글자 읽기</h2><p>필요한 쪽만 선택해 이미지에서 글자를 읽습니다.</p></div><button className="icon-button" aria-label="글자 읽기 닫기" onClick={close}><Icon name="close"/></button></header>
    <div className="ocr-content"><p className="ocr-preservation">이 PC에서 글자를 읽으며 번역 AI를 호출하지 않습니다. 원본 PDF와 기존 번역·메모는 유지하고, 결과를 확인한 뒤 반영합니다.</p>
      {loading&&<p className="ocr-status" role="status"><span className="spinner"/>글자를 읽을 수 있는 페이지를 확인하고 있습니다…</p>}
      {error&&<div className="ocr-error" role="alert">{error}{!catalog&&!running&&<button onClick={()=>void load()}>다시 확인</button>}</div>}
      {catalog&&<>
        <div className="ocr-notices">{catalog.notices.map((notice,index)=><p key={index}>{notice}</p>)}</div>
        {!catalog.available&&<p className="ocr-notice">현재 이 PC에서 글자 읽기를 사용할 수 없습니다.</p>}
        {catalog.busy&&<p className="ocr-notice">다른 논문 처리가 진행 중입니다. 완료 후 다시 확인해 주세요.</p>}
        {running?<div className="ocr-running" role="status"><span className="spinner"/><strong>선택한 {selected.size}쪽에서 글자를 읽고 있습니다…</strong><p>완료 후 원본과 결과를 비교합니다. 창을 닫으면 결과를 적용하지 않습니다.</p></div>:<>
          <div className="ocr-options"><label>읽을 언어<select aria-label="글자 읽기 언어" disabled={disabled} value={language} onChange={event=>setLanguage(event.target.value)}>{catalog.languages.map(value=><option key={value} value={value}>{LANGUAGES[value]||value}</option>)}</select></label><span>한 번에 최대 {max}쪽</span><button disabled={disabled||!eligible.length} onClick={()=>setSelected(new Set(eligible.slice(0,max).map(page=>page.page)))}>{eligible.length>max?`앞에서 ${max}쪽 선택`:`읽기 가능한 ${eligible.length}쪽 선택`}</button><button disabled={disabled||!selected.size} onClick={()=>setSelected(new Set())}>선택 해제</button><button disabled={loading} onClick={()=>void load()}>다시 확인</button></div>
          <div className="ocr-columns"><section className="ocr-page-list" aria-label="글자 읽을 페이지"><p>글자가 없는 쪽에는 그림이나 도표만 있을 수도 있습니다. 원본을 확인하고 선택하세요.</p>{catalog.pages.map(page=><div className="ocr-page-row" key={page.page}><label><input type="checkbox" aria-label={`${page.page}쪽 글자 읽기 선택`} checked={selected.has(page.page)} disabled={disabled||!page.eligible||page.has_text||(!selected.has(page.page)&&selected.size>=max)} onChange={event=>{const checked=event.target.checked;setSelected(current=>{const next=new Set(current);if(checked)next.add(page.page);else next.delete(page.page);return next;});}}/><b>{page.page}쪽</b><span>{page.has_text?'원문 텍스트 있음':page.recognized_blocks?`이미 읽은 원문 ${page.recognized_blocks}개`:page.eligible?'글자 읽기 가능':'선택할 수 없음'}</span></label><button aria-label={`${page.page}쪽 원본 보기`} onClick={()=>setPreviewPage(page.page)}>원본 보기</button></div>)}{!eligible.length&&<p className="ocr-empty">현재 글자를 읽을 수 있는 페이지가 없습니다.</p>}</section><section className="ocr-page-preview" aria-label="글자 읽기 원본 페이지"><h3>{previewPage?`원본 PDF ${previewPage}쪽`:'원본 페이지'}</h3>{previewPage===null?<p>페이지의 원본 보기를 누르면 여기에 표시합니다.</p>:image?<div className="ocr-image-scroll"><img src={image} alt={`글자 읽기 원본 PDF ${previewPage}쪽`} onError={()=>{setImage('');setImageError('원본 페이지를 표시하지 못했습니다.');}}/></div>:<p role={imageError?'alert':'status'}>{imageError||'원본 페이지를 불러오는 중…'}</p>}</section></div>
        </>}
      </>}
    </div><footer className="ocr-footer"><span role="status">{selected.size}/{max}쪽 선택</span><div><button onClick={close}>{running?'닫기 · 결과 미적용':'취소'}</button><button className="primary-button" disabled={disabled||!selected.size||selected.size>max||!catalog?.languages.length} onClick={()=>void run()}>선택한 {selected.size}쪽 글자 읽기</button></div></footer>
  </dialog>;
}
