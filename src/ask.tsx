import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { request } from './api';
import { ConnectionChoices, defaultModel, Modal, ModelSelect, ProviderPanel } from './connections';
import { Icon } from './icons';
import { primaryKey, shortcut } from './platform';
import { FloatingWindow } from './floating-window';
import { AskContext } from './ask-context';
import { AskTranscript } from './ask-transcript';
import { askTarget, targetAnchor, type AskTarget } from './ask-target';
import { fallbackTitle, selectionTitle, useAskConversations, type Anchor, type AskSettings, type Message, type Preset } from './ask-conversations';
import type { Document, NoteKind } from './types';

export type { Anchor } from './ask-conversations';
const plain=(value:string)=>value.replace(/<[^>]*>/g,'');

function selectedAnchor():Anchor|null {
  const selection=window.getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount)return null;
  const range=selection.getRangeAt(0);const start=range.startContainer.nodeType===Node.ELEMENT_NODE?range.startContainer as Element:range.startContainer.parentElement;
  const field=start?.closest<HTMLElement>('[data-ask-field]');const block=field?.closest('[data-block]');
  if(!field||!block||!field.contains(range.endContainer))return null;
  const end=range.endContainer.nodeType===Node.ELEMENT_NODE?range.endContainer as Element:range.endContainer.parentElement;
  if(start?.closest('[data-source-text]')||end?.closest('[data-source-text]'))return null;
  const sourceText=(part:Range)=>{const fragment=part.cloneContents();fragment.querySelectorAll('[data-source-text]').forEach(el=>el.replaceWith(document.createTextNode(el.getAttribute('data-source-text')||'')));return fragment.textContent||'';};
  const before=range.cloneRange();before.selectNodeContents(field);before.setEnd(range.startContainer,range.startOffset);
  const offset=sourceText(before).length;const text=sourceText(range);if(!text.trim())return null;return {block_id:block.id,field:field.dataset.askField!,start:offset,end:offset+text.length,text};
}

export function useAsk(doc:Document|null,enabled=true) {
  const [visible,setVisible]=useState(false);const [opened,setOpened]=useState(false);
  const [anchor,setAnchor]=useState<Anchor|null>(null);const [selected,setSelected]=useState<Anchor|null>(null);const [activation,setActivation]=useState(0);
  const open=useCallback((next?:AskTarget)=>{
    setAnchor(next?targetAnchor(next):null);setOpened(true);setVisible(true);setActivation(value=>value+1);setSelected(null);
    if(next)window.getSelection()?.removeAllRanges();
  },[]);
  const close=useCallback(()=>{setVisible(false);setSelected(null);},[]);
  useEffect(()=>{setVisible(false);setOpened(false);setAnchor(null);setSelected(null);},[doc?.id]);
  useEffect(()=>{
    if(!enabled||!doc)return;
    let dragging=false;let fromText=false;let frame=0;
    const selection=()=>setSelected(dragging||document.querySelector('dialog[open]')||document.activeElement?.matches('input,textarea,select')?null:selectedAnchor());
    const down=(event:PointerEvent)=>{
      if((event.target as Element).closest('.selection-actions'))return;
      dragging=true;fromText=!!(event.target as Element).closest('[data-ask-field]');setSelected(null);
    };
    const up=()=>{
      if(!dragging)return;dragging=false;
      if(fromText){cancelAnimationFrame(frame);frame=requestAnimationFrame(selection);}
    };
    const cancel=()=>{dragging=false;fromText=false;setSelected(null);};
    const key=(e:KeyboardEvent)=>{if(primaryKey(e)&&e.key.toLowerCase()==='k'&&!document.querySelector('dialog[open]')){e.preventDefault();const anchor=selectedAnchor();open(anchor?{kind:'text',anchor}:undefined);}};
    document.addEventListener('selectionchange',selection);document.addEventListener('pointerdown',down,true);document.addEventListener('pointerup',up,true);document.addEventListener('pointercancel',cancel,true);window.addEventListener('blur',cancel);window.addEventListener('keydown',key);
    return()=>{cancelAnimationFrame(frame);document.removeEventListener('selectionchange',selection);document.removeEventListener('pointerdown',down,true);document.removeEventListener('pointerup',up,true);document.removeEventListener('pointercancel',cancel,true);window.removeEventListener('blur',cancel);window.removeEventListener('keydown',key);};
  },[enabled,!!doc,open]);
  return {anchor,selected,visible,opened,activation,open,close};
}

export function SelectionActions({anchor,onOpen}:{anchor:Anchor;onOpen:(anchor:Anchor)=>void}) {
  const menu=useRef<HTMLDivElement>(null);
  const [position,setPosition]=useState<{left:number;top:number;side:'above'|'below'}|null>(null);
  useLayoutEffect(()=>{
    let frame=0;
    const update=()=>{
      const selection=window.getSelection();const current=selectedAnchor();const popup=menu.current;
      if(!popup||!selection?.rangeCount||!current||current.block_id!==anchor.block_id||current.field!==anchor.field||current.start!==anchor.start||current.end!==anchor.end){setPosition(null);return;}
      const viewport=window.visualViewport;
      const left=(viewport?.offsetLeft??0)+8;const right=Math.min(document.documentElement.clientWidth,(viewport?.offsetLeft??0)+(viewport?.width??innerWidth))-8;
      const top=Math.max((viewport?.offsetTop??0)+8,(document.querySelector('.book-topbar')?.getBoundingClientRect().bottom??0)+8);
      const bottom=(viewport?.offsetTop??0)+(viewport?.height??innerHeight)-8;
      const range=selection.getRangeAt(0);
      const rects=Array.from(range.getClientRects()).filter(r=>r.width>0&&r.height>0&&r.bottom>top&&r.top<bottom&&r.right>left&&r.left<right);
      const backwards=selection.focusNode===range.startContainer&&selection.focusOffset===range.startOffset;
      const edge=backwards?rects[0]:rects.at(-1);
      if(!edge){setPosition(null);return;}
      const width=popup.offsetWidth;const height=popup.offsetHeight;const below=edge.bottom+8;
      const side=below+height<=bottom?'below':'above';
      const x=(backwards?edge.left:edge.right)-width/2;
      setPosition({left:Math.max(left,Math.min(x,right-width)),top:Math.max(top,Math.min(side==='below'?below:edge.top-height-8,bottom-height)),side});
    };
    const schedule=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(update);};
    update();
    const observer=new ResizeObserver(schedule);if(menu.current)observer.observe(menu.current);
    const node=window.getSelection()?.anchorNode;const block=(node instanceof Element?node:node?.parentElement)?.closest('[data-block]');if(block)observer.observe(block);
    document.addEventListener('scroll',schedule,true);window.addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);window.visualViewport?.addEventListener('scroll',schedule);
    return()=>{cancelAnimationFrame(frame);observer.disconnect();document.removeEventListener('scroll',schedule,true);window.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('scroll',schedule);};
  },[anchor]);
  return <div ref={menu} className="selection-actions" role="toolbar" aria-label="선택 텍스트 액션" data-placement={position?.side} style={{left:position?.left??0,top:position?.top??0,visibility:position?'visible':'hidden'}} onPointerDown={e=>e.preventDefault()} onMouseDown={e=>e.preventDefault()}><button onClick={()=>onOpen(anchor)}>Ask AI <kbd>{shortcut('K')}</kbd></button></div>;
}

export function AskPanel({doc,selection,visible,activation,onClose,onSaved}:{doc:Document;selection:Anchor|null;visible:boolean;activation:number;onClose:()=>void;onSaved:()=>Promise<void>}) {
  const chat=useAskConversations(doc);
  const {settings,setSettings,ready,session,threads}=chat;
  const {anchor,threadId,messages,question,preset,full,includeImage,snapshot,granularity,busy,error,notice}=session;
  const [edit,setEdit]=useState(false),[connections,setConnections]=useState(false);
  const [noteKind,setNoteKind]=useState<NoteKind>('ins');
  const panel=useRef<HTMLDivElement>(null),textarea=useRef<HTMLTextAreaElement>(null),transcript=useRef<HTMLDivElement>(null);
  const [models,setModels]=useState<{id:string;name:string}[]>([]),[cliEnabled,setCliEnabled]=useState(true);
  const [editing,setEditing]=useState<{key:string;value:string}|null>(null);const editingRef=useRef(editing);editingRef.current=editing;
  const titleInput=useRef<HTMLInputElement>(null),titleButton=useRef<HTMLButtonElement>(null);
  const currentTitle=session.title||(threads.find(t=>t.id===threadId)?fallbackTitle(threads.find(t=>t.id===threadId)!,doc):selectionTitle(anchor,doc)||'새 대화');
  const beginRename=()=>{if(!editing)setEditing({key:session.key,value:currentTitle});};
  const finishRename=(save:boolean,focus=false)=>{const value=editingRef.current;if(!value)return;editingRef.current=null;setEditing(null);if(save&&value.value.trim())void chat.rename(value.key,value.value);if(focus)requestAnimationFrame(()=>titleButton.current?.focus());};
  useEffect(()=>{if(editing)titleInput.current?.select();},[!!editing]);
  const selectedActivation=useRef<number|null>(null);
  useLayoutEffect(()=>{
    if(!selection||selectedActivation.current===activation)return;
    selectedActivation.current=activation;finishRename(true);chat.create(selection);
  },[activation]);
  useEffect(()=>{if(settings&&visible)textarea.current?.focus({preventScroll:true});},[!!settings,visible,activation,session.key]);
  useEffect(()=>{if(!settings)return;let live=true;const query=`?provider=${settings.provider}&mode=${settings.mode}`;setModels([]);void request<{configured:boolean;cli_enabled:boolean}>('/settings/providers'+query).then(async value=>{if(live)setCliEnabled(value.cli_enabled);if(value.configured){const health=await request<{models:{id:string;name:string}[]}>('/providers/health'+query);if(live)setModels(health.models);}}).catch(()=>{});return()=>{live=false;};},[settings?.provider,settings?.mode,connections]);
  const follow=useRef(true);
  useEffect(()=>{follow.current=true;},[session.key]);
  const onTranscriptScroll=(e:{currentTarget:HTMLDivElement})=>{const t=e.currentTarget;follow.current=t.scrollHeight-t.scrollTop-t.clientHeight<48;};
  useEffect(()=>{if(transcript.current&&follow.current)transcript.current.scrollTop=transcript.current.scrollHeight;},[messages]);
  const send=(regenerate=false)=>{follow.current=true;void chat.send(regenerate);};
  const copy=useCallback(async(message:Message,markdown:boolean)=>{try{const el=panel.current?.querySelector<HTMLElement>(`[data-message="${message.id}"] .answer-markdown`);await navigator.clipboard.writeText(markdown?message.content_md:el?.innerText||plain(message.content_md));chat.updateSession(session.key,{notice:'복사했습니다.'});}catch{chat.updateSession(session.key,{notice:'복사하지 못했습니다. 답변을 선택해 복사해 주세요.'});}},[session.key,chat.updateSession]);
  const saveNote=useCallback((message:Message,kind:NoteKind)=>{void request(`/threads/${threadId}/save-as-note`,{method:'POST',body:JSON.stringify({message_id:message.id,kind})}).then(async()=>{await onSaved();chat.updateSession(session.key,{notice:'블록 아래에 AI 답변 주석을 저장했습니다.'});}).catch(e=>chat.updateSession(session.key,{error:e.message}));},[threadId,onSaved,chat.updateSession,session.key]);
  const saveMemo=useCallback((message:Message)=>{
    const context=message.context_snapshot.context as {selection?:Anchor}|undefined;const source=context?.selection;
    const title=`${source?.field==='document'?'논문':doc.blocks.find(b=>b.id===source?.block_id)?.n||'본문'} 메모`;
    void request(`/documents/${doc.id}/presentation`,{method:'POST',body:JSON.stringify({message_id:message.id,title})}).then(()=>{window.dispatchEvent(new Event('paperduet-memos-changed'));chat.updateSession(session.key,{notice:'메모장에 추가했습니다.'});}).catch(e=>chat.updateSession(session.key,{error:e.message}));
  },[doc,chat.updateSession,session.key]);
  if(!settings)return <FloatingWindow id="ask" title="Ask AI" open={visible} activation={activation} onClose={onClose}><p>{error||'질문창을 여는 중…'}</p></FloatingWindow>;
  const block=doc.blocks.find(b=>b.id===anchor?.block_id);
  const drafts=Object.values(chat.sessions).filter(s=>!s.threadId);
  const background=Object.values(chat.sessions).filter(s=>s.key!==session.key&&s.busy).length;
  return <><FloatingWindow id="ask" title="Ask AI" subtitle={doc.title} open={visible} activation={activation} onClose={onClose}><div ref={panel} className="ask-panel" onKeyDown={e=>{if(e.key==='F2'&&!document.querySelector('dialog[open]')){e.preventDefault();e.stopPropagation();beginRename();}}}>
    <details className="ask-configuration"><summary>AI 연결·질문 설정</summary><ConnectionChoices cliEnabled={cliEnabled} provider={settings.provider} mode={settings.mode} onChange={(provider,mode)=>{if(!busy)setSettings({...settings,provider,mode,model:defaultModel(provider,mode)});}}/><label>질문 모델<ModelSelect key={`${settings.provider}-${settings.mode}`} listId="ask-models" label="질문 모델" provider={settings.provider} mode={settings.mode} value={settings.model} disabled={busy} onChange={model=>setSettings({...settings,model})}/></label><datalist id="ask-models">{models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</datalist><div className="ask-config-actions"><button onClick={()=>setConnections(true)}>연결 설정</button><button onClick={()=>setEdit(true)}>질문 규칙·프리셋</button></div></details>
    <div className="thread-picker"><div className="thread-picker-main"><label htmlFor="ask-thread-picker">이 논문의 대화</label><div className="thread-current">
      <select id="ask-thread-picker" className="thread-select" disabled={!ready} value={threadId||session.key} onChange={e=>{finishRename(false);chat.change(e.target.value);}}>
        {drafts.map(s=><option key={s.key} value={s.key}>{s.title||selectionTitle(s.anchor,doc)||'새 대화'}{s.busy?' · 생성 중':''}</option>)}
        {threads.map(t=><option key={t.id} value={t.id}>{fallbackTitle(t,doc)} · {t.message_count}개 메시지{t.running?' · 생성 중':''}</option>)}
      </select>
      {editing?<input ref={titleInput} className="thread-title-input" aria-label="대화 제목" maxLength={120} value={editing.value} onChange={e=>setEditing({...editing,value:e.target.value})} onBlur={()=>finishRename(true)} onKeyDown={e=>{if(e.key==='Enter'||e.key==='Escape'){e.preventDefault();e.stopPropagation();finishRename(e.key==='Enter',true);}}}/>
        :<button ref={titleButton} className="thread-title" aria-label={`대화 제목: ${currentTitle}`} title="더블클릭 또는 F2로 제목 변경" onDoubleClick={beginRename} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();beginRename();}}}>{busy&&<span className="spinner" aria-hidden="true"/>}<span>{currentTitle}</span></button>}
    </div></div><button disabled={!ready} onClick={()=>{finishRename(false);chat.create();}}>새 대화</button></div>
    {background>0&&<div className="thread-background" role="status">다른 대화 {background}개에서 답변 생성 중</div>}
    <AskTranscript messages={messages} loaded={session.loaded} selected={!!anchor} granularity={granularity} elementRef={transcript} onScroll={onTranscriptScroll} noteKind={noteKind} onKind={setNoteKind} onCopy={copy} onNote={saveNote} onMemo={saveMemo}/>
    <div className="ask-composer"><div className="ask-target"><div><span>{anchor?(askTarget(anchor,doc)?.kind==='figure'?'선택한 그림':'선택한 내용'):'질문 대상 · 이 논문'}</span>{anchor&&<button className="icon-button" aria-label="선택 문장 해제" onClick={()=>{chat.update({anchor:null});textarea.current?.focus();}}><Icon name="close"/></button>}</div>{anchor&&<blockquote className="selected-quote">{anchor.text||block?.n||'선택한 문단'}</blockquote>}</div>
      <div className="preset-chips" role="group" aria-label="빠른 질문">{settings.presets.map(p=><button key={p.id} disabled={busy} aria-pressed={preset===p.id} onClick={()=>{chat.update({preset:p.id,question:p.instruction});textarea.current?.focus();}}>{p.label}</button>)}</div>
      <label className="ask-input-label">질문<textarea ref={textarea} value={question} disabled={busy||!session.loaded} placeholder={anchor?'선택한 내용에서 어떤 부분이 궁금한가요?':'이 논문에 대해 질문하세요.'} maxLength={12000} rows={3} onChange={e=>chat.update({question:e.target.value,preset:''})} onKeyDown={e=>{if(primaryKey(e)&&e.key==='Enter'){e.preventDefault();send();}}}/></label>
      {error&&<p role="alert" className="error-message">{error}</p>}{notice&&<p role="status">{notice}</p>}
      <div className="dialog-actions">{busy?<button onClick={chat.stop}>응답 중지</button>:<><button className="primary-button" disabled={!ready||!session.loaded||!question.trim()} onClick={()=>send()}>질문 보내기</button>{threadId&&messages.some(m=>m.role==='user')&&<button onClick={()=>send(true)}>답변 재생성</button>}</>}</div>
      <AskContext key={session.key} body={chat.payload()} snapshot={snapshot} threadId={threadId} messageId={messages.filter(m=>m.role==='assistant').at(-1)?.id} visible={visible&&ready&&session.loaded}>{anchor&&<label className="context-toggle"><input type="checkbox" checked={full} disabled={busy} onChange={e=>chat.update({full:e.target.checked})}/>논문 전체 포함</label>}{block?.image_path&&<label className="context-toggle"><input type="checkbox" checked={includeImage} disabled={busy} onChange={e=>chat.update({includeImage:e.target.checked})}/>원본 이미지 첨부</label>}</AskContext>
    </div>
  </div></FloatingWindow>{connections&&<ProviderPanel onClose={()=>setConnections(false)}/>} {edit&&<AskSettingsEditor settings={settings} onClose={()=>setEdit(false)} onSave={async next=>{await request('/settings/ask',{method:'PUT',body:JSON.stringify(next)});setSettings(next);setEdit(false);}}/>}</>;
}
function AskSettingsEditor({settings,onClose,onSave}:{settings:AskSettings;onClose:()=>void;onSave:(s:AskSettings)=>Promise<void>}) {
  const [draft,setDraft]=useState(settings);const [error,setError]=useState('');const [saving,setSaving]=useState(false);
  const update=(i:number,patch:Partial<Preset>)=>setDraft({...draft,presets:draft.presets.map((v,j)=>j===i?{...v,...patch}:v)});
  const save=async()=>{setSaving(true);setError('');try{await onSave(draft);}catch(e){setError((e as Error).message);setSaving(false);}};
  return <Modal title="질문 규칙·프리셋" onClose={onClose}><div className="dialog-content ask-settings">
    <section aria-labelledby="ask-rules-title"><h3 id="ask-rules-title">기본 규칙</h3><p className="ask-settings-help">모든 질문에 함께 보내는 지시입니다.</p>
      <label>시스템 지시<textarea aria-label="시스템 지시" rows={8} value={draft.system} onChange={e=>setDraft({...draft,system:e.target.value})}/></label>
      <label className="ask-settings-number">앞뒤 문맥 블록 수<input type="number" min={0} max={8} value={draft.neighbors} onChange={e=>setDraft({...draft,neighbors:Number(e.target.value)})}/><small>선택한 블록 앞뒤로 함께 보낼 블록 수입니다. 0–8개</small></label>
    </section>
    <section aria-labelledby="ask-presets-title"><h3 id="ask-presets-title">빠른 질문 프리셋 <span>{draft.presets.length}개</span></h3><p className="ask-settings-help">버튼을 누르면 질문 칸에 채웁니다. 내용을 확인하고 ‘질문 보내기’를 누르세요.</p>
      <ol className="preset-list">{draft.presets.map((p,i)=><li key={p.id} className="preset-card">
        <div className="preset-card-head"><label>액션 이름<input value={p.label} maxLength={40} onChange={e=>update(i,{label:e.target.value})}/></label>
          <button className="icon-button" aria-label={`${p.label||'이름 없는'} 프리셋 삭제`} title="프리셋 삭제" disabled={draft.presets.length===1} onClick={()=>setDraft({...draft,presets:draft.presets.filter((_,j)=>j!==i)})}><Icon name="trash"/></button></div>
        <label>액션 지시<textarea aria-label="액션 지시" rows={3} value={p.instruction} maxLength={4000} onChange={e=>update(i,{instruction:e.target.value})}/></label>
      </li>)}</ol>
      <button className="preset-add" onClick={()=>setDraft({...draft,presets:[...draft.presets,{id:crypto.randomUUID(),label:'나의 질문',instruction:'원하는 답변 방식을 적어 주세요.'}]})}><Icon name="plus"/>프리셋 추가</button>
    </section>
    {error&&<p role="alert" className="error-message">{error}</p>}
    <div className="dialog-actions ask-settings-actions"><button className="primary-button" disabled={saving} onClick={()=>void save()}>질문 설정 저장</button><button disabled={saving} onClick={onClose}>취소</button></div>
  </div></Modal>;
}
