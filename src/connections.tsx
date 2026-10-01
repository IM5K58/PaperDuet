import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { Icon } from './icons';
import type { ConnectionMode, PipelineOptions, Provider } from './types';

export const DEFAULTS:PipelineOptions={provider:'anthropic',mode:'api_key',glossary_model:'claude-sonnet-5',translate_model:'claude-haiku-4-5',restore_model:'claude-sonnet-5',annotate_model:'claude-sonnet-5',min_ratio:.25};
export type ProviderSettings={configured:boolean;keyring_available:boolean;options:PipelineOptions;cli_enabled:boolean;cli_path:string|null};
export const NAMES={anthropic:'Anthropic',openai:'OpenAI',google:'Google AI Studio'};
export const defaultModel=(provider:Provider,mode:ConnectionMode)=>provider==='anthropic'?(mode==='cli'?'sonnet':'claude-sonnet-5'):provider==='openai'?(mode==='cli'?'default':'gpt-5.4'):'gemini-3.8-flash';
const CLAUDE_MODELS:[string,string][]=[['claude-opus-5-5','Claude Opus 5.5'],['claude-opus-5','Claude Opus 5'],['claude-opus-4-8','Claude Opus 4.8'],['claude-opus-4-7','Claude Opus 4.7'],['claude-opus-4-6','Claude Opus 4.6'],['claude-sonnet-5','Claude Sonnet 5'],['claude-sonnet-4-6','Claude Sonnet 4.6'],['claude-haiku-4-5','Claude Haiku 4.5']];
// Model lists for the pickers. Anything else can still be typed via "직접 입력".
export const modelPresets=(provider:Provider,mode:ConnectionMode):[string,string][]=>provider==='anthropic'?(mode==='cli'?[['sonnet','sonnet · CLI 최신 Sonnet'],['opus','opus · CLI 최신 Opus'],['haiku','haiku · CLI 최신 Haiku'],...CLAUDE_MODELS]:CLAUDE_MODELS)
  :provider==='openai'&&mode==='cli'?[['default','default · Codex 기본 모델'],['gpt-6-astra','GPT-6 Astra'],['gpt-6-sol','GPT-6 Sol'],['gpt-6-luna','GPT-6 Luna'],['gpt-5.5','GPT-5.5']]:[];
const CUSTOM='__custom__';
export function ModelSelect({provider,mode,value,onChange,label,disabled,listId}:{provider:Provider;mode:ConnectionMode;value:string;onChange:(v:string)=>void;label:string;disabled?:boolean;listId?:string}) {
  const presets=modelPresets(provider,mode);const known=presets.some(([id])=>id===value);
  const [custom,setCustom]=useState(!known&&presets.length>0&&!!value);
  if(!presets.length)return <input list={listId} aria-label={label} value={value} disabled={disabled} onChange={e=>onChange(e.target.value)} maxLength={100} pattern="[a-zA-Z0-9_.:-]+"/>;
  return <span className="model-select"><select aria-label={label} disabled={disabled} value={custom||!known?CUSTOM:value} onChange={e=>{if(e.target.value===CUSTOM)setCustom(true);else{setCustom(false);onChange(e.target.value);}}}>{presets.map(([id,name])=><option key={id} value={id}>{name}</option>)}<option value={CUSTOM}>직접 입력…</option></select>{(custom||!known)&&<input aria-label={`${label} 직접 입력`} value={value} disabled={disabled} onChange={e=>onChange(e.target.value)} maxLength={100} pattern="[a-zA-Z0-9_.:-]+" placeholder="모델 ID"/>}</span>;
}

export function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:React.ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const dialog=ref.current;const opener=document.activeElement as HTMLElement|null;dialog?.showModal();return()=>{dialog?.close();opener?.focus({preventScroll:true});};},[]);
  return <dialog ref={ref} aria-label={title} className="workspace-dialog" onCancel={e=>{e.preventDefault();onClose();}}><header><h2>{title}</h2><button onClick={onClose} aria-label="창 닫기"><Icon name="close"/></button></header>{children}</dialog>;
}

export function ConnectionChoices({provider,mode,onChange,cliEnabled=true}:{provider:Provider;mode:ConnectionMode;onChange:(p:Provider,m:ConnectionMode)=>void;cliEnabled?:boolean}) {
  return <div className="connection-choices"><label>제공자<select aria-label="제공자" value={provider} onChange={e=>onChange(e.target.value as Provider,e.target.value==='google'?'api_key':mode)}>{Object.entries(NAMES).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label><label>연결 방식<select aria-label="연결 방식" value={mode} onChange={e=>onChange(provider,e.target.value as ConnectionMode)}><option value="api_key">API 키</option>{provider!=='google'&&cliEnabled&&<option value="cli">내 구독 · 공식 CLI</option>}</select></label></div>;
}

export function ModelChoices({options,setOptions}:{options:PipelineOptions;setOptions:(value:PipelineOptions)=>void}) {
  return <><ConnectionChoices provider={options.provider??'anthropic'} mode={options.mode??'api_key'} onChange={(provider,mode)=>{const model=defaultModel(provider,mode);setOptions({...options,provider,mode,glossary_model:model,restore_model:model,translate_model:model,annotate_model:model});}}/><div className="model-choices">{([['restore_model','표·수식 복원'],['glossary_model','용어집'],['translate_model','전문 번역'],['annotate_model','맥락 주석']] as const).map(([key,label])=><label key={key}>{label}<ModelSelect key={`${options.provider}-${options.mode}`} listId="provider-models" label={`${label} 모델`} provider={options.provider??'anthropic'} mode={options.mode??'api_key'} value={options[key]} onChange={value=>setOptions({...options,[key]:value})}/></label>)}<label>요약 의심 기준 (번역/원문 길이)<input type="number" min={.05} max={1} step={.05} value={options.min_ratio} onChange={e=>setOptions({...options,min_ratio:Number(e.target.value)})}/></label></div></>;
}

type Health={status:string;models:{id:string;name:string}[];version?:string;path?:string;stream_granularity?:string};
export function ProviderPanel({onClose,onComplete,onGuide,onboarding=false}:{onClose:()=>void;onComplete?:()=>void;onGuide?:()=>void;onboarding?:boolean}) {
  const [provider,setProvider]=useState<Provider>('anthropic');const [mode,setMode]=useState<ConnectionMode>('api_key');
  const [key,setKey]=useState('');const [keyError,setKeyError]=useState('');const [path,setPath]=useState('');const [state,setState]=useState<ProviderSettings|null>(null);
  const [message,setMessage]=useState('');const [busy,setBusy]=useState(false);const [health,setHealth]=useState<Health|null>(null);
  const [options,setOptions]=useState(DEFAULTS);
  const query=`?provider=${provider}&mode=${mode}`;
  const refresh=async()=>{const value=await request<ProviderSettings>('/settings/providers'+query);setState(value);setPath(value.cli_path||'');return value;};
  useEffect(()=>{let live=true;void request<ProviderSettings>('/settings/providers'+query).then(value=>{if(live){setState(value);setPath(value.cli_path||'');}}).catch(e=>{if(live)setMessage(e.message);});return()=>{live=false;};},[query]);
  useEffect(()=>{void request<ProviderSettings>('/settings/providers').then(v=>setOptions(v.options));},[]);
  const action=async(work:()=>Promise<void>)=>{setBusy(true);setMessage('');try{await work();}catch(e){setMessage((e as Error).message);}finally{setBusy(false);}};
  const test=async()=>{
    if(mode==='cli')await request('/settings/providers',{method:'PUT',body:JSON.stringify({provider,mode,cli_path:path||null})});
    const result=await request<Health>('/providers/health'+query);setHealth(result);
    if(result.status==='ok'){
      const model=defaultModel(provider,mode);const next={...options,provider,mode,glossary_model:model,translate_model:model,restore_model:model,annotate_model:model};
      setOptions(next);await request('/settings/pipeline',{method:'PUT',body:JSON.stringify(next)});
      setMessage('연결 확인 완료 · 기본 번역 연결로 설정했습니다.');
    }else setMessage('공식 CLI에서 먼저 로그인한 뒤 다시 확인해 주세요.');
  };
  return <Modal title={onboarding?'PaperDuet 시작하기':'AI 연결 설정'} onClose={onClose}><div className="dialog-content">
    {onboarding&&<><div className="eyebrow">Welcome to PaperDuet</div><h3>PDF를 한국어 대역 리더로.</h3><p>AI를 연결하면 표·수식 복원, 번역, 맥락 주석을 만들 수 있습니다. 연결을 건너뛰면 원본 PDF와 추출 텍스트를 읽습니다.</p><ol className="onboarding-steps"><li>연결 선택</li><li>{mode==='api_key'?'키 저장':'CLI 탐지'}</li><li>연결 확인</li><li>논문 가져오기</li></ol></>}
    <ConnectionChoices provider={provider} mode={mode} cliEnabled={state?.cli_enabled??true} onChange={(p,m)=>{setProvider(p);setMode(m);setKey('');setKeyError('');setHealth(null);setMessage('');}}/>
    {mode==='api_key'?<><p>키는 Windows 자격 증명 관리자에만 저장합니다. API 요금은 구독과 별도입니다.</p><p className="connection-status">{state?.configured?'● API 키 저장됨':'○ API 키 미연결'}</p><form noValidate onSubmit={e=>{e.preventDefault();const invalid=!key.trim()?'API 키를 입력해 주세요.':key.length<16?'API 키가 너무 짧습니다. 전체 키를 붙여 넣어 주세요.':'';setKeyError(invalid);if(invalid)return;void action(async()=>{const value=key;setKey('');await request('/settings/providers',{method:'PUT',body:JSON.stringify({provider,mode,api_key:value})});await refresh();setMessage('키를 저장했습니다. 연결 확인을 눌러 주세요.');});}}><label>API 키<input autoComplete="off" spellCheck={false} type="password" value={key} onChange={e=>{setKey(e.target.value);setKeyError('');}} minLength={16} maxLength={2000} required aria-invalid={!!keyError} aria-describedby={keyError?'api-key-error':undefined} disabled={busy}/></label>{keyError&&<p id="api-key-error" className="field-error" role="alert">{keyError}</p>}<div className="dialog-actions"><button className="primary-button" disabled={busy||!state?.keyring_available}>키 저장</button><button type="button" disabled={busy||!state?.configured} onClick={()=>void action(async()=>{await request('/settings/providers?provider='+provider,{method:'DELETE'});setHealth(null);await refresh();setMessage('키를 삭제했습니다.');})}>키 삭제</button></div></form></>:
      <><p>{provider==='anthropic'?'Claude Code':'Codex'}를 설치하고 터미널에서 직접 로그인해 주세요. 앱은 로그인한 공식 CLI만 호출합니다.</p><pre className="cli-instructions">{provider==='anthropic'?'claude auth login':'codex login'}</pre><label>CLI 실행 파일 경로 (자동 탐지 시 비워 두기)<input value={path} onChange={e=>{setPath(e.target.value);setHealth(null);}} placeholder="C:\…\claude.exe 또는 codex.exe"/></label><small>PATH, 사용자 설치 폴더, npm 설치 경로를 확인합니다. 구독 적용·한도는 공식 CLI 계정에 따릅니다.</small><p><a href={provider==='anthropic'?'https://code.claude.com/docs/en/setup':'https://developers.openai.com/codex/cli/'} target="_blank" rel="noreferrer">공식 설치 안내 ↗</a></p></>}
    <button disabled={busy||(mode==='api_key'&&!state?.configured)} onClick={()=>void action(test)}>연결 확인</button>
    {health&&<div className="connection-details"><b>{health.status==='ok'?'연결됨':'로그인 필요'}</b>{health.version&&<p>CLI {health.version}<br/><code>{health.path}</code></p>}{health.stream_granularity==='message'&&<small>Codex CLI는 완성된 메시지 단위로 답변을 표시합니다.</small>}</div>}
    <p role="status" className="dialog-message">{busy?'확인 중…':message}</p>
    <small>연결 확인은 모델 목록 또는 CLI 로그인 상태만 조회합니다. 논문 전송은 번역·질문 버튼을 누를 때 시작합니다.</small>
    {!onboarding&&<details><summary>단계별 기본 모델</summary><ModelChoices options={options} setOptions={setOptions}/><datalist id="provider-models">{health?.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</datalist><button disabled={busy} onClick={()=>void action(async()=>{await request('/settings/pipeline',{method:'PUT',body:JSON.stringify(options)});setMessage('모델 설정을 저장했습니다.');})}>모델 설정 저장</button></details>}
    {onboarding&&<div className="dialog-actions"><button className="primary-button" disabled={health?.status!=='ok'} onClick={onComplete}>서재에서 시작하기</button><button onClick={onClose}>지금은 원문만 읽기</button>{onGuide&&<button onClick={onGuide}>먼저 사용 가이드 보기</button>}</div>}
  </div></Modal>;
}
