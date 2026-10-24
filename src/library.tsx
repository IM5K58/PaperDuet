import { useEffect, useMemo, useRef, useState } from 'react';
import { readEvents } from './sse';
import { App as Reader } from './reader';
import { Tutorial, lastTutorialScene } from './tutorial';
import { AppSettings, ArxivInput, type UpdateInfo } from './m4';
import { invoke } from '@tauri-apps/api/core';
import { ERROR_TEXT, authorizedFetch, request } from './api';
import type { Job, LibraryItem, PipelineOptions } from './types';
import { DEFAULTS, Modal, ModelChoices, ProviderPanel, type ProviderSettings } from './connections';
import { BrandMark, Icon } from './icons';
import { resolveTheme } from './theme';

type SortKey='recent'|'added'|'title';
const shelfColumns=()=>window.matchMedia('(max-width:600px)').matches?3:window.matchMedia('(max-width:950px)').matches?5:7;
function useShelfSize(){
  const [columns,setColumns]=useState(shelfColumns);
  useEffect(()=>{const queries=[matchMedia('(max-width:600px)'),matchMedia('(max-width:950px)')];const resize=()=>setColumns(shelfColumns());queries.forEach(q=>q.addEventListener('change',resize));return()=>queries.forEach(q=>q.removeEventListener('change',resize));},[]);
  return columns*2;
}
function Pagination({page,pages,onPage}:{page:number;pages:number;onPage:(page:number)=>void}){
  return <nav className="pagination" aria-label="논문 목록 페이지">
    <button className="icon-button pagination-arrow" aria-label="이전 페이지" disabled={page===1} onClick={()=>onPage(page-1)}><Icon name="chevronLeft"/></button>
    {Array.from({length:pages},(_,i)=><button className="icon-button pagination-dot" key={i} aria-label={`${i+1}페이지`} aria-current={i+1===page?'page':undefined} onClick={()=>onPage(i+1)}/>)}
    <button className="icon-button pagination-arrow" aria-label="다음 페이지" disabled={page===pages} onClick={()=>onPage(page+1)}><Icon name="chevronRight"/></button>
  </nav>;
}

// Fetch with the session header; never put a bearer token in an image URL.
function PaperCover({item,ribbon=false}:{item:LibraryItem;ribbon?:boolean}){
  const [url,setUrl]=useState('');
  useEffect(()=>{if(!item.has_thumbnail)return;const controller=new AbortController();const reader=new FileReader();
    void authorizedFetch(`/documents/${encodeURIComponent(item.id)}/thumbnail`,{signal:controller.signal}).then(async response=>{
      if(!response.ok)return;const blob=await response.blob();if(controller.signal.aborted)return;
      // data: is already allowed by the desktop and web CSP, unlike blob:.
      reader.onload=()=>{if(!controller.signal.aborted)setUrl(String(reader.result));};reader.readAsDataURL(blob);
    }).catch(()=>{});return()=>{controller.abort();if(reader.readyState===FileReader.LOADING)reader.abort();setUrl('');};
  },[item.id,item.has_thumbnail]);
  return <span className={`paper-cover ${url?'has-image':''}`} aria-hidden="true">
    {url&&<img src={url} alt="" className="cover-image"/>}<span className="cover-title">{item.title}</span>
    <span className="cover-read"><i style={{width:`${item.read_percent??0}%`}}/></span>{ribbon&&<span className="cover-ribbon"/>}
  </span>;
}

function ShelfBook({item,job,active,onRead,onProcess,onDelete}:{item:LibraryItem;job?:Tracked;active:boolean;onRead:()=>void;onProcess:()=>void;onDelete:()=>void}){
  const [menu,setMenu]=useState(false);const ref=useRef<HTMLElement>(null);const menuButton=useRef<HTMLButtonElement>(null);
  const status=job?.status??item.status;const busy=ACTIVE.has(status);const failed=status==='failed';
  useEffect(()=>{setMenu(false);},[active]);
  useEffect(()=>{if(!menu)return;const close=(event:PointerEvent)=>{if(!ref.current?.contains(event.target as Node))setMenu(false);};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close);},[menu]);
  const percent=item.read_percent??0;
  return <article ref={ref} className={`shelf-book ${menu?'menu-open':''} ${busy||failed?'is-processing':''}`} onKeyDown={e=>{if(e.key==='Escape'){setMenu(false);menuButton.current?.focus();}else if(menu&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();const buttons=Array.from(ref.current!.querySelectorAll<HTMLButtonElement>('[role=menuitem]'));const index=buttons.indexOf(document.activeElement as HTMLButtonElement);buttons[(index+(e.key==='ArrowDown'?1:buttons.length-1)+buttons.length)%buttons.length]?.focus();}}}>
    <button className="book-open" aria-label={`${item.title} 읽기`} onClick={onRead}>
      <PaperCover item={item}/><span className="book-title">{item.title}</span>
      <span className="book-meta">{item.page_count}쪽{busy?' · 번역 중':percent>=100?' · 다 읽음':percent>0?` · ${percent}% 읽음`:''}</span>
    </button>
    {(busy||failed)&&<span className={`cover-badge ${failed?'failed':''}`} role="status">{busy&&<span className="spinner" aria-hidden="true"/>}{failed?'처리 실패':`${Math.round((job?.progress??0)*100)}%`}</span>}
    <button ref={menuButton} className="icon-button book-menu-button" aria-label={`${item.title} 메뉴`} aria-haspopup="menu" aria-expanded={menu} onClick={()=>setMenu(v=>!v)} onKeyDown={e=>{if(e.key==='ArrowDown'){e.preventDefault();setMenu(true);requestAnimationFrame(()=>ref.current?.querySelector<HTMLButtonElement>('[role=menuitem]')?.focus());}}}><Icon name="more"/></button>
    {menu&&<div className="book-menu" role="menu" aria-label={`${item.title} 메뉴`}><button role="menuitem" onClick={()=>{menuButton.current?.focus();setMenu(false);onProcess();}}><Icon name="activity"/>처리 상태</button><button className="book-delete" role="menuitem" onClick={()=>{menuButton.current?.focus();setMenu(false);onDelete();}}><Icon name="trash"/>삭제</button></div>}
  </article>;
}
const LOAD_ERROR='서재를 불러오지 못했습니다. 다시 시도해 주세요.';
const LABELS: Record<string,string>={fixture:'샘플',queued:'처리 대기',running:'처리 중',batch_waiting:'제공자 대기 중',awaiting_ai:'AI 연결 필요',ready_to_translate:'미번역',paused:'일시정지',failed:'처리 실패',review:'번역 완료',complete:'번역 완료'};
const STAGES: Record<string,string>={Ingest:'파일 확인',Extract:'원문 추출',Structure:'구조 정리',Restore:'표·수식 복원',Glossary:'용어집',Translate:'전문 번역',Annotate:'맥락 주석',Render:'리더 완성'};
export function Workspace() {
  const initial=new URLSearchParams(location.search);
  const [screen,setScreen]=useState(initial.has('tutorial')?'tutorial':initial.has('doc')?'reader':'library');
  const [tutorialScene,setTutorialScene]=useState(initial.get('tutorial')||lastTutorialScene());
  const [docId,setDocId]=useState(initial.get('doc')||'');
  const [items,setItems]=useState<LibraryItem[]>([]);
  const [selected,setSelected]=useState<string|null>(null);
  const [removing,setRemoving]=useState<LibraryItem|null>(null);
  // Search and sort keep the existing storage key; each shelf has at most two rows.
  const [query,setQuery]=useState('');const [page,setPage]=useState(1);
  const searchInput=useRef<HTMLInputElement>(null);
  const [adding,setAdding]=useState(false);const pageSize=useShelfSize();
  const [sort,setSort]=useState<SortKey>(()=>{try{const saved=localStorage.getItem('paperduet-library-sort');return saved==='added'||saved==='title'?saved:'recent';}catch{return 'recent';}});
  const matched=useMemo(()=>{const q=query.trim().toLocaleLowerCase();const list=q?items.filter(i=>i.title.toLocaleLowerCase().includes(q)):[...items];
    // 'recent' keeps the library's own order: last opened, else last added.
    if(sort==='title')list.sort((a,b)=>a.title.localeCompare(b.title,'ko',{numeric:true}));else if(sort==='added')list.sort((a,b)=>(b.created_at??'').localeCompare(a.created_at??''));
    return list;},[items,query,sort]);
  const pages=Math.max(1,Math.ceil(matched.length/pageSize));const current=Math.min(page,pages);
  useEffect(()=>{setPage(1);},[pageSize]);
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
  const navigate=(view:string,id=docId)=>{
    setScreen(view);setDocId(id);setError('');
    const guide=lastTutorialScene();if(view==='tutorial'){setOnboarding(false);setTutorialScene(guide);try{localStorage.setItem('paperduet-guide-seen','1');}catch{/* Storage may be unavailable. */}}
    history.pushState(null,'',view==='tutorial'?`?tutorial=${guide}`:view==='library'?'?library=1':`?doc=${encodeURIComponent(id)}`);
    window.scrollTo(0,0);
  };
  const recent=items.find(item=>item.opened_at&&item.block_count>0);
  const [loading,setLoading]=useState(true);
  // Right after launch the sidecar may still be booting, so the first load retries quietly.
  const refresh=async(retries=0,alive=()=>true)=>{for(let attempt=0;alive();attempt++){try{const list=await request<LibraryItem[]>('/documents');if(alive()){setItems(list);setError(e=>e===LOAD_ERROR?'':e);setLoading(false);}return;}catch{if(attempt>=retries){if(alive()){setError(LOAD_ERROR);setLoading(false);}return;}await new Promise(resolve=>setTimeout(resolve,500));}}};
  useEffect(()=>{const pop=()=>{const p=new URLSearchParams(location.search);setScreen(p.has('tutorial')?'tutorial':p.has('doc')?'reader':'library');setTutorialScene(p.get('tutorial')||lastTutorialScene());setDocId(p.get('doc')||'');};window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
  useEffect(()=>{if(screen==='reader')return;let alive=true;void refresh(120,()=>alive);return()=>{alive=false;};},[screen]);
  const upload=async(file?:File)=>{
    if(!file||uploadLock.current)return;
    if(!file.name.toLowerCase().endsWith('.pdf')){setError('PDF 파일을 선택해 주세요.');return;}
    if(file.size>150*1024*1024){setError('PDF는 150MB 이하만 지원합니다.');return;}
    uploadLock.current=true;setUploading(true);setError('');
    try{const result=await request<{doc_id:string}>('/documents',{method:'POST',body:file,headers:{'Content-Type':'application/pdf'}});setAdding(false);setSelected(result.doc_id);setPage(1);jobs.wake();await refresh();}
    catch(e){setError((e as Error).message);}finally{uploadLock.current=false;setUploading(false);if(input.current)input.current.value='';}
  };
  return <>{screen==='tutorial'?<Tutorial sceneId={tutorialScene} onScene={id=>{setTutorialScene(id);history.pushState(null,'',`?tutorial=${id}`);}} onGo={target=>{if(target==='connection')setSettings(true);else if(target==='settings')setAppSettings(true);else navigate('library');}}/>:screen==='reader'?<Reader key={docId} docId={docId} onLibrary={()=>navigate('library')} onTutorial={()=>navigate('tutorial')} onProcess={()=>{setSelected(docId);navigate('library');}}/>:
    <div className={`library-page ${drag?'dragging':''}`} onDragOver={e=>{if(!e.dataTransfer.types.includes('Files'))return;e.preventDefault();setDrag(true);}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))setDrag(false);}} onDrop={e=>{e.preventDefault();setDrag(false);void upload(e.dataTransfer.files[0]);}}>
      <a className="library-logo" href="?library=1" aria-label="PaperDuet · 서재로 이동" onClick={e=>{e.preventDefault();window.scrollTo(0,0);}}><BrandMark/></a>
      <nav className="library-dock" aria-label="서재 도구">
        <button className="icon-button dock-tool" aria-label="사용 가이드" title="사용 가이드" onClick={()=>navigate('tutorial')}><Icon name="help"/></button>
        <button className="icon-button dock-tool" aria-label="AI 연결 설정" title="AI 연결 설정" onClick={()=>setSettings(true)}>AI</button>
        <button className="icon-button dock-tool" aria-label="앱 설정" title={updateAvailable?'새 업데이트 · 앱 설정':'앱 설정'} onClick={()=>setAppSettings(true)}><Icon name="gear"/>{updateAvailable&&<span className="dock-update-dot"/>}</button>
        <span className="dock-separator" aria-hidden="true"/>
        <button className="shelf-add-button" disabled={uploading} onClick={()=>setAdding(true)}>{uploading?<span className="spinner" aria-hidden="true"/>:<Icon name="plus"/>}논문 추가</button>
      </nav>
      <input ref={input} type="file" accept=".pdf,application/pdf" aria-label="PDF 파일" hidden onChange={e=>void upload(e.target.files?.[0])}/>
      <main className="library-main"><h1>나의 논문 서재</h1>
      {updateAvailable&&!updateDismissed&&<div className="update-banner" role="status"><Icon name="arrowUp"/><span><b>새 버전 {update!.version}</b>이 나왔습니다. 지금 쓰는 버전은 {update!.current_version}입니다.</span><button className="update-banner-action" onClick={()=>setAppSettings(true)}>업데이트</button><button className="icon-button update-banner-close" aria-label="업데이트 알림 닫기" title="닫기" onClick={()=>setUpdateDismissed(true)}><Icon name="close"/></button></div>}
      {error&&<p className="error-message" role="alert">{error}<button onClick={()=>void refresh(20)}>다시 불러오기</button></p>}
      {loading&&!items.length?<p className="library-loading" role="status">서재를 불러오는 중…</p>:!items.length?<section className="library-empty"><Icon name="book"/><h2>첫 논문을 서재에 놓아 보세요</h2><p>PDF나 arXiv 논문으로 읽기를 시작하세요.</p><button className="shelf-add-button" disabled={uploading} onClick={()=>setAdding(true)}><Icon name="plus"/>논문 추가</button></section>:<>
      {recent&&<section className="continue-card" aria-label="이어 읽기"><PaperCover item={recent} ribbon/><div className="continue-body"><h2>{recent.title}</h2><p className="continue-stats"><span><b>{recent.last_page??1}쪽</b>에서 멈춤</span><span><b>{recent.read_percent??0}%</b> 읽음</span><span>전체 {recent.page_count}쪽</span></p><div className="continue-track" role="progressbar" aria-label="읽은 정도" aria-valuenow={recent.read_percent??0} aria-valuemin={0} aria-valuemax={100}><i style={{width:`${recent.read_percent??0}%`}}/></div></div><button className="continue-go" onClick={()=>navigate('reader',recent.id)}>이어서 읽기<Icon name="arrowRight"/></button></section>}
      <div className="library-section-title"><span className="library-count"><b>{items.length}</b>편</span><div className="library-tools">
        <input ref={searchInput} className="library-search" type="search" aria-label="논문 제목 검색" placeholder="제목 검색" value={query} onChange={e=>{setQuery(e.target.value);setPage(1);}} onKeyDown={e=>{if(e.key==='Escape'){setQuery('');setPage(1);}}}/>
        <button className="icon-button library-find" aria-label="논문 검색" onClick={()=>searchInput.current?.focus()}><Icon name="search"/></button>
        <label className="library-sort"><Icon name="sort"/><select aria-label="정렬" value={sort} onChange={e=>{const next=e.target.value as SortKey;setSort(next);setPage(1);try{localStorage.setItem('paperduet-library-sort',next);}catch{/* The order simply resets next time. */}}}><option value="recent">최근 읽은 순</option><option value="added">최근 추가 순</option><option value="title">제목 순</option></select></label>
      </div></div>
      {query.trim()&&<p className="library-result" role="status">{matched.length?`‘${query.trim()}’ 검색 결과 ${matched.length}편`:`‘${query.trim()}’와 일치하는 논문이 없습니다.`}</p>}
      <div className="shelf-viewport"><div className="shelf-track" style={{transform:`translateX(calc(-${(current-1)*100}% - ${(current-1)*48}px))`}}>
        {Array.from({length:pages},(_,index)=><section key={index} className="shelf-page" aria-label={`${index+1}페이지 논문`} aria-hidden={index+1!==current} inert={index+1!==current}>
          {Math.abs(index+1-current)<=1&&matched.slice(index*pageSize,(index+1)*pageSize).map(item=><ShelfBook key={item.id} item={item} job={jobs.active[item.id]} active={index+1===current} onRead={()=>{if(item.block_count)navigate('reader',item.id);else setSelected(item.id);}} onProcess={()=>setSelected(item.id)} onDelete={()=>setRemoving(item)}/>)}
        </section>)}
      </div></div>
      {pages>1&&<Pagination page={current} pages={pages} onPage={setPage}/>}
      </>}
      </main></div>}
    {adding&&<Modal title="논문 추가" onClose={()=>setAdding(false)}><div className="add-paper-content" onDragOver={e=>{e.preventDefault();e.currentTarget.classList.add('drop-active');}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))e.currentTarget.classList.remove('drop-active');}} onDrop={e=>{e.preventDefault();e.currentTarget.classList.remove('drop-active');void upload(e.dataTransfer.files[0]);}}>
      <button className="add-paper-drop" aria-label="PDF 선택" disabled={uploading} onClick={()=>input.current?.click()}>{uploading?<span className="spinner" aria-hidden="true"/>:<Icon name="plus"/>}<span>{uploading?'PDF를 가져오고 있습니다…':'PDF를 끌어다 놓거나 눌러서 선택하세요'}</span><small>최대 150MB · 원본은 이 PC에만 저장됩니다</small></button>
      <div className="add-paper-divider">또는 arXiv</div><ArxivInput compact onImported={id=>{setAdding(false);setSelected(id);setPage(1);jobs.wake();void refresh();}}/>
      {error&&<p className="error-message" role="alert">{error}</p>}
    </div></Modal>}
    {selected&&(items.find(item=>item.id===selected)?.status==='fixture'?<Modal title="논문 처리" onClose={()=>setSelected(null)}><div className="dialog-content"><p>대역과 주석이 준비된 샘플 논문입니다.</p><div className="dialog-actions"><button onClick={()=>{const id=selected;setSelected(null);navigate('reader',id);}}>원문·대역 읽기</button></div></div></Modal>:<ProgressPanel docId={selected} onClose={()=>{setSelected(null);jobs.wake();void refresh();}} onRead={()=>{const id=selected;setSelected(null);navigate('reader',id);}} onSettings={()=>setSettings(true)}/>)}
    <JobBanner active={Object.values(jobs.active)} settled={jobs.settled} hidden={selected} onOpen={id=>setSelected(id)} onRead={id=>navigate('reader',id)} onDismiss={jobs.dismiss}/>
    {onboarding&&<ProviderPanel onboarding onGuide={()=>navigate('tutorial')} onClose={()=>void finishOnboarding()} onComplete={()=>{void finishOnboarding();navigate('library');}}/>}
    {removing&&<DeleteDialog item={removing} running={!!jobs.active[removing.id]} onClose={()=>setRemoving(null)} onDeleted={()=>{const id=removing.id;setRemoving(null);if(selected===id)setSelected(null);jobs.dismiss(id);void refresh();}}/>}
    {appSettings&&<AppSettings initialUpdate={update??undefined} onClose={()=>setAppSettings(false)}/>}
    {settings&&<ProviderPanel onClose={()=>setSettings(false)}/>}
  </>;
}

type Tracked={id:string;title:string;status:string;stage:string;progress:number};
const ACTIVE=new Set(['queued','running','batch_waiting']);
const DONE_TEXT:Record<string,string>={complete:'번역·주석 완료',review:'번역 완료',paused:'일시정지됨',failed:'처리 중단',awaiting_ai:'미번역 · AI 연결 후 번역할 수 있습니다',ready_to_translate:'미번역 항목을 번역할 수 있습니다'};
const OK_STATES=new Set(['complete','review','ready_to_translate','awaiting_ai']);
// Polls the local library so running jobs stay visible after their dialog closes.
function useJobTracker(onSettled:()=>void){
  const [active,setActive]=useState<Record<string,Tracked>>({});const [settled,setSettled]=useState<Tracked[]>([]);
  const previous=useRef<Record<string,Tracked>>({});const settledRef=useRef(onSettled);settledRef.current=onSettled;const wake=useRef<()=>void>(()=>{});
  useEffect(()=>{let stopped=false;let timer:ReturnType<typeof setTimeout>;
    const tick=async()=>{try{
      const items=await request<(Pick<LibraryItem,'id'|'title'|'status'>&{job_status?:string;stage?:string;progress?:number})[]>('/documents?stats=false');const next:Record<string,Tracked>={};
      await Promise.all(items.filter(i=>ACTIVE.has(i.status)).map(async i=>{
        // Older sidecars ignore the query parameter and still need the job lookup.
        const job='job_status' in i?{status:i.job_status,stage:i.stage,progress:i.progress}:await request<Job>(`/documents/${i.id}/job`).catch(()=>null);
        next[i.id]={id:i.id,title:i.title,status:job?.status??i.status,stage:job?.stage??'',progress:job?.progress??0};
      }));
      if(stopped)return;
      const ended=Object.values(previous.current).filter(t=>!next[t.id]&&items.some(i=>i.id===t.id)).map(t=>({...t,status:items.find(i=>i.id===t.id)?.status??'complete',progress:1}));
      if(ended.length){setSettled(list=>[...list.filter(s=>!ended.some(e=>e.id===s.id)),...ended]);settledRef.current();}
      if(JSON.stringify(previous.current)!==JSON.stringify(next))setActive(next);previous.current=next;
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
    <p>이 논문을 서재에서 삭제할까요? 원문 PDF와 번역·주석, AI 대화, 메모가 모두 지워지며 되돌릴 수 없습니다.</p>
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
      setError('');for await(const job of readEvents<Job>(response))setJob(job);
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
