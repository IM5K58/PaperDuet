import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { request } from './api';

/** Hidden diagnostics do no network or serialization work. Stored evidence stays on the server. */
export function AskContext({body,snapshot,threadId,messageId,visible,children}:{body:Record<string,unknown>;snapshot?:unknown;threadId:string|null;messageId?:string;visible:boolean;children:ReactNode}) {
  const [open,setOpen]=useState(false),[data,setData]=useState<unknown>(),[estimate,setEstimate]=useState<number>(),[error,setError]=useState('');
  const bodyJSON=open?JSON.stringify(body):'';
  useEffect(()=>{
    if(!open||!visible)return;
    setError('');setEstimate(undefined);
    if(snapshot&&typeof snapshot==='object'&&'messages' in snapshot){setData(snapshot);return;}
    const abort=new AbortController();setData(undefined);
    const timer=setTimeout(()=>{
      const load=threadId&&messageId
        ?request<unknown>(`/threads/${threadId}/messages/${messageId}/context`,{signal:abort.signal})
        :request<{snapshot:unknown;estimated_tokens:number}>('/ask/context',{method:'POST',body:bodyJSON,signal:abort.signal}).then(value=>{if(!abort.signal.aborted)setEstimate(value.estimated_tokens);return value.snapshot;});
      void load.then(value=>{if(!abort.signal.aborted)setData(value);}).catch(e=>{if(!abort.signal.aborted)setError((e as Error).message);});
    },300);
    return()=>{clearTimeout(timer);abort.abort();};
  },[open,visible,bodyJSON,snapshot,threadId,messageId]);
  const json=useMemo(()=>open&&data?JSON.stringify(data,null,2):'', [open,data]);
  return <details className="context-debug" onToggle={event=>setOpen(event.currentTarget.open)}><summary>전송 컨텍스트 확인</summary>{open&&<>{children}<small>{estimate!==undefined?`약 ${estimate.toLocaleString()} 토큰 · `:''}가장 최근 요청의 문맥입니다. 이미지 본문은 제외합니다.</small>{error?<p role="status">{error}</p>:<pre>{json||'문맥을 불러오는 중…'}</pre>}</>}</details>;
}
