import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from './api';
import { AskPanel, SelectionActions, useAsk } from './ask';
import { PdfPreview } from './pdf-preview';
import { DocumentActions } from './m4';
import { Inline, NOTE_NAMES, ReaderBlock } from './blocks';
import { BrandMark, Icon } from './icons';
import { SummaryView } from './summary';
import { resolveTheme } from './theme';
import { primaryKey, shortcut } from './platform';
import { ReadingContent, ReadingProgress, type ProgressHandle, type ReadingHandle } from './reading-content';
import { Notebook } from './notebook';
import { ReadingSettings } from './reading-settings';
import './styles/book.css';
import type { Block, Document, NoteKind, Settings, View } from './types';

const INITIAL: Settings = { view: 'split', theme: 'light', font_size: 16, show_notes: true, note_kinds: ['key', 'res', 'lim', 'ins', 'mth', 'trm'], density: 'high' };
const textOnly = (html: string) => html.replace(/<[^>]*>/g, '').replace(/&[a-z0-9#]+;/gi, ' ');
export function App({docId,onLibrary,onProcess,onTutorial,offline=false}:{docId:string;onLibrary?:()=>void;onProcess?:()=>void;onTutorial?:()=>void;offline?:boolean}) {
  const [doc, setDoc] = useState<Document | null>(null);
  const ask=useAsk(doc,!offline);
  const askImage=useCallback((block:Block)=>ask.open({kind:'figure',blockId:block.id}),[ask.open]);
  const [pdfOpen,setPdfOpen]=useState(false);
  const [settings, setSettings] = useState<Settings>(() => ({ ...INITIAL, theme: resolveTheme() }));
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [matchIndex, setMatchIndex] = useState(0);
  const [activeId, setActiveId] = useState('b0000');
  const progress=useRef<ProgressHandle>(null);
  const [copy, setCopy] = useState('');
  const [memos,setMemos]=useState(false);
  const reading=useRef<ReadingHandle>(null);
  // 본문 | 요약. While the summary is open the reading position is left untouched.
  const [tab, setTab] = useState<'paper' | 'summary'>('paper');
  const [pendingJump, setPendingJump] = useState<string | null>(null);
  const summaryOpen = useRef(false);
  summaryOpen.current = tab === 'summary';
  const searchRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const tocScrollRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const restored = useRef(false);
  const latestPosition = useRef({ block_id: null as string | null, offset: 0 });
  const settingsLoaded = useRef(false);
  const loadedSettings = useRef('');
  const latestSettings=useRef(settings);latestSettings.current=settings;
  const startupAbort = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    startupAbort.current?.abort();
    const controller = new AbortController();
    startupAbort.current = controller;
    setLoading(true); setError('');
    for (let attempt = 0; attempt < 60 && !controller.signal.aborted; attempt++) {
      try {
        const [paper, preferences] = await Promise.all([
          request<Document>(`/documents/${docId}`), request<Settings>('/settings/reader'),
        ]);
        if (controller.signal.aborted) return;
        loadedSettings.current = JSON.stringify(preferences);
        // An old 'system' setting resolves once and is then saved as light or dark.
        preferences.theme = resolveTheme(preferences.theme);
        setSettings(paper.status !== 'fixture' && !paper.blocks.some(b=>b.ko) ? {...preferences,view:'en'} : preferences); setDoc(paper);  settingsLoaded.current = true; setLoading(false); return;
      } catch {
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }
    if (!controller.signal.aborted) { setLoading(false); setError('로컬 리더에 연결하지 못했습니다. 앱을 다시 실행하거나 연결을 재시도해 주세요.'); }
  }, [docId,offline]);
  useEffect(() => { void load(); return () => startupAbort.current?.abort(); }, [load]);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = settings.theme; document.body.dataset.view = settings.view;
    root.style.setProperty('--fs', `${settings.font_size}px`);
    if (!settingsLoaded.current || JSON.stringify(settings) === loadedSettings.current) return;
    const timer = setTimeout(() => {
      void request('/settings/reader', { method: 'PUT', body: JSON.stringify(settings) })
        .then(() => { loadedSettings.current = JSON.stringify(settings); setSaveError(false); }).catch(() => setSaveError(true));
    }, 200);
    return () => clearTimeout(timer);
  }, [settings]);
  // Leaving immediately after a theme/font change must also save the preference.
  useEffect(()=>()=>{
    if(settingsLoaded.current&&JSON.stringify(latestSettings.current)!==loadedSettings.current)
      void request('/settings/reader',{method:'PUT',body:JSON.stringify(latestSettings.current),keepalive:true}).catch(()=>{});
  },[]);

  const headings = useMemo(() => doc?.blocks.filter(b => ['sec', 'sub', 'ssub'].includes(b.type)) ?? [], [doc]);
  const evidence = useMemo(()=>new Map(doc?.blocks.filter(b=>!b.note).map(b=>[b.id,b.n||`${b.section_path.at(-1)||'본문'} · ${b.id}`])??[]),[doc]);
  const refreshDoc = async () => {setDoc(await request<Document>(`/documents/${docId}`));};
  const shownBlocks = useMemo(() => doc?.blocks.filter(b => !b.note || (
    settings.show_notes && settings.note_kinds.includes(b.note.kind) &&
    (settings.density === 'high' || (settings.density === 'normal' ? b.note.kind !== 'trm' : b.note.kind === 'key'))
  )) ?? [], [doc, settings.show_notes, settings.note_kinds, settings.density]);
  const matches = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    if (!q) return [];
    return doc?.blocks.filter(b => [b.en, b.ko, b.caption_en, b.caption_ko, b.card?.body, b.card?.explain_ko,
      b.note?.title, b.note?.body_md, b.table ? [...b.table.header, ...b.table.body].flat().map(c => c.text_en+' '+(c.text_ko||'')).join(' ') : '']
      .some(s => textOnly(s ?? '').toLocaleLowerCase().includes(q))) ?? [];
  }, [doc, query]);

  const jump = useCallback((id: string, offset = 0) => {
    const element = document.getElementById(id);
    if (element) {
      reading.current?.jump(id,offset);
      history.replaceState(null, '', `${location.pathname}${location.search}#${id}`);
    }
  }, []);
  // From the summary (or the contents while it is open): back to the paper, then to the block.
  const goToBlock = useCallback((id: string) => { setTab('paper'); setPendingJump(id); }, []);
  const switchTab = (next: 'paper' | 'summary') => {
    if (next === tab) return;
    setTab(next);
    if (next === 'paper') { if (latestPosition.current.block_id) setPendingJump(latestPosition.current.block_id); }
    else requestAnimationFrame(() => document.querySelector('.reader-tabs')?.scrollIntoView({ block: 'start' }));
  };
  useEffect(() => {
    if (tab !== 'paper' || !pendingJump) return;
    const id = pendingJump; setPendingJump(null);
    requestAnimationFrame(() => {
      const back = id === latestPosition.current.block_id;
      jump(id, back ? latestPosition.current.offset : 0);
      if (back) return;
      const element = document.getElementById(id);
      element?.classList.add('summary-target');
      setTimeout(() => element?.classList.remove('summary-target'), 2000);
    });
  }, [tab, pendingJump, jump]);

  useEffect(() => {
    if (!doc || restored.current) return;
    let cancelled = false;
    void document.fonts.ready.then(() => {
      if (cancelled) return;
      requestAnimationFrame(() => {
        const id = location.hash.slice(1) || doc.reading_position.block_id;
        if (id) jump(id, location.hash && id!==doc.reading_position.block_id ? 0 : doc.reading_position.offset);
        restored.current = true;
      });
    });
    return () => { cancelled = true; };
  }, [doc, jump]);

  const positionTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
  const savePosition=useCallback(()=>{
    if(!doc||!restored.current)return;
    void request(`/documents/${doc.id}/position`,{method:'PATCH',body:JSON.stringify(latestPosition.current),keepalive:true}).then(()=>setSaveError(false)).catch(()=>setSaveError(true));
  },[doc?.id]);
  const onPosition=useCallback((position:{block_id:string|null;offset:number},percent:number)=>{
    if(summaryOpen.current)return;
    latestPosition.current=position;progress.current?.update(percent);
    const current=doc?.blocks.find(b=>b.id===position.block_id);
    setActiveId(headings.filter(h=>h.order<=(current?.order??-1)).at(-1)?.id||headings[0]?.id||'');
    if(restored.current){
      if(location.hash&&position.block_id&&location.hash.slice(1)!==position.block_id)history.replaceState(null,'',`${location.pathname}${location.search}#${position.block_id}`);
      clearTimeout(positionTimer.current);positionTimer.current=setTimeout(savePosition,350);
    }
  },[doc,headings,savePosition]);
  useEffect(()=>{window.addEventListener('pagehide',savePosition);return()=>{clearTimeout(positionTimer.current);savePosition();window.removeEventListener('pagehide',savePosition);};},[savePosition]);

  useEffect(() => {
    const scroller = tocScrollRef.current;
    const link = scroller?.querySelector<HTMLElement>(`[href="#${activeId}"]`);
    if (scroller && link) {
      const top = link.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      if (top < scroller.scrollTop + 8 || top + link.offsetHeight > scroller.scrollTop + scroller.clientHeight - 8) {
        scroller.scrollTop = Math.max(0, top - scroller.clientHeight / 2);
      }
    }
  }, [activeId, drawer]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if(event.defaultPrevented||document.querySelector('dialog[open]')||(event.target as Element)?.closest('.floating-window'))return;
      if(event.key==='Escape'&&ask.visible){event.preventDefault();ask.close();return;}
      if (primaryKey(event) && ['1', '2', '3'].includes(event.key)) {
        event.preventDefault(); setSettings(s => ({ ...s, view: ({ '1': 'en', '2': 'split', '3': 'ko' } as Record<string, View>)[event.key] }));
      }
      if (primaryKey(event) && event.key.toLowerCase() === 'f') { event.preventDefault(); setSearchOpen(true); }
      if (event.key === 'Escape') { setDrawer(false); setSearchOpen(false); menuRef.current?.focus(); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ask.visible,ask.close]);
  useEffect(() => { if (searchOpen) searchRef.current?.focus(); }, [searchOpen]);
  useEffect(()=>{if(drawer)(drawerRef.current?.querySelector<HTMLElement>('a[aria-current]')||drawerRef.current?.querySelector<HTMLElement>('a'))?.focus({preventScroll:true});},[drawer]);

  const findMatch = (index: number) => {
    if (!matches.length) return;
    const next = (index + matches.length) % matches.length;
    setMatchIndex(next);
    const block = matches[next];
    if (block.note) setSettings(s => ({ ...s, show_notes: true, density: 'high', note_kinds: [...new Set([...s.note_kinds, block.note!.kind])] }));
    requestAnimationFrame(() => jump(block.id));
  };
  const anchorClick = (event: React.MouseEvent) => {
    const a = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="#"]');
    if (a) { event.preventDefault(); if (summaryOpen.current) goToBlock(a.hash.slice(1)); else jump(a.hash.slice(1)); }
  };

  if (loading || error) return <div className="startup"><BrandMark className="brand-mark startup-mark"/><h1>PaperDuet</h1>
    <p role={error ? 'alert' : 'status'}>{error || '로컬 서재를 열고 있습니다…'}</p>{error && <button onClick={() => void load()}>연결 재시도</button>}</div>;
  if (!doc) return null;
  return <div className="reader book-reader" onClick={anchorClick} onPointerDownCapture={event => {
    if (event.button !== 0 || document.querySelector('dialog[open]') || (event.target as Element).closest('.floating-window,.reader-assistant-dock')) return;
    if (memos) setMemos(false);
  }} onKeyDownCapture={event => {
    // Esc dismisses the open native picker before a tool window or the TOC.
    if (event.key === 'Escape' && CSS.supports('selector(select:open)') && (event.target as Element).closest('select:open')) event.stopPropagation();
  }}>
    <a className="skip-link" href="#paper">본문으로 이동</a>
    <header className="topbar book-topbar"><div className="toolbar">
      {onLibrary&&!offline?<a className="brand brand-link" href="?library=1" title="서재로 이동" aria-label="PaperDuet · 서재로 이동" onClick={e=>{e.preventDefault();onLibrary();}}><BrandMark/></a>:<div className="brand"><BrandMark/></div>}
      <button ref={menuRef} className="icon-button menu-button" aria-label="목차 열기" aria-expanded={drawer} aria-controls="toc" onClick={() => setDrawer(d=>!d)}><Icon name="menu"/></button>
      <span className="paper-name" title={doc.title}>{doc.title}</span>
      <div className="reader-dock" role="toolbar" aria-label="읽기 도구">
      <div className="view-controls" role="group" aria-label="보기 모드">{([['en', '원문'], ['split', '대역'], ['ko', '번역']] as const).map(([mode, name]) =>
        <button key={mode} aria-pressed={settings.view === mode} onClick={() => setSettings(s => ({ ...s, view: mode }))}>{name}</button>)}</div>
      <span className="dock-separator" aria-hidden="true"/>
      <button className="icon-button" onClick={() => setSearchOpen(s => !s)} aria-label="문서 검색" title={`문서 검색 (${shortcut('F')})`}><Icon name="search"/></button>
      <ReadingSettings value={settings} onChange={setSettings} onGuide={onTutorial&&!offline?onTutorial:undefined}/>
      </div>
    </div><ReadingProgress ref={progress}/>
    {searchOpen && <div className="search-panel" role="search"><label htmlFor="document-search">문서 검색</label><input id="document-search" ref={searchRef} value={query} placeholder="원문과 번역에서 찾기" onChange={e => { setQuery(e.target.value); setMatchIndex(0); }} onKeyDown={e => { if (e.key === 'Enter') findMatch(e.shiftKey ? matchIndex - 1 : matchIndex); }}/>
      <span role="status">{matches.length ? `${Math.min(matchIndex + 1, matches.length)} / ${matches.length}` : '0건'}</span><button onClick={() => findMatch(matchIndex - 1)} disabled={!matches.length} aria-label="이전 검색 결과"><Icon name="chevronUp"/></button><button onClick={() => findMatch(matchIndex + 1)} disabled={!matches.length} aria-label="다음 검색 결과"><Icon name="chevronDown"/></button><button className="icon-button" onClick={() => setSearchOpen(false)} aria-label="검색 닫기"><Icon name="close"/></button></div>}
    </header>
    {saveError && <div className="save-warning" role="status">읽기 설정 저장을 기다리고 있습니다. 로컬 연결이 복구되면 다시 시도해 주세요.</div>}
    <nav id="toc" ref={drawerRef} className={`toc ${drawer ? 'open' : ''}`} aria-label="목차" aria-hidden={!drawer} inert={!drawer} onKeyDown={e => { if (e.key === 'Escape') setDrawer(false); }}>
      <div className="toc-heading"><b>목차</b><button className="icon-button drawer-close" aria-label="목차 닫기" onClick={() => setDrawer(false)}><Icon name="close"/></button></div>
      <div className="toc-scroll" ref={tocScrollRef}>
      <div className="toc-links">{headings.map(h => <a key={h.id} href={`#${h.id}`} className={`depth-${h.type}`} aria-current={activeId === h.id ? 'location' : undefined}><span>{h.n === 'Abstract' ? '00' : h.n}</span><Inline text={h.ko||h.en} /></a>)}</div>
      <div className="note-options"><label className="note-toggle"><input type="checkbox" checked={settings.show_notes} onChange={e => setSettings(s => ({ ...s, show_notes: e.target.checked }))}/>맥락 주석 <span>{doc.blocks.filter(b=>b.note).length}</span></label>
        <div className="note-filters">{(Object.entries(NOTE_NAMES) as [NoteKind, string][]).map(([kind, name]) => <button key={kind} className={`filter-${kind}`} aria-pressed={settings.note_kinds.includes(kind)} onClick={() => setSettings(s => ({ ...s, note_kinds: s.note_kinds.includes(kind) ? s.note_kinds.filter(k => k !== kind) : [...s.note_kinds, kind] }))}><i />{name}</button>)}</div>
        <label className="density">주석 밀도<select value={settings.density} onChange={e => setSettings(s => ({ ...s, density: e.target.value as Settings['density'] }))}><option value="low">낮음 · 핵심</option><option value="normal">보통</option><option value="high">높음 · 전체</option></select></label>
      </div><div className="offline-indicator"><i/>오프라인 열람 가능</div>
      </div>
    </nav>
    <main id="paper" className="book-main">
    <div className="book-subbar">{!offline&&<><div className="reader-tabs" role="tablist" aria-label="보기">{([['paper','본문'],['summary','요약']] as const).map(([id,name])=><button key={id} role="tab" aria-selected={tab===id} onClick={()=>switchTab(id)}>{name}</button>)}</div><DocumentActions doc={doc}/></>}
      <details className="reader-more"><summary className="icon-button" aria-label="논문 정보"><Icon name="more"/></summary><div><b>{doc.title}</b><p>{doc.authors}</p><p>{doc.page_count}쪽{doc.arxiv_id&&` · arXiv ${doc.arxiv_id}`}</p>
        {!offline&&doc.status!=='fixture'&&<div className="document-status"><span>{doc.untranslated_count===0?'번역 완료':doc.untranslated_count!==undefined?`번역 ${doc.translated_count??0}개 · 미번역 ${doc.untranslated_count}개`:doc.status==='complete'||doc.status==='review'?'번역 완료':'미번역'}</span><button onClick={onProcess}>처리 상태·번역·주석</button>{doc.source_kind!=='arxiv_html'&&<button onClick={()=>setPdfOpen(true)}>원본 PDF 보기</button>}</div>}
        {!!doc.glossary.length&&<details><summary>용어집 · {doc.glossary.length}개</summary><dl>{doc.glossary.map(g=><div key={g.term}><dt>{g.term} · {g.ko}</dt><dd>{g.definition_ko}</dd></div>)}</dl></details>}
        <button aria-label="현재 위치 링크 복사" onClick={async()=>{try{await navigator.clipboard.writeText(`#${latestPosition.current.block_id??doc.blocks[0]?.id}`);setCopy('복사됨');}catch{setCopy('복사 실패');}}}>{copy||'현재 위치 링크 복사'}</button><button aria-label="맨 위로" onClick={()=>{reading.current?.first();history.replaceState(null,'',`${location.pathname}${location.search}`);}}>처음으로</button>
      </div></details>
    </div>
    {tab==='summary'?<div className="book-summary"><SummaryView doc={doc} labels={evidence} onJump={goToBlock}/></div>:<ReadingContent ref={reading} blocks={shownBlocks} position={latestPosition.current} layoutKey={JSON.stringify(settings)} onPosition={onPosition}>
      <section className="book-intro"><span className="eyebrow">{doc.arxiv_id?`arXiv ${doc.arxiv_id}`:'PaperDuet'}</span><h1>{doc.title}</h1>{doc.title_ko&&<h2>{doc.title_ko}</h2>}<p>{doc.authors||(doc.page_count?`${doc.page_count}쪽`:'샘플 논문')}</p>{offline&&<p className="export-notice">독립 HTML · 오프라인 열람용</p>}</section>
      {shownBlocks.map(b => <div key={b.id} className={`reader-block-wrap ${['sec','sub','ssub'].includes(b.type)?'heading-block':''}`}><ReaderBlock block={b} evidence={evidence} onAskImage={offline?undefined:askImage} />{!offline&&doc.status!=='fixture'&&!b.section_path.some(p=>/^(references|bibliography)$/i.test(p))&&<BlockTools block={b} onUpdate={refreshDoc}/>}</div>)}
    </ReadingContent>}
    </main>
    {pdfOpen&&<PdfPreview docId={doc.id} pages={doc.page_count} onClose={()=>setPdfOpen(false)} onProcess={()=>onProcess?.()}/>}
    {ask.selected&&<SelectionActions anchor={ask.selected} onOpen={anchor=>ask.open({kind:'text',anchor})}/>}
    {ask.opened&&!offline&&<AskPanel key={doc.id} doc={doc} selection={ask.anchor} visible={ask.visible} activation={ask.activation} onClose={ask.close} onSaved={refreshDoc}/>}
    {!offline&&<div className="reader-assistant-dock" role="toolbar" aria-label="보조 기능"><button aria-pressed={ask.visible} onClick={()=>ask.open()}><Icon name="message"/>Ask AI</button><span aria-hidden="true"/><button aria-pressed={memos} onClick={()=>setMemos(v=>!v)}><Icon name="notebook"/>메모장</button></div>}
    {memos&&<Notebook doc={doc} onClose={()=>setMemos(false)} onJump={goToBlock}/>}
  </div>;
}

function BlockTools({block,onUpdate}:{block:Block;onUpdate:()=>Promise<void>}) {
  const [value,setValue]=useState(block.ko??block.caption_ko??'');const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const caption=!!block.caption_en;
  const run=async(regenerate:boolean)=>{setBusy(true);setError('');try{const result=await request<Block>(`/documents/${block.doc_id}/blocks/${block.id}${regenerate?'/regenerate':''}`,{method:regenerate?'POST':'PATCH',body:regenerate?undefined:JSON.stringify({[caption?'caption_ko':'ko']:value})});await onUpdate();setValue(result.ko??result.caption_ko??'');}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  if(block.note)return <NoteTools block={block} onUpdate={onUpdate}/>;
  if(block.type==='eq')return <div className="block-editor"><button disabled={busy} onClick={()=>void run(true)}>이 수식 복원 재시도</button>{error&&<p role="alert">{error}</p>}</div>;
  if(!caption&&!['sec','sub','ssub','p','li'].includes(block.type))return null;
  return <details className="block-editor"><summary>{value.trim()?'번역 수정':'번역하기'}</summary><label>한국어 {caption?'캡션':'번역'}<textarea disabled={busy} value={value} onChange={e=>setValue(e.target.value)} rows={4}/></label><button disabled={busy} onClick={()=>void run(false)}>수정 저장</button> <button disabled={busy} onClick={()=>void run(true)}>{block.type==='tab'&&block.image_path?'원본 이미지로 표 다시 복원':'이 블록 재번역'}</button>{busy&&<span role="status"> 처리 중…</span>}{error&&<p role="alert">{error}</p>}</details>;
}

function NoteTools({block,onUpdate}:{block:Block;onUpdate:()=>Promise<void>}) {
  const [note,setNote]=useState(block.note!);const [refs,setRefs]=useState(note.refs.join(', '));const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const run=async(regenerate:boolean)=>{setBusy(true);setError('');try{const result=await request<Block>(`/documents/${block.doc_id}/blocks/${block.id}${regenerate?'/regenerate':''}`,{method:regenerate?'POST':'PATCH',body:regenerate?undefined:JSON.stringify({note:{...note,refs:[...new Set(refs.split(/[\s,]+/).filter(Boolean))]}})});setNote(result.note!);setRefs(result.note!.refs.join(', '));await onUpdate();}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <details className="block-editor note-editor"><summary>주석 수정·재생성</summary><label>주석 제목<input disabled={busy} value={note.title} maxLength={300} onChange={e=>setNote({...note,title:e.target.value})}/></label><label>주석 내용<textarea disabled={busy} value={note.body_md} rows={6} maxLength={12000} onChange={e=>setNote({...note,body_md:e.target.value})}/></label><label>주장 유형<select disabled={busy} value={note.claim} onChange={e=>setNote({...note,claim:e.target.value as typeof note.claim})}><option value="stated">논문 명시</option><option value="interpretation">해석</option><option value="mixed">혼합</option></select></label><label>근거 블록 ID (쉼표로 구분)<input disabled={busy} value={refs} onChange={e=>setRefs(e.target.value)}/></label><button disabled={busy||!note.title.trim()||!note.body_md.trim()||!refs.trim()} onClick={()=>void run(false)}>주석 수정 저장</button> <button disabled={busy} onClick={()=>void run(true)}>이 주석 재생성</button>{busy&&<span role="status"> 주석 처리 중…</span>}{error&&<p role="alert">{error}</p>}</details>;
}
