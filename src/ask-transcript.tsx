import { memo, type Ref, type UIEvent } from 'react';
import { AnswerMarkdown } from './markdown';
import { NOTE_NAMES } from './blocks';
import type { Message } from './ask-conversations';
import type { NoteKind } from './types';

type Actions={noteKind:NoteKind;onKind:(kind:NoteKind)=>void;onCopy:(message:Message,markdown:boolean)=>void;onNote:(message:Message,kind:NoteKind)=>void;onMemo:(message:Message)=>void};
const AskMessage=memo(function AskMessage({message:m,granularity,...actions}:{message:Message;granularity:string}&Actions){
  return <article data-message={m.id} className={`ask-message ${m.role}`}><b>{m.role==='user'?'나':'AI'}</b>{m.role==='user'?<p>{m.content_md}</p>:<>
    <AnswerMarkdown text={m.content_md||(granularity==='message'?'CLI가 답변을 작성 중입니다…':'답변을 기다리는 중입니다…')}/>
    <small>{m.status==='complete'?`입력 ${m.tokens_in??'—'} · 출력 ${m.tokens_out??'—'} 토큰`:m.status==='running'?'응답 중…':'중단된 답변'}</small>
    {m.status==='complete'&&<div className="answer-actions"><button onClick={()=>actions.onCopy(m,true)}>Markdown 복사</button><button onClick={()=>actions.onCopy(m,false)}>평문 복사</button>
      <select aria-label="저장할 주석 유형" value={actions.noteKind} onChange={e=>actions.onKind(e.target.value as NoteKind)}>{Object.entries(NOTE_NAMES).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select>
      <button onClick={()=>actions.onNote(m,actions.noteKind)}>주석으로 저장</button><button onClick={()=>actions.onMemo(m)}>메모장에 추가</button></div>}
  </>}</article>;
});
export function AskTranscript({messages,loaded,selected,granularity,elementRef,onScroll,...actions}:{messages:Message[];loaded:boolean;selected:boolean;granularity:string;elementRef:Ref<HTMLDivElement>;onScroll:(event:UIEvent<HTMLDivElement>)=>void}&Actions){
  return <div className="ask-transcript" ref={elementRef} onScroll={onScroll} role="log" aria-label="질문과 답변">
    {!loaded?<p role="status">대화를 불러오는 중…</p>:!messages.length&&<p className="ask-empty">{selected?'선택한 내용에 대해 궁금한 점을 질문해 보세요.':<>논문에 대해 질문하거나,<br/>문장을 선택해 새 대화를 시작해 보세요.</>}</p>}
    {messages.map(message=><AskMessage key={message.id} message={message} granularity={granularity} {...actions}/>)}
  </div>;
}
