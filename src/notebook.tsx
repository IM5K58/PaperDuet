import { useEffect, useState } from 'react';
import { request } from './api';
import { Modal } from './connections';
import { FloatingWindow } from './floating-window';
import { AnswerMarkdown } from './markdown';
import type { Document } from './types';

type Memo = { id: string; title: string; body_md: string; block_id: string | null };
const draftKey = (docId: string, id: string | null) => `paperduet-memo-draft-${docId}-${id || 'new'}`;
function draft(docId: string, id: string | null) {
  try { const value = JSON.parse(localStorage.getItem(draftKey(docId, id)) || 'null'); if (typeof value?.title === 'string' && typeof value?.body === 'string') return value as { title: string; body: string }; } catch { /* optional draft */ }
  return undefined;
}
export function Notebook({ doc, onClose, onJump }: { doc: Document; onClose: () => void; onJump: (id: string) => void }) {
  const [memos, setMemos] = useState<Memo[]>([]), [selected, setSelected] = useState<string | null>(null);
  const [title, setTitle] = useState(() => draft(doc.id, null)?.title || ''), [body, setBody] = useState(() => draft(doc.id, null)?.body || '');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [removing, setRemoving] = useState<Memo | null>(null), [preview, setPreview] = useState(false);
  // The existing route also works with a sidecar built before the notebook UI.
  const base = `/documents/${doc.id}/presentation`;
  const refresh = () => request<Memo[]>(base).then(setMemos).catch(error => setError(error.message));
  useEffect(() => { void refresh(); const update = () => { void refresh(); }; window.addEventListener('paperduet-memos-changed', update); return () => window.removeEventListener('paperduet-memos-changed', update); }, [doc.id]);
  useEffect(() => { try { localStorage.setItem(draftKey(doc.id, selected), JSON.stringify({ title, body })); } catch { /* Saving to the backend remains available. */ } }, [doc.id, selected, title, body]);
  const current = memos.find(memo => memo.id === selected);
  const dirty = title !== (current?.title || '') || body !== (current?.body_md || '');
  const choose = (memo?: Memo) => { const value = draft(doc.id, memo?.id || null); setSelected(memo?.id || null); setTitle(value?.title ?? memo?.title ?? ''); setBody(value?.body ?? memo?.body_md ?? ''); setNotice(''); setError(''); };
  const save = async () => {
    if (busy || !body.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const value = { title: title.trim() || '메모', body_md: body };
      const created = await request<Memo | undefined>(base + (selected ? `/${selected}` : ''), { method: selected ? 'PUT' : 'POST', body: JSON.stringify(value) });
      const id = selected || created!.id;
      try { localStorage.removeItem(draftKey(doc.id, selected)); } catch { /* optional draft */ }
      setSelected(id); setTitle(value.title);
      await refresh(); setNotice('저장했습니다.');
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  };
  return <><FloatingWindow id="notebook" title="메모장" subtitle={doc.title} onClose={onClose} className="notebook-window">
    <div className="notebook-toolbar"><label>내 메모<select aria-label="내 메모" disabled={busy} value={selected || ''} onChange={event => choose(memos.find(memo => memo.id === event.target.value))}><option value="">새 메모</option>{memos.map(memo => <option key={memo.id} value={memo.id}>{memo.title}</option>)}</select></label><button disabled={busy} onClick={() => choose()}>새 메모</button>{current && <button disabled={busy} onClick={() => setRemoving(current)} aria-label="현재 메모 삭제">삭제</button>}</div>
    <div className="notebook-editor" onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); void save(); } }}>
      <input aria-label="메모 제목" placeholder="메모 제목" value={title} maxLength={300} disabled={busy} onChange={event => { setTitle(event.target.value); setNotice(''); }}/>
      {preview ? <div className="notebook-preview"><AnswerMarkdown text={body || '작성한 메모가 여기에 표시됩니다.'}/></div>
        : <textarea aria-label="메모 내용" placeholder="읽으면서 떠오른 생각을 자유롭게 적어 보세요." value={body} maxLength={30000} disabled={busy} onChange={event => { setBody(event.target.value); setNotice(''); }}/>}
    </div>
    {current?.block_id && <button className="memo-source" onClick={() => onJump(current.block_id!)}>원문 근거로 이동</button>}
    <footer className="notebook-footer"><span role="status">{error || (dirty ? '임시 보관 · 저장 전' : notice || '이 논문에 저장됨')}</span><button aria-pressed={preview} onClick={() => setPreview(value => !value)}>미리보기</button><button className="primary-button" disabled={busy || !body.trim() || !dirty} onClick={() => void save()}>{busy ? '저장 중…' : '메모 저장'}</button></footer>
  </FloatingWindow>{removing && <Modal title="메모 삭제" onClose={() => setRemoving(null)}><div className="dialog-content"><p>‘{removing.title}’ 메모를 삭제할까요?</p><div className="dialog-actions"><button onClick={() => setRemoving(null)}>취소</button><button className="primary-button" disabled={busy} onClick={async () => { setBusy(true); try { await request(`${base}/${removing.id}`, { method: 'DELETE' }); try { localStorage.removeItem(draftKey(doc.id, removing.id)); } catch { /* optional draft */ } setRemoving(null); setSelected(null); setTitle(''); setBody(''); await refresh(); } catch (error) { setError((error as Error).message); } finally { setBusy(false); } }}>삭제</button></div></div></Modal>}</>;
}
