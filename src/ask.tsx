import { useCallback, useEffect, useRef, useState } from 'react';
import { AnswerMarkdown } from './markdown';
import { authorizedFetch, ERROR_TEXT, request } from './api';
import { ConnectionChoices, defaultModel, Modal, ModelSelect, ProviderPanel } from './connections';
import { NOTE_NAMES } from './blocks';
import { Icon } from './icons';
import type { ConnectionMode, Document, NoteKind, Provider } from './types';

export interface Anchor {block_id:string;field:string;start:number;end:number;text:string}
type Preset={id:string;label:string;instruction:string};
type AskSettings={system:string;presets:Preset[];neighbors:number;provider:Provider;mode:ConnectionMode;model:string};
type Message={id:string;role:'user'|'assistant';content_md:string;status:string;tokens_in:number|null;tokens_out:number|null;context_snapshot:Record<string,unknown>;error_code?:string};
type Thread={id:string;block_id:string;anchor:Anchor;messages:Message[]};
type ThreadSummary={id:string;block_id:string;message_count:number};
const plain=(value:string)=>value.replace(/<[^>]*>/g,'');

export function selectedAnchor():Anchor|null {
  const selection=window.getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount)return null;
  const range=selection.getRangeAt(0);const start=range.startContainer.nodeType===Node.ELEMENT_NODE?range.startContainer as Element:range.startContainer.parentElement;
  const field=start?.closest<HTMLElement>('[data-ask-field]');const block=field?.closest('[data-block]');
  if(!field||!block||!field.contains(range.endContainer))return null;
  const end=range.endContainer.nodeType===Node.ELEMENT_NODE?range.endContainer as Element:range.endContainer.parentElement;
  if(start?.closest('[data-source-text]')||end?.closest('[data-source-text]'))return null;
  const sourceText=(part:Range)=>{const fragment=part.cloneContents();fragment.querySelectorAll('[data-source-text]').forEach(el=>el.replaceWith(document.createTextNode(el.getAttribute('data-source-text')||'')));return fragment.textContent||'';};
  const before=range.cloneRange();before.selectNodeContents(field);before.setEnd(range.startContainer,range.startOffset);
  const offset=sourceText(before).length;const text=sourceText(range);return {block_id:block.id,field:field.dataset.askField!,start:offset,end:offset+text.length,text};
}

export function useAsk(doc:Document|null,enabled=true) {
  const [anchor,setAnchor]=useState<Anchor|null>(null);const [selected,setSelected]=useState<Anchor|null>(null);
  const [threadId,setThreadId]=useState<string|null>(null);const [threads,setThreads]=useState<ThreadSummary[]>([]);const [preset,setPreset]=useState<string|undefined>();
  const refresh=useCallback(async()=>{if(doc&&enabled)setThreads(await request<ThreadSummary[]>('/threads?doc_id='+doc.id));},[doc?.id,enabled]);
  useEffect(()=>{void refresh().catch(()=>{});},[refresh]);
  const open=(next:Anchor,presetId?:string,tid?:string)=>{setAnchor(next);setPreset(presetId);setThreadId(tid||null);setSelected(null);};
  useEffect(()=>{
    if(!enabled)return;
    const selection=()=>{if(!document.querySelector('.ask-panel'))setSelected(selectedAnchor());};
    const key=(e:KeyboardEvent)=>{if(e.ctrlKey&&e.key.toLowerCase()==='k'&&!document.querySelector('dialog[open]')){const next=selectedAnchor();if(next){e.preventDefault();open(next);}}};
    document.addEventListener('selectionchange',selection);window.addEventListener('keydown',key);
    return()=>{document.removeEventListener('selectionchange',selection);window.removeEventListener('keydown',key);};
  },[enabled]);
  return {anchor,selected,threadId,threads,preset,open,refresh,close:()=>{setAnchor(null);void refresh();}};
}

export function SelectionActions({anchor,onOpen}:{anchor:Anchor;onOpen:(anchor:Anchor,preset?:string)=>void}) {
  return <div className="selection-actions" role="toolbar" aria-label="선택 텍스트 액션" onMouseDown={e=>e.preventDefault()}><button onClick={()=>onOpen(anchor)}>Ask AI <kbd>Ctrl K</kbd></button><button onClick={()=>onOpen(anchor,'explain')}>쉽게 설명</button><button onClick={()=>onOpen(anchor,'summary')}>3문장 요약</button></div>;
}

export function AskPanel({doc,anchor:initialAnchor,threadId:initialThread,preset:initialPreset,onClose,onSaved,onThreads}:{doc:Document;anchor:Anchor;threadId:string|null;preset?:string;onClose:()=>void;onSaved:()=>Promise<void>;onThreads:()=>Promise<void>}) {
  const [settings,setSettings]=useState<AskSettings|null>(null);const [anchor,setAnchor]=useState(initialAnchor);const [threadId,setThreadId]=useState(initialThread);
  const [threads,setThreads]=useState<ThreadSummary[]>([]);const [messages,setMessages]=useState<Message[]>([]);const [question,setQuestion]=useState('');const [preset,setPreset]=useState(initialPreset||'');
  const [full,setFull]=useState(false);const [includeImage,setIncludeImage]=useState(true);const [estimate,setEstimate]=useState<number|null>(null);const [snapshot,setSnapshot]=useState<unknown>();
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');const [edit,setEdit]=useState(false);const [connections,setConnections]=useState(false);
  const [noteKind,setNoteKind]=useState<NoteKind>('ins');const controller=useRef<AbortController|null>(null);const panel=useRef<HTMLElement>(null);const textarea=useRef<HTMLTextAreaElement>(null);const transcript=useRef<HTMLDivElement>(null);
  const [granularity,setGranularity]=useState('token');
  const [models,setModels]=useState<{id:string;name:string}[]>([]);const [cliEnabled,setCliEnabled]=useState(true);const autoSent=useRef(false);
  const refreshThreads=()=>request<ThreadSummary[]>('/threads?doc_id='+doc.id).then(setThreads);
  useEffect(()=>{void request<AskSettings>('/settings/ask').then(setSettings).catch(e=>setError(e.message));void refreshThreads();return()=>controller.current?.abort();},[]);
  const loadThread=async(id:string)=>{const thread=await request<Thread>('/threads/'+id);setThreadId(id);setAnchor(thread.anchor);setMessages(thread.messages);setSnapshot(thread.messages.filter(m=>m.role==='assistant').at(-1)?.context_snapshot);};
  useEffect(()=>{if(initialThread)void loadThread(initialThread).catch(e=>setError(e.message));},[initialThread]);
  useEffect(()=>{if(!settings)return;let live=true;const query=`?provider=${settings.provider}&mode=${settings.mode}`;setModels([]);void request<{configured:boolean;cli_enabled:boolean}>('/settings/providers'+query).then(async value=>{if(live)setCliEnabled(value.cli_enabled);if(value.configured){const health=await request<{models:{id:string;name:string}[]}>('/providers/health'+query);if(live)setModels(health.models);}}).catch(()=>{});return()=>{live=false;};},[settings?.provider,settings?.mode,connections]);
  useEffect(()=>{const opener=document.activeElement as HTMLElement|null;textarea.current?.focus();const key=(e:KeyboardEvent)=>{
    if(document.querySelector('dialog[open]'))return;
    if(e.key==='Escape'){e.stopPropagation();onClose();}
    if(e.key==='Tab'&&window.innerWidth<1024){const items=Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input,select,textarea,summary')||[]).filter(e=>e.getClientRects().length);if(e.shiftKey&&document.activeElement===items[0]){e.preventDefault();items.at(-1)?.focus();}else if(!e.shiftKey&&document.activeElement===items.at(-1)){e.preventDefault();items[0]?.focus();}}
  };window.addEventListener('keydown',key);return()=>{window.removeEventListener('keydown',key);opener?.focus({preventScroll:true});};},[]);
  useEffect(()=>{if(transcript.current)transcript.current.scrollTop=transcript.current.scrollHeight;},[messages]);
  const payload=(regenerate=false)=>({doc_id:doc.id,anchor,question:question.trim()||settings?.presets.find(p=>p.id===preset)?.instruction||'이 블록을 설명해 주세요.',preset:preset||null,thread_id:threadId,regenerate,provider:settings?.provider,mode:settings?.mode,model:settings?.model,full_context:full,include_image:includeImage});
  useEffect(()=>{if(!settings)return;const abort=new AbortController();const timer=setTimeout(()=>{void request<{estimated_tokens:number;snapshot:unknown}>('/ask/context',{method:'POST',body:JSON.stringify(payload()),signal:abort.signal}).then(v=>{setEstimate(v.estimated_tokens);if(!messages.length)setSnapshot(v.snapshot);}).catch(()=>{});},300);return()=>{clearTimeout(timer);abort.abort();};},[settings,anchor,full,includeImage,question,preset,threadId]);
  const send=async(regenerate=false)=>{
    if(!settings||busy)return;const abort=new AbortController();controller.current=abort;setBusy(true);setError('');setNotice('');
    const body=payload(regenerate);let activeId='';
    try{
      await request('/settings/ask',{method:'PUT',body:JSON.stringify(settings)});
      if(!regenerate)setMessages(m=>[...m,{id:'pending-user',role:'user',content_md:body.question,status:'complete',tokens_in:null,tokens_out:null,context_snapshot:{}}]);
      setQuestion('');const response=await authorizedFetch('/ask',{method:'POST',body:JSON.stringify(body),signal:abort.signal});
      if(!response.ok||!response.body){const value=await response.json().catch(()=>({}));throw new Error(ERROR_TEXT[value.detail]||'질문을 시작하지 못했습니다.');}
      const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
      for(;;){const result=await reader.read();if(result.done)break;buffer+=decoder.decode(result.value,{stream:true});let end;
        while((end=buffer.indexOf('\n\n'))>=0){const raw=buffer.slice(0,end);buffer=buffer.slice(end+2);const line=raw.split('\n').find(l=>l.startsWith('data: '));if(!line)continue;
          const event=JSON.parse(line.slice(6));
          if(event.event==='start'){activeId=event.message_id;setThreadId(event.thread_id);setSnapshot(event.context);setGranularity(event.stream_granularity);setMessages(m=>[...m,{id:activeId,role:'assistant',content_md:'',status:'running',tokens_in:null,tokens_out:null,context_snapshot:event.context}]);}
          if(event.event==='delta')setMessages(m=>m.map(x=>x.id===activeId?{...x,content_md:x.content_md+event.text}:x));
          if(event.event==='error')setError(ERROR_TEXT[event.code]||'AI 응답이 중단되었습니다. 연결을 확인해 주세요.');
          if(event.event==='done'){await loadThread(event.thread_id);await refreshThreads();await onThreads();}
        }
      }
    }catch(e){if(abort.signal.aborted){setNotice('응답을 중지했습니다.');setMessages(m=>m.map(x=>x.id===activeId?{...x,status:'cancelled'}:x));}else setError((e as Error).message);}
    finally{setBusy(false);controller.current=null;}
  };
  const copy=async(message:Message,markdown:boolean)=>{try{const el=panel.current?.querySelector<HTMLElement>(`[data-message="${message.id}"] .answer-markdown`);await navigator.clipboard.writeText(markdown?message.content_md:el?.innerText||plain(message.content_md));setNotice('복사했습니다.');}catch{setNotice('복사하지 못했습니다. 답변을 선택해 복사해 주세요.');}};
  useEffect(()=>{if(settings&&initialPreset&&!initialThread&&!autoSent.current){autoSent.current=true;void send();}},[settings]);
  if(!settings)return <aside className="ask-panel"><button onClick={onClose}>닫기</button><p>{error||'질문 패널을 여는 중…'}</p></aside>;
  const block=doc.blocks.find(b=>b.id===anchor.block_id);
  return <><button className="ask-scrim" aria-label="AI 질문 닫기" onClick={onClose}/><aside ref={panel} className="ask-panel" aria-label="Ask AI" role="dialog" aria-modal={window.innerWidth<1024}>
    <header><div><span className="eyebrow">Read · ask · understand</span><h2>Ask AI <small>{block?.n||'본문'} · {anchor.block_id}</small></h2></div><button className="icon-button" aria-label="AI 질문 닫기" onClick={onClose}><Icon name="close"/></button></header>
    <div className="ask-configuration"><ConnectionChoices cliEnabled={cliEnabled} provider={settings.provider} mode={settings.mode} onChange={(provider,mode)=>{if(!busy)setSettings({...settings,provider,mode,model:defaultModel(provider,mode)});}}/><label>질문 모델<ModelSelect key={`${settings.provider}-${settings.mode}`} listId="ask-models" label="질문 모델" provider={settings.provider} mode={settings.mode} value={settings.model} disabled={busy} onChange={model=>setSettings({...settings,model})}/></label><datalist id="ask-models">{models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</datalist><div className="ask-config-actions"><button onClick={()=>setConnections(true)}>연결 설정</button><button onClick={()=>setEdit(true)}>질문 규칙·프리셋</button></div></div>
    {anchor.text&&<blockquote className="selected-quote">{anchor.text}</blockquote>}
    <div className="thread-picker"><label>이 블록의 대화<select disabled={busy} value={threadId||''} onChange={e=>{if(e.target.value)void loadThread(e.target.value);else{setThreadId(null);setMessages([]);setAnchor(initialAnchor);}}}><option value="">새 대화</option>{threads.filter(t=>t.block_id===anchor.block_id).map((t,i)=><option value={t.id} key={t.id}>대화 {i+1} · {t.message_count}개 메시지</option>)}</select></label></div>
    <div className="ask-transcript" ref={transcript} role="log" aria-label="질문과 답변">{!messages.length&&<p className="ask-empty">선택한 내용과 주변 문맥을 함께 읽고 답합니다.<br/>아래 액션을 고르거나 직접 질문하세요.</p>}{messages.map(m=><article key={m.id} data-message={m.id} className={`ask-message ${m.role}`}><b>{m.role==='user'?'나':'AI'}</b>{m.role==='user'?<p>{m.content_md}</p>:<><AnswerMarkdown text={m.content_md|| (granularity==='message'?'CLI가 답변을 작성 중입니다…':'답변을 기다리는 중입니다…')}/><small>{m.status==='complete'?`입력 ${m.tokens_in??'—'} · 출력 ${m.tokens_out??'—'} 토큰`:m.status==='running'?'응답 중…':'중단된 답변'}</small>{m.status==='complete'&&<div className="answer-actions"><button onClick={()=>void copy(m,true)}>Markdown 복사</button><button onClick={()=>void copy(m,false)}>평문 복사</button><select aria-label="저장할 주석 유형" value={noteKind} onChange={e=>setNoteKind(e.target.value as NoteKind)}>{Object.entries(NOTE_NAMES).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select><button onClick={()=>void request(`/threads/${threadId}/save-as-note`,{method:'POST',body:JSON.stringify({message_id:m.id,kind:noteKind})}).then(async()=>{await onSaved();setNotice('블록 아래에 AI 답변 주석을 저장했습니다.');}).catch(e=>setError(e.message))}>주석으로 저장</button><button onClick={()=>void request(`/documents/${doc.id}/presentation`,{method:'POST',body:JSON.stringify({message_id:m.id,title:`${block?.n||'본문'} 발표 메모`})}).then(()=>setNotice('발표 노트에 추가했습니다. 리더 상단에서 열 수 있습니다.')).catch(e=>setError(e.message))}>발표 노트에 추가</button></div>}</>}</article>)}</div>
    <div className="ask-composer"><div className="preset-chips" role="group" aria-label="빠른 질문">{settings.presets.map(p=><button key={p.id} disabled={busy} aria-pressed={preset===p.id} onClick={()=>setPreset(preset===p.id?'':p.id)}>{p.label}</button>)}</div>
      <label className="context-toggle"><input type="checkbox" checked={full} disabled={busy} onChange={e=>setFull(e.target.checked)}/>논문 전체 포함 <span>약 {estimate?.toLocaleString()??'…'} 토큰</span></label>{block?.image_path&&<label className="context-toggle"><input type="checkbox" checked={includeImage} disabled={busy} onChange={e=>setIncludeImage(e.target.checked)}/>원본 이미지 첨부</label>}
      <label className="ask-input-label">질문<textarea ref={textarea} value={question} disabled={busy} placeholder="어떤 부분이 궁금한가요?" maxLength={12000} rows={3} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();void send();}}}/></label>
      {error&&<p role="alert" className="error-message">{error}</p>}{notice&&<p role="status">{notice}</p>}
      <div className="dialog-actions">{busy?<button onClick={()=>controller.current?.abort()}>응답 중지</button>:<><button className="primary-button" onClick={()=>void send()}>질문 보내기</button>{threadId&&messages.some(m=>m.role==='user')&&<button onClick={()=>void send(true)}>답변 재생성</button>}</>}</div>
      <details className="context-debug"><summary>전송 컨텍스트 확인</summary><small>가장 최근 요청의 실제 시스템 지시·대화·문맥입니다. 이미지 본문은 제외하고 첨부 여부를 표시합니다.</small><pre>{JSON.stringify(snapshot,null,2)}</pre></details>
    </div>
  </aside>{connections&&<ProviderPanel onClose={()=>setConnections(false)}/>} {edit&&<AskSettingsEditor settings={settings} onClose={()=>setEdit(false)} onSave={async next=>{await request('/settings/ask',{method:'PUT',body:JSON.stringify(next)});setSettings(next);setEdit(false);}}/>}</>;
}

function AskSettingsEditor({settings,onClose,onSave}:{settings:AskSettings;onClose:()=>void;onSave:(s:AskSettings)=>Promise<void>}) {
  const [draft,setDraft]=useState(settings);const [error,setError]=useState('');
  return <Modal title="질문 규칙·프리셋" onClose={onClose}><div className="dialog-content"><label>시스템 지시<textarea rows={8} value={draft.system} onChange={e=>setDraft({...draft,system:e.target.value})}/></label><label>앞뒤 문맥 블록 수<input type="number" min={0} max={8} value={draft.neighbors} onChange={e=>setDraft({...draft,neighbors:Number(e.target.value)})}/></label>{draft.presets.map((p,i)=><fieldset key={p.id}><label>액션 이름<input value={p.label} maxLength={40} onChange={e=>setDraft({...draft,presets:draft.presets.map((v,j)=>j===i?{...v,label:e.target.value}:v)})}/></label><label>액션 지시<textarea aria-label="액션 지시" value={p.instruction} maxLength={4000} onChange={e=>setDraft({...draft,presets:draft.presets.map((v,j)=>j===i?{...v,instruction:e.target.value}:v)})}/></label><button disabled={draft.presets.length===1} onClick={()=>setDraft({...draft,presets:draft.presets.filter((_,j)=>j!==i)})}>프리셋 삭제</button></fieldset>)}<button onClick={()=>setDraft({...draft,presets:[...draft.presets,{id:crypto.randomUUID(),label:'나의 질문',instruction:'원하는 답변 방식을 적어 주세요.'}]})}>프리셋 추가</button><button className="primary-button" onClick={()=>void onSave(draft).catch(e=>setError(e.message))}>질문 설정 저장</button>{error&&<p role="alert">{error}</p>}</div></Modal>;
}
