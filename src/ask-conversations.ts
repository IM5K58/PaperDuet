import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { authorizedFetch, ERROR_TEXT, request } from './api';
import type { ConnectionMode, Document, Provider } from './types';
import { askTarget, targetAnchor, targetTitle, type Anchor } from './ask-target';
import { readEvents } from './sse';
export type { Anchor } from './ask-target';

export type Preset={id:string;label:string;instruction:string};
export type AskSettings={system:string;presets:Preset[];neighbors:number;provider:Provider;mode:ConnectionMode;model:string};
export type Message={id:string;role:'user'|'assistant';content_md:string;status:string;tokens_in:number|null;tokens_out:number|null;context_snapshot:Record<string,unknown>};
export type Thread={id:string;title:string;block_id:string;anchor:Anchor;messages:Message[]};
export type ThreadSummary={id:string;title:string;block_id:string;anchor:string;message_count:number;running?:boolean};
type AskEvent={event:'start';thread_id:string;message_id:string;context:Record<string,unknown>;stream_granularity:string}|{event:'delta';text:string}|{event:'error';code:string}|{event:'done';status:string;usage?:{tokens_in?:number;tokens_out?:number}};
type Draft={threadId:string|null;title:string;anchor:Anchor|null;question:string;preset:string;full:boolean;includeImage:boolean};
type Session=Draft&{key:string;messages:Message[];snapshot?:unknown;granularity:string;busy:boolean;loaded:boolean;error:string;notice:string;anchorVersion:number;restoreAnchor:boolean};
const fresh=(key='draft:'+crypto.randomUUID(),draft:Partial<Draft>={}):Session=>({key,threadId:null,title:'',anchor:null,question:'',preset:'',full:false,includeImage:true,
  messages:[],granularity:'token',busy:false,loaded:true,error:'',notice:'',anchorVersion:0,restoreAnchor:false,...draft});
const validDraft=(value:Partial<Draft>|null):value is Draft=>!!value&&typeof value.question==='string'&&typeof value.preset==='string'&&
  (value.threadId===null||typeof value.threadId==='string')&&(value.anchor===null||typeof value.anchor?.block_id==='string');
const unused=(s:Session)=>!s.threadId&&!s.busy&&!s.question.trim()&&!s.messages.length&&!s.title.trim();
const keepDrafts=(sessions:Record<string,Session>,active:string)=>Object.fromEntries(Object.entries(sessions).filter(([key,s])=>key===active||!unused(s)));
export const selectionTitle=(anchor:Anchor|null,doc?:Document)=>targetTitle(askTarget(anchor,doc),doc);
function restore(docId:string) {
  try {
    const saved=JSON.parse(localStorage.getItem(`paperduet-ask-${docId}`)||'null');
    if(saved?.sessions&&typeof saved.active==='string') {
      const sessions:Record<string,Session>={};
      for(const [key,value] of Object.entries(saved.sessions))if(validDraft(value as Draft))sessions[key]=fresh(key,{...(value as Draft)});
      if(sessions[saved.active]){for(const s of Object.values(sessions))s.loaded=!s.threadId;return {active:saved.active,sessions:keepDrafts(sessions,saved.active),saved:true};}
    }
    if(validDraft(saved)){const s=fresh(saved.threadId??undefined,saved);s.loaded=!s.threadId;return {active:s.key,sessions:{[s.key]:s},saved:true};}
  }catch{ /* Optional local drafts. */ }
  const s=fresh();return {active:s.key,sessions:{[s.key]:s},saved:false};
}
export function fallbackTitle(thread:ThreadSummary,doc:Document) {
  if(thread.title)return thread.title;
  try{const anchor=JSON.parse(thread.anchor) as Anchor;return (anchor.field==='document'?doc.title:anchor.text||doc.blocks.find(b=>b.id===anchor.block_id)?.n||doc.title).slice(0,120);}
  catch{return '대화';}
}

/** Each stream owns a session; changing the visible session never redirects its updates. */
export function useAskConversations(doc:Document) {
  const [seed]=useState(()=>restore(doc.id));
  const [sessions,setSessions]=useState(seed.sessions);const cache=useRef(sessions);
  const [active,setActive]=useState(seed.active);const activeRef=useRef(active);
  const [draftRevision,setDraftRevision]=useState(0);
  const [settings,setSettings]=useState<AskSettings|null>(null);const [threads,setThreads]=useState<ThreadSummary[]>([]);const [ready,setReady]=useState(false);
  const controllers=useRef(new Map<string,AbortController>());const alive=useRef(true);const explicitConversation=useRef(false);
  const put=useCallback((key:string,change:Partial<Session>|((old:Session)=>Partial<Session>))=>{
    const old=cache.current[key];if(!old||!alive.current)return;
    const next={...old,...(typeof change==='function'?change(old):change)};
    if((['threadId','title','anchor','question','preset','full','includeImage'] as const).some(field=>old[field]!==next[field]))setDraftRevision(value=>value+1);
    cache.current={...cache.current,[key]:next};setSessions(cache.current);
  },[]);
  const insert=(s:Session)=>{cache.current={...cache.current,[s.key]:s};setSessions(cache.current);setDraftRevision(value=>value+1);};
  const activate=(key:string)=>{cache.current=keepDrafts(cache.current,key);setSessions(cache.current);activeRef.current=key;setActive(key);setDraftRevision(value=>value+1);};
  const update=(change:Partial<Draft>&{snapshot?:unknown;notice?:string;error?:string})=>put(activeRef.current,old=>({...change,anchorVersion:old.anchorVersion+('anchor' in change?1:0)}));
  const refreshThreads=async()=>{const list=await request<ThreadSummary[]>('/threads?doc_id='+doc.id);if(alive.current)setThreads(list);return list;};
  const load=async(key:string)=>{
    const original=cache.current[key];if(!original?.threadId)return;
    try {
      const thread=await request<Thread>('/threads/'+original.threadId+'?compact=true');
      put(key,old=>({loaded:true,messages:thread.messages,title:old.title||thread.title||'',snapshot:thread.messages.filter(m=>m.role==='assistant').at(-1)?.context_snapshot,
        ...(old.restoreAnchor&&old.anchorVersion===original.anchorVersion?{anchor:thread.anchor.field==='document'?null:thread.anchor}:{}),restoreAnchor:false}));
    }catch(e){put(key,{loaded:true,error:(e as Error).message});}
  };
  useEffect(()=>{
    alive.current=true;let live=true;
    void Promise.all([request<AskSettings>('/settings/ask'),request<ThreadSummary[]>('/threads?doc_id='+doc.id)]).then(([preferences,list])=>{
      if(!live)return;setSettings(preferences);setThreads(list);
      if(!seed.saved&&list[0]&&!explicitConversation.current){
        const original=cache.current[activeRef.current];
        const s=fresh(list[0].id,{threadId:list[0].id,title:list[0].title,anchor:original.anchor,question:original.question,preset:original.preset});s.loaded=false;s.restoreAnchor=original.anchorVersion===0;insert(s);activate(s.key);void load(s.key);
      }else {
        for(const s of Object.values(cache.current))if(s.threadId&&!list.some(t=>t.id===s.threadId))put(s.key,{threadId:null,loaded:true});
        if(!cache.current[activeRef.current].loaded)void load(activeRef.current);
      }
      setReady(true);
    }).catch(e=>{if(live){put(activeRef.current,{error:e.message});setReady(true);}});
    return()=>{live=false;alive.current=false;for(const controller of controllers.current.values())controller.abort();controllers.current.clear();};
  },[]);
  const draftJSON=useMemo(()=>JSON.stringify({active,sessions:Object.fromEntries(Object.entries(cache.current).map(([key,s])=>[key,{threadId:s.threadId,title:s.title,anchor:s.anchor,question:s.question,preset:s.preset,full:s.full,includeImage:s.includeImage}]))}),[active,draftRevision]);
  useEffect(()=>{if(ready)try{localStorage.setItem(`paperduet-ask-${doc.id}`,draftJSON);}catch{ /* optional preference */ }},[ready,doc.id,draftJSON]);

  const change=(id:string)=>{
    let s=cache.current[id]||Object.values(cache.current).find(s=>s.threadId===id);
    if(!s){const t=threads.find(t=>t.id===id);if(!t)return;s=fresh(id,{threadId:id,title:t.title});s.loaded=false;s.restoreAnchor=true;insert(s);}
    activate(s.key);if(!s.loaded)void load(s.key);
  };
  const create=(anchor?:Anchor)=>{
    explicitConversation.current=true;
    if(!anchor){const empty=Object.values(cache.current).find(s=>!s.threadId&&!s.busy&&!s.question&&!s.anchor&&!s.title);if(empty){activate(empty.key);return;}}
    const s=fresh(undefined,{anchor:anchor??null});insert(s);activate(s.key);
  };
  const rename=async(key:string,title:string)=>{
    const s=cache.current[key];if(!s||!title.trim())return;
    try{if(s.threadId){const result=await request<{title:string}>(`/threads/${s.threadId}`,{method:'PATCH',body:JSON.stringify({title:title.trim()})});put(key,{title:result.title});await refreshThreads();}
      else put(key,{title:title.trim()});
    }catch(e){put(key,{error:(e as Error).message});}
  };
  const paperAnchor=targetAnchor({kind:'paper',blockId:doc.blocks.find(b=>!b.note)?.id??doc.blocks[0]?.id??''});
  const payload=(s:Session,regenerate=false)=>({doc_id:doc.id,anchor:s.anchor??paperAnchor,question:s.question.trim()||'이 논문의 핵심을 설명해 주세요.',preset:s.preset||null,
    thread_id:s.threadId,thread_title:s.title||selectionTitle(s.anchor,doc)||null,regenerate,provider:settings?.provider,mode:settings?.mode,model:settings?.model,full_context:s.full,include_image:s.includeImage});
  const send=async(regenerate=false)=>{
    const key=activeRef.current;const s=cache.current[key];
    if(!settings||!ready||!s.loaded||controllers.current.has(key)||(!regenerate&&!s.question.trim()))return;
    const body=payload(s,regenerate),abort=new AbortController();controllers.current.set(key,abort);put(key,{busy:true,error:'',notice:''});
    let messageId='';let done=false;
    const pendingMessage:Message={id:'pending-'+key,role:'user',content_md:body.question,status:'complete',tokens_in:null,tokens_out:null,context_snapshot:{}};
    try {
      await request('/settings/ask',{method:'PUT',body:JSON.stringify(settings),signal:AbortSignal.any([abort.signal,AbortSignal.timeout(20000)])});
      abort.signal.throwIfAborted();
      if(!regenerate)put(key,old=>({messages:[...old.messages,pendingMessage],question:'',preset:''}));
      const response=await authorizedFetch('/ask',{method:'POST',body:JSON.stringify(body),signal:abort.signal});
      if(!response.ok||!response.body){const value=await response.json().catch(()=>({}));throw new Error(ERROR_TEXT[value.detail]||'질문을 시작하지 못했습니다.');}
      for await(const event of readEvents<AskEvent>(response)){
          if(event.event==='start'){
            messageId=event.message_id;
            put(key,old=>({threadId:event.thread_id,title:old.title||body.thread_title||body.question.slice(0,120),snapshot:event.context,granularity:event.stream_granularity,
              messages:[...old.messages,{id:messageId,role:'assistant',content_md:'',status:'running',tokens_in:null,tokens_out:null,context_snapshot:event.context}]}));
            const current=cache.current[key];
            if(current.title&&current.title!==(body.thread_title||body.question.slice(0,120)))void rename(key,current.title);
            if(alive.current)setThreads(old=>old.some(t=>t.id===event.thread_id)?old:[{id:event.thread_id,title:current.title,block_id:body.anchor.block_id,anchor:JSON.stringify(body.anchor),message_count:2,running:true},...old]);
          }
          if(event.event==='delta')put(key,old=>({messages:old.messages.map(m=>m.id===messageId?{...m,content_md:m.content_md+event.text}:m)}));
          if(event.event==='error')put(key,old=>({error:ERROR_TEXT[event.code]||'AI 응답이 중단되었습니다. 연결을 확인해 주세요.',...(!regenerate?{question:old.question||body.question}:{})}));
          if(event.event==='done'){
            done=true;
            put(key,old=>({messages:old.messages.map(m=>m.id===messageId?{...m,status:event.status,tokens_in:event.usage?.tokens_in??null,tokens_out:event.usage?.tokens_out??null}:m)}));
            await load(key);await refreshThreads();
          }
      }
      if(!done)throw new Error('응답 연결이 끊겼습니다. 다시 질문하거나 재생성해 주세요.');
    }catch(e){
      const unsent=abort.signal.aborted&&!messageId&&!regenerate&&!done;
      put(key,old=>({notice:abort.signal.aborted?'응답을 중지했습니다.':old.notice,error:abort.signal.aborted?old.error:(e as Error).message,
        ...(unsent?{question:old.question||s.question,preset:old.preset||s.preset}:!abort.signal.aborted&&!regenerate&&!done?{question:old.question||body.question}:{}),
        messages:old.messages.filter(m=>!unsent||m!==pendingMessage).map(m=>m.id===messageId&&m.status==='running'?{...m,status:abort.signal.aborted?'cancelled':'interrupted'}:m)}));
    }
    finally{controllers.current.delete(key);put(key,{busy:false});}
  };
  const current=sessions[active];
  const byThread=new Map(Object.values(sessions).filter(s=>s.threadId).map(s=>[s.threadId,s]));
  const summaries=threads.map(t=>{const local=byThread.get(t.id);return {...t,title:local?.title||t.title,running:local?.busy??t.running};});
  return {settings,setSettings,ready,threads:summaries,session:current,sessions,update,updateSession:put,change,create,rename,send,payload:()=>payload(cache.current[activeRef.current]),
    stop:()=>controllers.current.get(activeRef.current)?.abort()};
}
