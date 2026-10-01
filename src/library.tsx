import { useEffect, useMemo, useRef, useState } from 'react';
import { App as Reader } from './reader';
import { Tutorial, lastTutorialScene } from './tutorial';
import { AppSettings, ArxivInput, type UpdateInfo } from './m4';
import { invoke } from '@tauri-apps/api/core';
import { ERROR_TEXT, authorizedFetch, request } from './api';
import type { Job, LibraryItem, PipelineOptions } from './types';
import { DEFAULTS, Modal, ModelChoices, ProviderPanel, type ProviderSettings } from './connections';
import { BrandMark, Icon } from './icons';
import { resolveTheme } from './theme';

// opened_at is SQLite CURRENT_TIMESTAMP ("YYYY-MM-DD HH:MM:SS", UTC).
const RELATIVE = new Intl.RelativeTimeFormat('ko', { numeric: 'auto' });
function since(value?: string) {
  const time = value ? Date.parse(value.replace(' ', 'T') + 'Z') : NaN;
  if (Number.isNaN(time)) return '';
  const seconds = (time - Date.now()) / 1000, abs = Math.abs(seconds);
  if (abs < 60) return '방금';
  if (abs < 3600) return RELATIVE.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return RELATIVE.format(Math.round(seconds / 3600), 'hour');
  return RELATIVE.format(Math.round(seconds / 86400), 'day');
}

type SortKey='recent'|'added'|'title';
const PAGE_SIZE=10;
// First, last, and the pages around the current one; gaps in between.
function pageNumbers(page:number,pages:number){
  const keep=[...new Set([1,page-1,page,page+1,pages])].filter(p=>p>=1&&p<=pages).sort((a,b)=>a-b);
  return keep.flatMap((p,i)=>i&&p-keep[i-1]>1?['gap' as const,p]:[p]);
}
function Pagination({page,pages,onPage}:{page:number;pages:number;onPage:(page:number)=>void}){
  return <nav className="pagination" aria-label="논문 목록 페이지">
    <button aria-label="이전 페이지" disabled={page===1} onClick={()=>onPage(page-1)}><Icon name="chevronLeft"/></button>
    {pageNumbers(page,pages).map((p,i)=>p==='gap'?<span key={`gap-${i}`} className="pagination-gap" aria-hidden="true">…</span>:
      <button key={p} aria-label={`${p}페이지`} aria-current={p===page?'page':undefined} onClick={()=>onPage(p)}>{p}</button>)}
    <button aria-label="다음 페이지" disabled={page===pages} onClick={()=>onPage(page+1)}><Icon name="chevronRight"/></button>
  </nav>;
}
const LOAD_ERROR='서재를 불러오지 못했습니다. 다시 시도해 주세요.';
const LABELS: Record<string,string>={fixture:'샘플',queued:'처리 대기',running:'처리 중',batch_waiting:'제공자 대기 중',awaiting_ai:'AI 연결 필요',ready_to_translate:'번역 준비됨',paused:'일시정지',failed:'처리 실패',review:'검수 필요',complete:'완료'};
const STAGES: Record<string,string>={Ingest:'파일 확인',Extract:'원문 추출',Structure:'구조 정리',Restore:'표·수식 복원',Glossary:'용어집',Translate:'전문 번역',Annotate:'맥락 주석',Validate:'검증',Render:'리더 완성'};
export function Workspace() {
  const initial=new URLSearchParams(location.search);
  const [screen,setScreen]=useState(initial.has('tutorial')?'tutorial':initial.has('doc')?'reader':'library');
  const [tutorialScene,setTutorialScene]=useState(initial.get('tutorial')||lastTutorialScene());
  const [docId,setDocId]=useState(initial.get('doc')||'rex-omni');
  const [items,setItems]=useState<LibraryItem[]>([]);
  const [selected,setSelected]=useState<string|null>(null);
  const [removing,setRemoving]=useState<LibraryItem|null>(null);
  // Saved papers: title search, sort (remembered on this PC), ten per page.
  const [query,setQuery]=useState('');const [page,setPage]=useState(1);
  const [sort,setSort]=useState<SortKey>(()=>{try{const saved=localStorage.getItem('paperduet-library-sort');return saved==='added'||saved==='title'?saved:'recent';}catch{return 'recent';}});
  const matched=useMemo(()=>{const q=query.trim().toLocaleLowerCase();const list=q?items.filter(i=>i.title.toLocaleLowerCase().includes(q)):[...items];
    // 'recent' keeps the library's own order: last opened, else last added.
    if(sort==='title')list.sort((a,b)=>a.title.localeCompare(b.title,'ko',{numeric:true}));else if(sort==='added')list.sort((a,b)=>(b.created_at??'').localeCompare(a.created_at??''));
    return list;},[items,query,sort]);
  const pages=Math.max(1,Math.ceil(matched.length/PAGE_SIZE));const current=Math.min(page,pages);
  const pageItems=matched.slice((current-1)*PAGE_SIZE,current*PAGE_SIZE);
  const [settings,setSettings]=useState(false);
  const [appSettings,setAppSettings]=useState(false);const [update,setUpdate]=useState<UpdateInfo|null>(null);
  // Closing the notice lasts until the app restarts; it returns on every launch until updated.
  const [updateDismissed,setUpdateDismissed]=useState(false);const updateAvailable=!!update?.available;
  useEffect(()=>{if(localStorage.getItem('paperduet-auto-update')!=='false')void invoke<UpdateInfo>('check_update').then(setUpdate).catch(()=>{});},[]);
  const [onboarding,setOnboarding]=useState(false);
  useEffect(()=>{let stopped=false;const load=async()=>{for(let attempt=0;attempt<60&&!stopped;attempt++){try{const saved=await request<{theme?:string}>('/settings/reader');if(!stopped&&!document.documentElement.dataset.theme)document.documentElement.dataset.theme=resolveTheme(saved.theme);return;}catch{await new Promise(resolve=>setTimeout(resolve,500));}}};void load();return()=>{stopped=true;};},[]);
  useEffect(()=>{let stopped=false;const load=async()=>{for(let attempt=0;attempt<60&&!stopped;attempt++){try{const value=await request<{completed:boolean}>('/settings/onboarding');if(!stopped)setOnboarding(!value.completed&&!new URLSearchParams(location.search).has('tutorial'));return;}catch{await new Promise(resolve=>setTimeout(resolve,500));}}};void load();return()=>{stopped=true;};},[]);
  const finishOnboarding=async()=>{await request('/settings/onboarding',{method:'PUT',body:JSON.stringify({completed:true})});setOnboarding(false);};
  const [uploading,setUploading]=useState(false);
  const [error,setError]=useState('');
  const [drag,setDrag]=useState(false);
  const input=useRef<HTMLInputElement>(null);
  const uploadLock=useRef(false);
  const jobs=useJobTracker(()=>{if(screen==='library')void refresh();});
  const [guideSeen,setGuideSeen]=useState(()=>{try{return localStorage.getItem('paperduet-guide-seen')==='1';}catch{return false;}});
  const navigate=(view:string,id=docId)=>{
    setScreen(view);setDocId(id);setError('');
    const guide=lastTutorialScene();if(view==='tutorial'){setOnboarding(false);setTutorialScene(guide);setGuideSeen(true);try{localStorage.setItem('paperduet-guide-seen','1');}catch{/* The banner simply shows again. */}}
    history.pushState(null,'',view==='tutorial'?`?tutorial=${guide}`:view==='library'?'?library=1':`?doc=${encodeURIComponent(id)}`);
    window.scrollTo(0,0);
  };
  const hasSample=items.some(item=>item.id==='rex-omni');
  // Until the user adds a paper the page leads with import; afterwards it leads with the list.
  const firstRun=!items.some(item=>item.status!=='fixture');
  const recent=items.find(item=>item.opened_at&&item.block_count>0);
  const arxiv=<ArxivInput onImported={id=>{setSelected(id);setPage(1);void refresh();}}/>;
  const tutorialLink=<button className="library-tutorial-link" onClick={()=>navigate('tutorial')}><b>?</b><span><strong>처음이라면, 사용 가이드부터</strong><small>AI 연결부터 번역·질문·발표 준비까지 10개 설명으로 알아보세요.</small></span><Icon name="arrowRight"/></button>;
  const openSample=()=>hasSample?navigate('reader','rex-omni'):navigate('library');
  const [loading,setLoading]=useState(true);
  // Right after launch the sidecar may still be booting, so the first load retries quietly.
  const refresh=async(retries=0,alive=()=>true)=>{for(let attempt=0;alive();attempt++){try{const list=await request<LibraryItem[]>('/documents');if(alive()){setItems(list);setError(e=>e===LOAD_ERROR?'':e);setLoading(false);}return;}catch{if(attempt>=retries){if(alive()){setError(LOAD_ERROR);setLoading(false);}return;}await new Promise(resolve=>setTimeout(resolve,500));}}};
  useEffect(()=>{const pop=()=>{const p=new URLSearchParams(location.search);setScreen(p.has('tutorial')?'tutorial':p.has('doc')?'reader':'library');setTutorialScene(p.get('tutorial')||lastTutorialScene());setDocId(p.get('doc')||'rex-omni');};window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
  useEffect(()=>{if(screen==='reader')return;let alive=true;void refresh(120,()=>alive);return()=>{alive=false;};},[screen]);
  const upload=async(file?:File)=>{
    if(!file||uploadLock.current)return;
    if(!file.name.toLowerCase().endsWith('.pdf')){setError('PDF 파일을 선택해 주세요.');return;}
    if(file.size>150*1024*1024){setError('PDF는 150MB 이하만 지원합니다.');return;}
    uploadLock.current=true;setUploading(true);setError('');
    try{const result=await request<{doc_id:string}>('/documents',{method:'POST',body:file,headers:{'Content-Type':'application/pdf'}});setSelected(result.doc_id);setPage(1);await refresh();}
    catch(e){setError((e as Error).message);}finally{uploadLock.current=false;setUploading(false);if(input.current)input.current.value='';}
  };
  return <>{screen==='tutorial'?<Tutorial sceneId={tutorialScene} hasSample={hasSample} onScene={id=>{setTutorialScene(id);history.pushState(null,'',`?tutorial=${id}`);}} onGo={target=>{if(target==='connection')setSettings(true);else if(target==='settings')setAppSettings(true);else if(target==='sample')openSample();else navigate('library');}}/>:screen==='reader'?<Reader key={docId} docId={docId} onLibrary={()=>navigate('library')} onTutorial={()=>navigate('tutorial')} onProcess={()=>{setSelected(docId);navigate('library');}}/>:
    <div className={`library-page ${drag?'dragging':''}`} onDragOver={e=>{if(!e.dataTransfer.types.includes('Files'))return;e.preventDefault();setDrag(true);}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))setDrag(false);}} onDrop={e=>{e.preventDefault();setDrag(false);void upload(e.dataTransfer.files[0]);}}>
      <header className="topbar library-bar"><div className="toolbar"><a className="brand" href="?library=1" onClick={e=>e.preventDefault()}><BrandMark/><b>PaperDuet</b></a>
        <div className="appbar-actions"><button onClick={()=>navigate('tutorial')}>사용 가이드</button><button onClick={()=>setSettings(true)}>AI 연결 설정</button><button onClick={()=>setAppSettings(true)}>{updateAvailable?'새 업데이트 · 앱 설정':'앱 설정'}</button>
          <button className="primary-button" disabled={uploading} onClick={()=>input.current?.click()}>{uploading?<span className="spinner" aria-hidden="true"/>:<Icon name="plus"/>}논문 추가</button></div></div></header>
      {updateAvailable&&!updateDismissed&&<div className="update-banner" role="status"><Icon name="arrowUp"/><span><b>새 버전 {update!.version}</b>이 나왔습니다. 지금 쓰는 버전은 {update!.current_version}입니다.</span><button className="update-banner-action" onClick={()=>setAppSettings(true)}>업데이트</button><button className="icon-button update-banner-close" aria-label="업데이트 알림 닫기" title="닫기" onClick={()=>setUpdateDismissed(true)}><Icon name="close"/></button></div>}
      <input ref={input} type="file" accept=".pdf,application/pdf" aria-label="PDF 파일" hidden onChange={e=>void upload(e.target.files?.[0])}/>
      <main className="library-main"><h1>나의 논문 서재</h1>
      <p className="library-intro">{loading&&!items.length?'서재를 불러오는 중…':firstRun?<>한 편의 논문을, 처음부터 끝까지.<br/>AI를 연결하고 한국어 대역 리더를 만드세요.</>:`이 PC에 저장된 논문 ${items.length}편`}</p>
      {!guideSeen&&firstRun&&tutorialLink}
      {error&&<p className="error-message" role="alert">{error}<button onClick={()=>void refresh(20)}>다시 불러오기</button></p>}
      {firstRun?<><section className="upload-zone" aria-label="PDF 업로드">
        <div className="upload-icon"><Icon name="upload"/></div><h2>{uploading?<><span className="spinner" aria-hidden="true"/>PDF를 가져오고 있습니다…</>:'PDF를 여기에 놓으세요'}</h2><p>최대 150MB · 원본은 이 PC에 저장됩니다.</p><button className="primary-button" disabled={uploading} onClick={()=>input.current?.click()}>PDF 파일 선택</button><small>AI 연결 전에도 원문을 읽을 수 있습니다. 번역은 예상 사용량을 확인한 후 시작합니다.</small>
      </section>{arxiv}</>:recent&&<section className="continue-card" aria-label="이어 읽기"><div className="continue-cover" aria-hidden="true">{recent.title}</div><div className="continue-body"><span className="continue-label">이어 읽기</span><p className="continue-title">{recent.title}</p><p>{since(recent.opened_at)} 마지막으로 읽음</p></div><button className="primary-button" onClick={()=>navigate('reader',recent.id)}>이어서 읽기<Icon name="arrowRight"/></button></section>}
      <div className="library-section-title"><h2>저장한 논문 <span>{items.length}</span></h2><div className="library-tools"><label className="library-search"><Icon name="search"/><input type="search" aria-label="논문 제목 검색" placeholder="제목 검색" value={query} onChange={e=>{setQuery(e.target.value);setPage(1);}}/></label><select aria-label="정렬" value={sort} onChange={e=>{const next=e.target.value as SortKey;setSort(next);setPage(1);try{localStorage.setItem('paperduet-library-sort',next);}catch{/* The order simply resets next time. */}}}><option value="recent">최근 읽은 순</option><option value="added">최근 추가 순</option><option value="title">제목 순</option></select><button onClick={()=>void refresh()}>새로고침</button></div></div>
      {query.trim()&&<p className="library-result" role="status">{matched.length?`‘${query.trim()}’ 검색 결과 ${matched.length}편`:`‘${query.trim()}’와 일치하는 논문이 없습니다.`}</p>}
      <div className="paper-list">{pageItems.map(item=>{const job=jobs.active[item.id];const status=job?.status??item.status;return <article className="paper-item" key={item.id}><div className="paper-item-content"><h3>{item.title}</h3><p>{item.status==='fixture'?'Rex-Omni · 대역과 주석이 준비된 샘플':`${item.page_count}쪽 · ${item.block_count}블록`}{item.opened_at?` · ${since(item.opened_at)} 읽음`:''}</p></div><span className={`state-badge state-${status}`}>{job&&<span className="spinner" aria-hidden="true"/>}{LABELS[status]??status}{job?` · ${Math.round(job.progress*100)}%`:''}</span><div className="paper-actions"><button disabled={!item.block_count} onClick={()=>navigate('reader',item.id)}>논문 열기</button>{item.status!=='fixture'&&<button onClick={()=>setSelected(item.id)}>처리 상태</button>}<button className="icon-button paper-delete" aria-label={`${item.title} 삭제`} title="논문 삭제" onClick={()=>setRemoving(item)}><Icon name="trash"/></button></div></article>;})}</div>
      {pages>1&&<Pagination page={current} pages={pages} onPage={p=>{setPage(p);document.querySelector('.library-section-title')?.scrollIntoView({block:'start'});}}/>}
      {!firstRun&&<section className="import-panel" aria-label="논문 가져오기"><div className="import-drop"><Icon name="upload"/><span><b>{uploading?'PDF를 가져오고 있습니다…':'PDF를 끌어다 놓거나'}</b> 파일을 선택하세요 · 최대 150MB</span><button disabled={uploading} onClick={()=>input.current?.click()}>PDF 파일 선택</button></div>{arxiv}</section>}
      {!guideSeen&&!firstRun&&tutorialLink}
      <p className="library-footnote">번역 언어는 한국어입니다. 참고문헌은 원문을 유지합니다.</p></main></div>}
    {selected&&<ProgressPanel docId={selected} onClose={()=>{setSelected(null);jobs.wake();void refresh();}} onRead={()=>{const id=selected;setSelected(null);navigate('reader',id);}} onSettings={()=>setSettings(true)}/>}
    <JobBanner active={Object.values(jobs.active)} settled={jobs.settled} hidden={selected} onOpen={id=>setSelected(id)} onRead={id=>navigate('reader',id)} onDismiss={jobs.dismiss}/>
    {onboarding&&<ProviderPanel onboarding hasSample={hasSample} onGuide={()=>navigate('tutorial')} onClose={()=>void finishOnboarding()} onComplete={()=>{void finishOnboarding();openSample();}}/>}
    {removing&&<DeleteDialog item={removing} running={!!jobs.active[removing.id]} onClose={()=>setRemoving(null)} onDeleted={()=>{const id=removing.id;setRemoving(null);if(selected===id)setSelected(null);jobs.dismiss(id);void refresh();}}/>}
    {appSettings&&<AppSettings initialUpdate={update??undefined} onClose={()=>setAppSettings(false)}/>}
    {settings&&<ProviderPanel onClose={()=>setSettings(false)}/>}
  </>;
}

type Tracked={id:string;title:string;status:string;stage:string;progress:number};
const ACTIVE=new Set(['queued','running','batch_waiting']);
const DONE_TEXT:Record<string,string>={complete:'번역·주석 완료',review:'완료 · 검수할 부분이 있습니다',paused:'일시정지됨',failed:'처리 중단',awaiting_ai:'원문 추출 완료 · AI 연결 후 번역할 수 있습니다',ready_to_translate:'원문 추출 완료 · 번역을 시작할 수 있습니다'};
const OK_STATES=new Set(['complete','review','ready_to_translate','awaiting_ai']);
// Polls the local library so running jobs stay visible after their dialog closes.
function useJobTracker(onSettled:()=>void){
  const [active,setActive]=useState<Record<string,Tracked>>({});const [settled,setSettled]=useState<Tracked[]>([]);
  const previous=useRef<Record<string,Tracked>>({});const settledRef=useRef(onSettled);settledRef.current=onSettled;const wake=useRef<()=>void>(()=>{});
  useEffect(()=>{let stopped=false;let timer:ReturnType<typeof setTimeout>;
    const tick=async()=>{try{
      const items=await request<LibraryItem[]>('/documents');const next:Record<string,Tracked>={};
      await Promise.all(items.filter(i=>ACTIVE.has(i.status)).map(async i=>{const job=await request<Job>(`/documents/${i.id}/job`).catch(()=>null);next[i.id]={id:i.id,title:i.title,status:job?.status??i.status,stage:job?.stage??'',progress:job?.progress??0};}));
      if(stopped)return;
      const ended=Object.values(previous.current).filter(t=>!next[t.id]&&items.some(i=>i.id===t.id)).map(t=>({...t,status:items.find(i=>i.id===t.id)?.status??'complete',progress:1}));
      if(ended.length){setSettled(list=>[...list.filter(s=>!ended.some(e=>e.id===s.id)),...ended]);settledRef.current();}
      previous.current=next;setActive(next);
    }catch{/* Sidecar not ready yet; retry on the next tick. */}
    if(!stopped)timer=setTimeout(()=>void tick(),Object.keys(previous.current).length?1200:4000);};
    wake.current=()=>{clearTimeout(timer);void tick();};
    void tick();return()=>{stopped=true;clearTimeout(timer);};
  },[]);
  return {active,settled,dismiss:(id:string)=>setSettled(list=>list.filter(s=>s.id!==id)),wake:()=>wake.current()};
}

function JobBanner({active,settled,hidden,onOpen,onRead,onDismiss}:{active:Tracked[];settled:Tracked[];hidden:string|null;onOpen:(id:string)=>void;onRead:(id:string)=>void;onDismiss:(id:string)=>void}){
  const running=active.filter(t=>t.id!==hidden);const finished=settled.filter(t=>t.id!==hidden);
  if(!running.length&&!finished.length)return null;
  return <div className="job-banner" role="status" aria-live="polite">
    {running.map(t=><div key={t.id} className="job-toast"><span className="spinner" aria-hidden="true"/><div className="job-toast-body"><b title={t.title}>{t.title}</b><span>{t.status==='queued'?'처리 대기':t.status==='batch_waiting'?'절약 모드 · 제공자 서버에서 처리 중':STAGES[t.stage]??t.stage} · {Math.round(t.progress*100)}%</span><progress aria-label={`${t.title} 처리 진행률`} max={1} value={t.progress}/></div><button onClick={()=>onOpen(t.id)}>자세히</button></div>)}
    {finished.map(t=><div key={t.id} className={`job-toast job-${t.status}`}><span className="job-icon" aria-hidden="true">{OK_STATES.has(t.status)?'✓':'!'}</span><div className="job-toast-body"><b title={t.title}>{t.title}</b><span>{DONE_TEXT[t.status]??LABELS[t.status]??t.status}</span></div>{t.status==='complete'||t.status==='review'?<button className="primary-button" onClick={()=>{onDismiss(t.id);onRead(t.id);}}>읽기</button>:<button onClick={()=>{onDismiss(t.id);onOpen(t.id);}}>{t.status==='ready_to_translate'||t.status==='awaiting_ai'?'번역 시작하기':'상태 보기'}</button>}<button className="job-close" aria-label="알림 닫기" onClick={()=>onDismiss(t.id)}><Icon name="close"/></button></div>)}
  </div>;
}

function DeleteDialog({item,running,onClose,onDeleted}:{item:LibraryItem;running:boolean;onClose:()=>void;onDeleted:()=>void}){
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const remove=async()=>{setBusy(true);setError('');try{await request(`/documents/${encodeURIComponent(item.id)}`,{method:'DELETE'});onDeleted();}catch(e){setError((e as Error).message);setBusy(false);}};
  return <Modal title="논문 삭제" onClose={()=>{if(!busy)onClose();}}><div className="dialog-content">
    <p className="delete-title">‘{item.title}’</p>
    <p>이 논문을 서재에서 삭제할까요? 원문 PDF와 번역·주석, AI 대화, 발표 노트가 모두 지워지며 되돌릴 수 없습니다.</p>
    {running&&<p>진행 중인 번역·주석 처리도 함께 중지됩니다.</p>}
    {item.status==='fixture'&&<p>샘플 논문은 삭제하면 다시 불러올 수 없습니다.</p>}
    {error&&<p role="alert" className="error-message">{error}</p>}
    <div className="dialog-actions"><button className="danger-button" disabled={busy} onClick={()=>void remove()}>{busy&&<span className="spinner" aria-hidden="true"/>}삭제</button><button disabled={busy} onClick={onClose}>취소</button></div>
  </div></Modal>;
}

const USAGE_STAGES:Record<string,string>={table:'표 복원',equation:'수식 복원',glossary:'용어집',translate:'전문 번역',annotate:'맥락 주석'};
function UsageSummary({usage}:{usage:Job['usage']}){
  const total=usage.reduce((sum,u)=>sum+u.tokens_in+u.tokens_out,0);const calls=usage.reduce((sum,u)=>sum+(u.calls??0),0);const cached=usage.reduce((sum,u)=>sum+(u.cache_read??0)+(u.cache_write??0),0);
  return <details className="usage-summary"><summary>실제 AI 사용량 · 합계 {total.toLocaleString()} 토큰{calls?` · ${calls}회 호출`:''}</summary><table><thead><tr><th>단계 · 모델</th><th>호출</th><th>입력</th><th>출력</th></tr></thead><tbody>{usage.map(u=><tr key={u.stage+u.model+(u.batch??0)}><td>{USAGE_STAGES[u.stage]??u.stage}<small>{u.model}{u.batch?' · 절약 모드(50% 할인)':''}</small></td><td>{u.calls??'–'}{u.requests&&u.calls&&u.requests>u.calls?<small>재시도 {u.requests-u.calls}</small>:null}</td><td>{u.tokens_in.toLocaleString()}{(u.cache_read||u.cache_write)?<small>캐시 {((u.cache_read??0)+(u.cache_write??0)).toLocaleString()}</small>:null}</td><td>{u.tokens_out.toLocaleString()}</td></tr>)}</tbody></table>{cached>0&&<small>입력에는 캐시로 재사용한 토큰(공식 CLI의 기본 지시문 등) {cached.toLocaleString()}개가 포함됩니다.</small>}</details>;
}

function submittedAgo(seconds:number){
  const minutes=Math.round((Date.now()/1000-seconds)/60);
  return minutes<1?'방금':minutes<60?`${minutes}분 전`:`${Math.floor(minutes/60)}시간 ${minutes%60}분 전`;
}

function ProgressPanel({docId,onClose,onRead,onSettings}:{docId:string;onClose:()=>void;onRead:()=>void;onSettings:()=>void}) {
  const [job,setJob]=useState<Job|null>(null);const [error,setError]=useState('');const [revision,setRevision]=useState(0);
  const [estimate,setEstimate]=useState<{input_tokens:number;output_tokens:number;restore_regions:number}|null>(null);
  const [options,setOptions]=useState(DEFAULTS);const [busy,setBusy]=useState(false);
  useEffect(()=>{void request<ProviderSettings>('/settings/providers').then(s=>setOptions(s.options)).catch(()=>{});},[]);
  useEffect(()=>{
    const controller=new AbortController();let retry:ReturnType<typeof setTimeout>;
    const stream=async()=>{try{
      const response=await authorizedFetch(`/documents/${docId}/progress`,{signal:controller.signal});
      if(!response.ok||!response.body)throw new Error();
      setError('');const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
      for(;;){const result=await reader.read();if(result.done)break;buffer+=decoder.decode(result.value,{stream:true});
        let end;while((end=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,end);buffer=buffer.slice(end+2);const data=event.split('\n').find(l=>l.startsWith('data: '));if(data)setJob(JSON.parse(data.slice(6)));}}
    }catch{if(!controller.signal.aborted){setError('로컬 연결을 다시 확인하고 있습니다…');retry=setTimeout(()=>void stream(),1500);}}};
    void stream();return()=>{controller.abort();clearTimeout(retry);};
  },[docId,revision]);
  useEffect(()=>{if(job&&job.status!=='running'&&job.status!=='queued')void request<typeof estimate>(`/documents/${docId}/estimate`).then(setEstimate).catch(()=>{});},[docId,job?.status]);
  const active=job?.status==='running'||job?.status==='queued'||job?.status==='batch_waiting';
  const saving=job?.checkpoint.options?.batch;const waiting=job?.status==='batch_waiting'?job.checkpoint.pending_batch:null;
  const cli=options.mode==='cli';
  const action=async(path:string,body?:PipelineOptions)=>{setBusy(true);setError('');try{await request(`/documents/${docId}/${path}`,{method:'POST',body:body?JSON.stringify(body):undefined});setRevision(v=>v+1);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <Modal title="논문 처리" onClose={onClose}><div className="dialog-content"><p className="pipeline-state">{(active||!job)&&<span className="spinner" aria-hidden="true"/>}{job?LABELS[job.status]:'상태 확인 중…'}</p><progress aria-label="논문 처리 진행률" max={1} value={job?.progress??0}/><p>{job?`${STAGES[job.stage]??job.stage} · ${Math.round(job.progress*100)}%`:''}</p>
    <ol className="pipeline-stages">{Object.entries(STAGES).map(([stage,label])=><li key={stage} className={job?.stage===stage?'current':job?.checkpoint.completed_stages?.includes(stage)?'done':''}><span>{job?.checkpoint.completed_stages?.includes(stage)?'✓':'○'}</span>{label}</li>)}</ol>
    {job?.checkpoint.extracted_pages!==undefined&&<p>추출한 페이지 {job.checkpoint.extracted_pages}쪽{job.checkpoint.total_blocks?` · 번역 ${job.checkpoint.translated_count??0}/${job.checkpoint.total_blocks}블록`:''}</p>}
    {job?.checkpoint.annotation_total!==undefined&&<p>주석 완료 {job.checkpoint.annotation_count??0}/{job.checkpoint.annotation_total}개 섹션 묶음</p>}
    {waiting&&<div className="pipeline-notice"><b>절약 모드 · 제공자 서버에서 처리 중</b><p>{STAGES[job!.stage]??job!.stage} 요청 {waiting.count}건을 {submittedAgo(waiting.submitted_at)} 제출했습니다. 보통 1시간 이내, 늦으면 최대 24시간 걸립니다. 앱을 꺼도 제공자 서버에서 계속 처리되고, 다시 켜면 결과를 가져와 이어서 진행합니다.</p><button disabled={busy} onClick={()=>void action('realtime')}>남은 부분 바로 처리</button><small>제출한 요청을 취소하고 남은 부분을 기본 요금으로 바로 처리합니다. 이미 끝난 결과는 그대로 씁니다.</small></div>}
    {active&&saving&&!waiting&&<p className="transfer-note">절약 모드로 처리 중입니다. 다음 단계 요청을 제공자에게 보내고 있습니다.</p>}
    {job?.status==='awaiting_ai'&&<div className="pipeline-notice">텍스트 추출이 끝났습니다. 아직 완성된 대역 리더가 아닙니다. AI를 연결해 표·수식 복원, 번역과 주석을 만들어 주세요.<button onClick={onSettings}>AI 연결 설정</button></div>}
    {estimate&&!active&&<><div className="token-estimate"><b>예상 사용량</b><p>입력 약 {estimate.input_tokens.toLocaleString()} · 출력 약 {estimate.output_tokens.toLocaleString()} 토큰</p><small>문자 수와 이미지 수를 바탕으로 한 추정치입니다. 재시도·표 복원에 따라 실제 사용량이 달라집니다.</small></div><ModelChoices options={options} setOptions={setOptions}/><fieldset className="run-mode"><legend>처리 방식</legend>
      <label><input type="radio" name="run-mode" checked={!options.batch||cli} onChange={()=>setOptions({...options,batch:false})}/><span><b>바로 처리</b><small>몇 분 안에 끝납니다 · 기본 요금</small></span></label>
      <label><input type="radio" name="run-mode" checked={!!options.batch&&!cli} disabled={cli} onChange={()=>setOptions({...options,batch:true})}/><span><b>절약 모드</b><small>토큰 요금 50% 할인 · 보통 1시간 이내, 최대 24시간</small></span></label>
      {cli&&<small>절약 모드는 API 키로 연결했을 때만 쓸 수 있습니다.</small>}</fieldset><p className="transfer-note">번역·주석 시작 시 본문과 표·수식 이미지가 선택한 제공자·모델로 전송됩니다. API 요금 또는 공식 CLI의 사용량 한도가 적용됩니다.</p></>}
    {job?.checkpoint.error&&<p role="alert" className="error-message">{ERROR_TEXT[job.checkpoint.error]??'처리가 중단되었습니다. 다시 시도해 주세요.'}</p>}{error&&<p role="alert" className="error-message">{error}</p>}
    {!!job?.usage.length&&<UsageSummary usage={job.usage}/>}
    <div className="dialog-actions"><button disabled={!job?.checkpoint.completed_stages?.includes('Structure')} onClick={onRead}>원문·대역 읽기</button>{active?<button disabled={busy} title={waiting?'제출한 요청을 취소합니다. 이미 끝난 결과는 보관됩니다.':undefined} onClick={()=>void action('pause')}>일시정지</button>:job&& (!['complete','review'].includes(job.status)||!job.checkpoint.completed_stages?.includes('Annotate'))&&<button className="primary-button" disabled={busy} onClick={()=>void action('resume',{...options,batch:!!options.batch&&!cli})}>{['complete','review'].includes(job.status)?'주석 이어서 만들기':job.status==='paused'||job.status==='failed'?'이어서 실행':'번역·주석 시작'}</button>}</div><small>창을 닫아도 처리는 백그라운드에서 계속되고, 화면 위쪽에 진행률이 표시됩니다. 앱을 종료하면 저장된 단계부터 다음 실행 때 재개합니다.</small>
  </div></Modal>;
}
