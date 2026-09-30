import { useEffect, useRef, useState } from 'react';
import { App as Reader } from './reader';
import { Tutorial, lastTutorialScene } from './tutorial';
import { AppSettings, ArxivInput } from './m4';
import { invoke } from '@tauri-apps/api/core';
import { ERROR_TEXT, authorizedFetch, request } from './api';
import type { Job, LibraryItem, PipelineOptions } from './types';
import { DEFAULTS, Modal, ModelChoices, ProviderPanel, type ProviderSettings } from './connections';
import { Icon } from './icons';

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

const LOAD_ERROR='서재를 불러오지 못했습니다. 다시 시도해 주세요.';
const LABELS: Record<string,string>={fixture:'샘플',queued:'처리 대기',running:'처리 중',awaiting_ai:'AI 연결 필요',ready_to_translate:'번역 준비됨',paused:'일시정지',failed:'처리 실패',review:'검수 필요',complete:'완료'};
const STAGES: Record<string,string>={Ingest:'파일 확인',Extract:'원문 추출',Structure:'구조 정리',Restore:'표·수식 복원',Glossary:'용어집',Translate:'전문 번역',Annotate:'맥락 주석',Validate:'검증',Render:'리더 완성'};
export function Workspace() {
  const initial=new URLSearchParams(location.search);
  const [screen,setScreen]=useState(initial.has('tutorial')?'tutorial':initial.has('doc')?'reader':'library');
  const [tutorialScene,setTutorialScene]=useState(initial.get('tutorial')||lastTutorialScene());
  const [docId,setDocId]=useState(initial.get('doc')||'rex-omni');
  const [items,setItems]=useState<LibraryItem[]>([]);
  const [selected,setSelected]=useState<string|null>(null);
  const [settings,setSettings]=useState(false);
  const [appSettings,setAppSettings]=useState(false);const [updateAvailable,setUpdateAvailable]=useState(false);
  useEffect(()=>{if(localStorage.getItem('paperduet-auto-update')!=='false')void invoke<{available?:boolean}>('check_update').then(u=>setUpdateAvailable(!!u.available)).catch(()=>{});},[]);
  const [onboarding,setOnboarding]=useState(false);
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
  const arxiv=<ArxivInput onImported={id=>{setSelected(id);void refresh();}}/>;
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
    try{const result=await request<{doc_id:string}>('/documents',{method:'POST',body:file,headers:{'Content-Type':'application/pdf'}});setSelected(result.doc_id);await refresh();}
    catch(e){setError((e as Error).message);}finally{uploadLock.current=false;setUploading(false);if(input.current)input.current.value='';}
  };
  return <>{screen==='tutorial'?<Tutorial sceneId={tutorialScene} hasSample={hasSample} onScene={id=>{setTutorialScene(id);history.pushState(null,'',`?tutorial=${id}`);}} onGo={target=>{if(target==='connection')setSettings(true);else if(target==='settings')setAppSettings(true);else if(target==='sample')openSample();else navigate('library');}}/>:screen==='reader'?<Reader key={docId} docId={docId} onLibrary={()=>navigate('library')} onTutorial={()=>navigate('tutorial')} onProcess={()=>{setSelected(docId);navigate('library');}}/>:
    <div className={`library-page ${drag?'dragging':''}`} onDragOver={e=>{if(!e.dataTransfer.types.includes('Files'))return;e.preventDefault();setDrag(true);}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node|null))setDrag(false);}} onDrop={e=>{e.preventDefault();setDrag(false);void upload(e.dataTransfer.files[0]);}}>
      <header className="topbar library-bar"><div className="toolbar"><a className="brand" href="?library=1" onClick={e=>e.preventDefault()}><span className="brand-mark">P<span>D</span></span><b>PaperDuet</b></a>
        <div className="appbar-actions"><button onClick={()=>navigate('tutorial')}>사용 가이드</button><button onClick={()=>setSettings(true)}>AI 연결 설정</button><button onClick={()=>setAppSettings(true)}>{updateAvailable?'새 업데이트 · 앱 설정':'앱 설정'}</button>
          <button className="primary-button" disabled={uploading} onClick={()=>input.current?.click()}>{uploading?<span className="spinner" aria-hidden="true"/>:<Icon name="plus"/>}논문 추가</button></div></div></header>
      <input ref={input} type="file" accept=".pdf,application/pdf" aria-label="PDF 파일" hidden onChange={e=>void upload(e.target.files?.[0])}/>
      <main className="library-main"><h1>나의 논문 서재</h1>
      <p className="library-intro">{loading&&!items.length?'서재를 불러오는 중…':firstRun?<>한 편의 논문을, 처음부터 끝까지.<br/>AI를 연결하고 한국어 대역 리더를 만드세요.</>:`이 PC에 저장된 논문 ${items.length}편`}</p>
      {!guideSeen&&firstRun&&tutorialLink}
      {error&&<p className="error-message" role="alert">{error}<button onClick={()=>void refresh(20)}>다시 불러오기</button></p>}
      {firstRun?<><section className="upload-zone" aria-label="PDF 업로드">
        <div className="upload-icon"><Icon name="upload"/></div><h2>{uploading?<><span className="spinner" aria-hidden="true"/>PDF를 가져오고 있습니다…</>:'PDF를 여기에 놓으세요'}</h2><p>최대 150MB · 원본은 이 PC에 저장됩니다.</p><button className="primary-button" disabled={uploading} onClick={()=>input.current?.click()}>PDF 파일 선택</button><small>AI 연결 전에도 원문을 읽을 수 있습니다. 번역은 예상 사용량을 확인한 후 시작합니다.</small>
      </section>{arxiv}</>:recent&&<section className="continue-card" aria-label="이어 읽기"><div className="continue-cover" aria-hidden="true">{recent.title}</div><div className="continue-body"><span className="continue-label">이어 읽기</span><p className="continue-title">{recent.title}</p><p>{since(recent.opened_at)} 마지막으로 읽음</p></div><button className="primary-button" onClick={()=>navigate('reader',recent.id)}>이어서 읽기<Icon name="arrowRight"/></button></section>}
      <div className="library-section-title"><h2>저장한 논문 <span>{items.length}</span></h2><button onClick={()=>void refresh()}>새로고침</button></div>
      <div className="paper-list">{items.map(item=>{const job=jobs.active[item.id];const status=job?.status??item.status;return <article className="paper-item" key={item.id}><div className="paper-item-content"><h3>{item.title}</h3><p>{item.status==='fixture'?'Rex-Omni · 대역과 주석이 준비된 샘플':`${item.page_count}쪽 · ${item.block_count}블록`}{item.opened_at?` · ${since(item.opened_at)} 읽음`:''}</p></div><span className={`state-badge state-${status}`}>{job&&<span className="spinner" aria-hidden="true"/>}{LABELS[status]??status}{job?` · ${Math.round(job.progress*100)}%`:''}</span><div className="paper-actions"><button disabled={!item.block_count} onClick={()=>navigate('reader',item.id)}>논문 열기</button>{item.status!=='fixture'&&<button onClick={()=>setSelected(item.id)}>처리 상태</button>}</div></article>;})}</div>
      {!firstRun&&<section className="import-panel" aria-label="논문 가져오기"><div className="import-drop"><Icon name="upload"/><span><b>{uploading?'PDF를 가져오고 있습니다…':'PDF를 끌어다 놓거나'}</b> 파일을 선택하세요 · 최대 150MB</span><button disabled={uploading} onClick={()=>input.current?.click()}>PDF 파일 선택</button></div>{arxiv}</section>}
      {!guideSeen&&!firstRun&&tutorialLink}
      <p className="library-footnote">번역 언어는 한국어입니다. 참고문헌은 원문을 유지합니다.</p></main></div>}
    {selected&&<ProgressPanel docId={selected} onClose={()=>{setSelected(null);jobs.wake();void refresh();}} onRead={()=>{const id=selected;setSelected(null);navigate('reader',id);}} onSettings={()=>setSettings(true)}/>}
    <JobBanner active={Object.values(jobs.active)} settled={jobs.settled} hidden={selected} onOpen={id=>setSelected(id)} onRead={id=>navigate('reader',id)} onDismiss={jobs.dismiss}/>
    {onboarding&&<ProviderPanel onboarding hasSample={hasSample} onGuide={()=>navigate('tutorial')} onClose={()=>void finishOnboarding()} onComplete={()=>{void finishOnboarding();openSample();}}/>}
    {appSettings&&<AppSettings onClose={()=>setAppSettings(false)}/>}
    {settings&&<ProviderPanel onClose={()=>setSettings(false)}/>}
  </>;
}

type Tracked={id:string;title:string;status:string;stage:string;progress:number};
const ACTIVE=new Set(['queued','running']);
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
      const ended=Object.values(previous.current).filter(t=>!next[t.id]).map(t=>({...t,status:items.find(i=>i.id===t.id)?.status??'complete',progress:1}));
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
    {running.map(t=><div key={t.id} className="job-toast"><span className="spinner" aria-hidden="true"/><div className="job-toast-body"><b title={t.title}>{t.title}</b><span>{t.status==='queued'?'처리 대기':STAGES[t.stage]??t.stage} · {Math.round(t.progress*100)}%</span><progress aria-label={`${t.title} 처리 진행률`} max={1} value={t.progress}/></div><button onClick={()=>onOpen(t.id)}>자세히</button></div>)}
    {finished.map(t=><div key={t.id} className={`job-toast job-${t.status}`}><span className="job-icon" aria-hidden="true">{OK_STATES.has(t.status)?'✓':'!'}</span><div className="job-toast-body"><b title={t.title}>{t.title}</b><span>{DONE_TEXT[t.status]??LABELS[t.status]??t.status}</span></div>{t.status==='complete'||t.status==='review'?<button className="primary-button" onClick={()=>{onDismiss(t.id);onRead(t.id);}}>읽기</button>:<button onClick={()=>{onDismiss(t.id);onOpen(t.id);}}>{t.status==='ready_to_translate'||t.status==='awaiting_ai'?'번역 시작하기':'상태 보기'}</button>}<button className="job-close" aria-label="알림 닫기" onClick={()=>onDismiss(t.id)}><Icon name="close"/></button></div>)}
  </div>;
}

const USAGE_STAGES:Record<string,string>={table:'표 복원',equation:'수식 복원',glossary:'용어집',translate:'전문 번역',annotate:'맥락 주석'};
function UsageSummary({usage}:{usage:Job['usage']}){
  const total=usage.reduce((sum,u)=>sum+u.tokens_in+u.tokens_out,0);const calls=usage.reduce((sum,u)=>sum+(u.calls??0),0);const cached=usage.reduce((sum,u)=>sum+(u.cache_read??0)+(u.cache_write??0),0);
  return <details className="usage-summary"><summary>실제 AI 사용량 · 합계 {total.toLocaleString()} 토큰{calls?` · ${calls}회 호출`:''}</summary><table><thead><tr><th>단계 · 모델</th><th>호출</th><th>입력</th><th>출력</th></tr></thead><tbody>{usage.map(u=><tr key={u.stage+u.model}><td>{USAGE_STAGES[u.stage]??u.stage}<small>{u.model}</small></td><td>{u.calls??'–'}{u.requests&&u.calls&&u.requests>u.calls?<small>재시도 {u.requests-u.calls}</small>:null}</td><td>{u.tokens_in.toLocaleString()}{(u.cache_read||u.cache_write)?<small>캐시 {((u.cache_read??0)+(u.cache_write??0)).toLocaleString()}</small>:null}</td><td>{u.tokens_out.toLocaleString()}</td></tr>)}</tbody></table>{cached>0&&<small>입력에는 캐시로 재사용한 토큰(공식 CLI의 기본 지시문 등) {cached.toLocaleString()}개가 포함됩니다.</small>}</details>;
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
  const active=job?.status==='running'||job?.status==='queued';
  const action=async(path:string,body?:PipelineOptions)=>{setBusy(true);setError('');try{await request(`/documents/${docId}/${path}`,{method:'POST',body:body?JSON.stringify(body):undefined});setRevision(v=>v+1);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <Modal title="논문 처리" onClose={onClose}><div className="dialog-content"><p className="pipeline-state">{(active||!job)&&<span className="spinner" aria-hidden="true"/>}{job?LABELS[job.status]:'상태 확인 중…'}</p><progress aria-label="논문 처리 진행률" max={1} value={job?.progress??0}/><p>{job?`${STAGES[job.stage]??job.stage} · ${Math.round(job.progress*100)}%`:''}</p>
    <ol className="pipeline-stages">{Object.entries(STAGES).map(([stage,label])=><li key={stage} className={job?.stage===stage?'current':job?.checkpoint.completed_stages?.includes(stage)?'done':''}><span>{job?.checkpoint.completed_stages?.includes(stage)?'✓':'○'}</span>{label}</li>)}</ol>
    {job?.checkpoint.extracted_pages!==undefined&&<p>추출한 페이지 {job.checkpoint.extracted_pages}쪽{job.checkpoint.total_blocks?` · 번역 ${job.checkpoint.translated_count??0}/${job.checkpoint.total_blocks}블록`:''}</p>}
    {job?.checkpoint.annotation_total!==undefined&&<p>주석 완료 {job.checkpoint.annotation_count??0}/{job.checkpoint.annotation_total}개 섹션 묶음</p>}
    {job?.status==='awaiting_ai'&&<div className="pipeline-notice">텍스트 추출이 끝났습니다. 아직 완성된 대역 리더가 아닙니다. AI를 연결해 표·수식 복원, 번역과 주석을 만들어 주세요.<button onClick={onSettings}>AI 연결 설정</button></div>}
    {estimate&&!active&&<><div className="token-estimate"><b>예상 사용량</b><p>입력 약 {estimate.input_tokens.toLocaleString()} · 출력 약 {estimate.output_tokens.toLocaleString()} 토큰</p><small>문자 수와 이미지 수를 바탕으로 한 추정치입니다. 재시도·표 복원에 따라 실제 사용량이 달라집니다.</small></div><ModelChoices options={options} setOptions={setOptions}/><p className="transfer-note">번역·주석 시작 시 본문과 표·수식 이미지가 선택한 제공자·모델로 전송됩니다. API 요금 또는 공식 CLI의 사용량 한도가 적용됩니다.</p></>}
    {job?.checkpoint.error&&<p role="alert" className="error-message">{ERROR_TEXT[job.checkpoint.error]??'처리가 중단되었습니다. 다시 시도해 주세요.'}</p>}{error&&<p role="alert" className="error-message">{error}</p>}
    {!!job?.usage.length&&<UsageSummary usage={job.usage}/>}
    <div className="dialog-actions"><button disabled={!job?.checkpoint.completed_stages?.includes('Structure')} onClick={onRead}>원문·대역 읽기</button>{active?<button disabled={busy} onClick={()=>void action('pause')}>일시정지</button>:job&& (!['complete','review'].includes(job.status)||!job.checkpoint.completed_stages?.includes('Annotate'))&&<button className="primary-button" disabled={busy} onClick={()=>void action('resume',options)}>{['complete','review'].includes(job.status)?'주석 이어서 만들기':job.status==='paused'||job.status==='failed'?'이어서 실행':'번역·주석 시작'}</button>}</div><small>창을 닫아도 처리는 백그라운드에서 계속되고, 화면 위쪽에 진행률이 표시됩니다. 앱을 종료하면 저장된 단계부터 다음 실행 때 재개합니다.</small>
  </div></Modal>;
}
