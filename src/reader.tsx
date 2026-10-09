import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from './api';
import { AskPanel, SelectionActions, useAsk } from './ask';
import { PdfPreview } from './pdf-preview';
import { DocumentActions } from './m4';
import { Inline, NOTE_NAMES, ReaderBlock } from './blocks';
import { BrandMark, Icon } from './icons';
import { SummaryView } from './summary';
import { resolveTheme } from './theme';
import type { Block, Document, NoteKind, Settings, View } from './types';

const INITIAL: Settings = { view: 'split', theme: resolveTheme(), font_size: 16, show_notes: true, note_kinds: ['key', 'res', 'lim', 'ins', 'mth', 'trm'], density: 'high' };
const textOnly = (html: string) => html.replace(/<[^>]*>/g, '').replace(/&[a-z0-9#]+;/gi, ' ');
// Per-viewer convenience only: storage may be unavailable (private mode, exported file).
const GUIDE_KEY = 'paperduet.readingGuideHidden';
const guideHidden = () => { try { return localStorage.getItem(GUIDE_KEY) === '1'; } catch { return false; } };

export function App({docId='rex-omni',onLibrary,onProcess,onTutorial,offline=false}:{docId?:string;onLibrary?:()=>void;onProcess?:()=>void;onTutorial?:()=>void;offline?:boolean}) {
  const [doc, setDoc] = useState<Document | null>(null);
  const ask=useAsk(doc,!offline);
  const [pdfOpen,setPdfOpen]=useState(false);
  const [settings, setSettings] = useState<Settings>(INITIAL);
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [matchIndex, setMatchIndex] = useState(0);
  const [activeId, setActiveId] = useState('b0000');
  const [progress, setProgress] = useState(0);
  const [copy, setCopy] = useState('');
  const [guide, setGuide] = useState(() => !guideHidden());
  // 본문 | 요약. While the summary is open the reading position is left untouched.
  const [tab, setTab] = useState<'paper' | 'summary'>('paper');
  const [pendingJump, setPendingJump] = useState<string | null>(null);
  const summaryOpen = useRef(false);
  summaryOpen.current = tab === 'summary';
  const searchRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const restored = useRef(false);
  const latestPosition = useRef({ block_id: null as string | null, offset: 0 });
  const settingsLoaded = useRef(false);
  const loadedSettings = useRef('');
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
        setSettings(paper.status !== 'fixture' && !paper.blocks.some(b=>b.ko) ? {...preferences,view:'en'} : preferences); setDoc(paper); setPdfOpen(!offline&&paper.source_kind!=='arxiv_html'&&paper.status!=='fixture'&&!paper.blocks.some(b=>b.ko)&&paper.page_count>0); settingsLoaded.current = true; setLoading(false); return;
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
      const top = document.querySelector('.topbar')?.getBoundingClientRect().height ?? 64;
      window.scrollTo({ top: element.getBoundingClientRect().top + window.scrollY - top - 18 + offset, behavior: 'instant' });
      history.replaceState(null, '', `#${id}`);
    }
    setDrawer(false);
  }, []);
  // From the summary (or the contents while it is open): back to the paper, then to the block.
  const goToBlock = useCallback((id: string) => { setTab('paper'); setPendingJump(id); setDrawer(false); }, []);
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
        if (id) jump(id, location.hash ? 0 : doc.reading_position.offset);
        restored.current = true;
      });
    });
    return () => { cancelled = true; };
  }, [doc, jump]);

  useEffect(() => {
    if (!doc) return;
    let frame = 0;
    let saveTimer: ReturnType<typeof setTimeout> | undefined;
    const savePosition = () => {
      if (!restored.current || summaryOpen.current) return;
      void request(`/documents/${doc.id}/position`, { method: 'PATCH', body: JSON.stringify(latestPosition.current), keepalive: true })
        .then(() => setSaveError(false)).catch(() => setSaveError(true));
    };
    const update = () => {
      if (summaryOpen.current) { frame = 0; return; }
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(100, Math.max(0, window.scrollY / max * 100)) : 0);
      const threshold = (document.querySelector('.topbar')?.getBoundingClientRect().height ?? 64) + 28;
      let active = headings[0]?.id ?? '';
      for (const heading of headings) {
        const el = document.getElementById(heading.id);
        if (el && el.getBoundingClientRect().top <= threshold) active = heading.id;
      }
      setActiveId(active);
      let anchor: Element | null = null;
      for (const el of document.querySelectorAll('[data-block]')) {
        if (el.getBoundingClientRect().top <= threshold) anchor = el; else break;
      }
      latestPosition.current = anchor
        ? { block_id: anchor.id, offset: threshold - 10 - anchor.getBoundingClientRect().top }
        : { block_id: null, offset: 0 };
      frame = 0;
    };
    const scroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
      clearTimeout(saveTimer); saveTimer = setTimeout(savePosition, 350);
    };
    window.addEventListener('scroll', scroll, { passive: true }); window.addEventListener('resize', scroll);
    window.addEventListener('pagehide', savePosition); update();
    return () => { window.removeEventListener('scroll', scroll); window.removeEventListener('resize', scroll); window.removeEventListener('pagehide', savePosition); cancelAnimationFrame(frame); clearTimeout(saveTimer); };
  }, [doc, headings, shownBlocks]);

  useEffect(() => {
    const nav = drawerRef.current;
    const link = nav?.querySelector<HTMLElement>(`[href="#${activeId}"]`);
    if (nav && link && (link.offsetTop < nav.scrollTop + 50 || link.offsetTop > nav.scrollTop + nav.clientHeight - 60)) {
      nav.scrollTop = Math.max(0, link.offsetTop - nav.clientHeight / 2);
    }
  }, [activeId]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if(document.querySelector('dialog[open]'))return;
      if (event.ctrlKey && ['1', '2', '3'].includes(event.key)) {
        event.preventDefault(); setSettings(s => ({ ...s, view: ({ '1': 'en', '2': 'split', '3': 'ko' } as Record<string, View>)[event.key] }));
      }
      if (event.ctrlKey && event.key.toLowerCase() === 'f') { event.preventDefault(); setSearchOpen(true); }
      if (event.key === 'Escape') { setDrawer(false); setSearchOpen(false); menuRef.current?.focus(); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => { if (searchOpen) searchRef.current?.focus(); }, [searchOpen]);
  useEffect(() => {
    if (!drawer) return;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    drawerRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const list = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>('button, a, select, input') ?? []).filter(e => e.getClientRects().length);
      if (event.shiftKey && document.activeElement === list[0]) { event.preventDefault(); list.at(-1)?.focus(); }
      if (!event.shiftKey && document.activeElement === list.at(-1)) { event.preventDefault(); list[0]?.focus(); }
    };
    const resize = () => { if (window.innerWidth >= 1024) setDrawer(false); };
    window.addEventListener('keydown', trap); window.addEventListener('resize', resize);
    return () => { document.body.style.overflow = oldOverflow; window.removeEventListener('keydown', trap); window.removeEventListener('resize', resize); menuRef.current?.focus(); };
  }, [drawer]);

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
  return <div className={`reader ${ask.anchor?'with-ask':''}`} onClick={anchorClick}>
    <a className="skip-link" href="#paper">본문으로 이동</a>
    <header className="topbar"><div className="toolbar">
      <button ref={menuRef} className="menu-button" aria-label="목차 열기" aria-expanded={drawer} aria-controls="toc" onClick={() => setDrawer(true)}><Icon name="menu"/></button>
      {onLibrary&&!offline?<a className="brand brand-link" href="?library=1" title="서재로 이동" aria-label="PaperDuet · 서재로 이동" onClick={e=>{e.preventDefault();onLibrary();}}><BrandMark/><b>PaperDuet</b></a>:<div className="brand"><BrandMark/><b>PaperDuet</b></div>}
      <span className="brand-divider" aria-hidden="true"/><span className="paper-name" title={doc.title}>{doc.title}</span>
      <div className="view-controls" role="group" aria-label="보기 모드">{([['en', '원문'], ['split', '대역'], ['ko', '번역']] as const).map(([mode, name]) =>
        <button key={mode} aria-pressed={settings.view === mode} onClick={() => setSettings(s => ({ ...s, view: mode }))}>{name}</button>)}</div>
      <div className="tools"><button onClick={() => setSearchOpen(s => !s)} aria-label="문서 검색" title="문서 검색 (Ctrl+F)"><Icon name="search"/></button>
        <button aria-label="글자 작게" title="글자 작게" disabled={settings.font_size === 13} onClick={() => setSettings(s => ({ ...s, font_size: Math.max(13, s.font_size - 1) }))}><Icon name="textSmaller"/></button>
        <button aria-label="글자 크게" title="글자 크게" disabled={settings.font_size === 21} onClick={() => setSettings(s => ({ ...s, font_size: Math.min(21, s.font_size + 1) }))}><Icon name="textLarger"/></button>
        <button aria-label="테마 변경" title={settings.theme === 'dark' ? '현재: 다크 · 라이트로 바꾸기' : '현재: 라이트 · 다크로 바꾸기'} onClick={() => setSettings(s => ({ ...s, theme: s.theme === 'dark' ? 'light' : 'dark' }))}>
          <Icon name={settings.theme === 'dark' ? 'moon' : 'sun'}/></button>{onTutorial&&!offline&&<button aria-label="사용 가이드" title="사용 가이드" onClick={onTutorial}><Icon name="help"/></button>}</div>
    </div><div className="progress-track"><div className="progress-fill" role="progressbar" aria-label="읽기 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} style={{ width: `${progress}%` }} /></div>
    {searchOpen && <div className="search-panel" role="search"><label htmlFor="document-search">문서 검색</label><input id="document-search" ref={searchRef} value={query} placeholder="원문과 번역에서 찾기" onChange={e => { setQuery(e.target.value); setMatchIndex(0); }} onKeyDown={e => { if (e.key === 'Enter') findMatch(e.shiftKey ? matchIndex - 1 : matchIndex); }}/>
      <span role="status">{matches.length ? `${Math.min(matchIndex + 1, matches.length)} / ${matches.length}` : '0건'}</span><button onClick={() => findMatch(matchIndex - 1)} disabled={!matches.length} aria-label="이전 검색 결과"><Icon name="chevronUp"/></button><button onClick={() => findMatch(matchIndex + 1)} disabled={!matches.length} aria-label="다음 검색 결과"><Icon name="chevronDown"/></button><button onClick={() => setSearchOpen(false)} aria-label="검색 닫기"><Icon name="close"/></button></div>}
    </header>
    {saveError && <div className="save-warning" role="status">읽기 설정 저장을 기다리고 있습니다. 로컬 연결이 복구되면 다시 시도해 주세요.</div>}
    {drawer && <button className="scrim" aria-label="목차 닫기" tabIndex={-1} onClick={() => setDrawer(false)} />}
    <div className="shell"><nav id="toc" ref={drawerRef} className={`toc ${drawer ? 'open' : ''}`} aria-label="목차" onKeyDown={e => { if (e.key === 'Escape') setDrawer(false); }}>
      <div className="toc-heading"><span>Contents <b>목차</b></span><button className="drawer-close" aria-label="목차 닫기" onClick={() => setDrawer(false)}><Icon name="close"/></button></div>
      <div className="toc-links">{headings.map(h => <a key={h.id} href={`#${h.id}`} className={`depth-${h.type}`} aria-current={activeId === h.id ? 'location' : undefined}><span>{h.n === 'Abstract' ? '00' : h.n}</span><Inline text={h.ko||h.en} /></a>)}</div>
      <div className="note-options"><label className="note-toggle"><input type="checkbox" checked={settings.show_notes} onChange={e => setSettings(s => ({ ...s, show_notes: e.target.checked }))}/>맥락 주석 <span>{doc.blocks.filter(b=>b.note).length}</span></label>
        <div className="note-filters">{(Object.entries(NOTE_NAMES) as [NoteKind, string][]).map(([kind, name]) => <button key={kind} className={`filter-${kind}`} aria-pressed={settings.note_kinds.includes(kind)} onClick={() => setSettings(s => ({ ...s, note_kinds: s.note_kinds.includes(kind) ? s.note_kinds.filter(k => k !== kind) : [...s.note_kinds, kind] }))}><i />{name}</button>)}</div>
        <label className="density">주석 밀도<select value={settings.density} onChange={e => setSettings(s => ({ ...s, density: e.target.value as Settings['density'] }))}><option value="low">낮음 · 핵심</option><option value="normal">보통</option><option value="high">높음 · 전체</option></select></label>
      </div><div className="offline-indicator"><i/>오프라인 열람 가능</div>
    </nav>
    <main id="paper"><div className="paper-wrap"><section className="hero"><div className="eyebrow">Paper reader <span>/</span> {doc.status==='fixture'?'Computer vision':'My library'}</div><h1>{doc.title}</h1><h2>{doc.title_ko}</h2>
      <div className="hero-meta"><div className="metadata">{doc.arxiv_id&&<span>arXiv {doc.arxiv_id}</span>}<span>{doc.status==='fixture'?'Rex-Omni · 3B':`${doc.page_count}쪽`}</span><span>표 {doc.blocks.filter(b=>b.type==='tab').length}</span><span>그림 {doc.blocks.filter(b=>b.type==='fig').length}</span></div>
      {!offline&&<DocumentActions doc={doc}/>}</div>
      {offline&&<p className="export-notice">독립 HTML · 이 파일은 오프라인 열람용입니다. 내보내기·공유 시 원문의 이용 조건을 확인해 주세요.</p>}
      {!offline&&doc.status!=='fixture'&&<div className="document-status"><span>{doc.status==='complete'?'처리 완료':doc.status==='review'?`검수 필요 ${doc.blocks.filter(b=>b.qa_flags.length).length}건`:'추출 미리보기 · AI 리더는 아직 준비되지 않았습니다'}</span><button onClick={onProcess}>처리 상태·번역·주석</button><button onClick={()=>setPdfOpen(true)}>원본 PDF 보기</button>{doc.blocks.some(b=>b.qa_flags.length)&&<button onClick={()=>{setSettings(s=>({...s,show_notes:true,density:'high',note_kinds:['key','res','lim','ins','mth','trm']}));requestAnimationFrame(()=>jump(doc.blocks.find(b=>b.qa_flags.length)!.id));}}>검수할 블록 보기</button>}</div>}
      {guide&&<div className="reading-guide"><span className="guide-icon"><Icon name="columns"/></span><div><b>원문과 번역, 한 문단씩 나란히.</b><p>{doc.status==='fixture'?'본문 전체를 따라 읽고, 색으로 구분한 맥락 주석에서 핵심과 해석을 확인하세요.':'표와 수식을 원본 이미지와 함께 확인하세요. 참고문헌은 번역하지 않습니다.'}</p></div><span className="sample-label">{doc.status==='fixture'?'샘플 논문':'내 PDF'}</span>
        <button className="guide-close" aria-label="읽기 안내 닫기" title="다시 보지 않기" onClick={() => { setGuide(false); try { localStorage.setItem(GUIDE_KEY, '1'); } catch { /* storage unavailable */ } }}><Icon name="close"/></button></div>}
      {doc.status==='fixture'?<details className="source-note"><summary>이 리더의 수록 범위와 주석 근거</summary><p>제공된 샘플의 1–31쪽 범위를 수록했습니다. 그림은 캡션만, 표는 제공된 한국어 헤더·캡션을 표시합니다. 주석은 레퍼런스의 해설이며, 근거 후보는 인접 블록과 명시된 참조에서 연결했습니다. 원문 대조 검토가 필요합니다. 원 논문: Qing Jiang 외, “Detect Anything via Next Point Prediction”, arXiv:2510.12798, CC BY 4.0.</p></details>:!!doc.glossary.length&&<details className="source-note"><summary>이 논문의 용어집 · {doc.glossary.length}개</summary><dl>{doc.glossary.map(g=><div key={g.term}><dt><b>{g.term}</b> · {g.ko}</dt><dd>{g.definition_ko}</dd></div>)}</dl></details>}
    </section>
    {!offline&&<div className="reader-tabs" role="tablist" aria-label="보기">{([['paper','본문'],['summary','요약']] as const).map(([id,name])=>
      <button key={id} role="tab" aria-selected={tab===id} onClick={()=>switchTab(id)}>{name}</button>)}</div>}
    {tab==='summary'?<SummaryView doc={doc} labels={evidence} onJump={goToBlock} onAsk={anchor=>ask.open(anchor)}/>:<>
    <div className="column-labels" aria-hidden="true"><span className="en">English <b>원문</b></span><span className="ko">Korean <b>번역</b></span></div>
    <article aria-label="논문 본문">{shownBlocks.map(b => <div key={b.id} className={`reader-block-wrap ${doc.status!=='fixture'&&b.qa_flags.length?'qa-block':''}`}><ReaderBlock block={b} evidence={evidence} />{!offline&&<div className="block-ask-actions"><button aria-label={`${b.n||b.id} AI에게 질문`} onClick={()=>ask.open({block_id:b.id,field:'en',start:0,end:0,text:''})}><Icon name="message"/>질문</button>{ask.threads.some(t=>t.block_id===b.id)&&<button className="thread-badge" aria-label={`${b.id} 대화 보기`} onClick={()=>ask.open({block_id:b.id,field:'en',start:0,end:0,text:''},undefined,ask.threads.find(t=>t.block_id===b.id)!.id)}>◌ {ask.threads.filter(t=>t.block_id===b.id).length}</button>}</div>}{!offline&&doc.status!=='fixture'&&!b.section_path.some(p=>/^(references|bibliography)$/i.test(p))&&<BlockTools block={b} onUpdate={refreshDoc}/>}</div>)}</article>
    <footer className="paper-end"><span>End of reading</span><p>{doc.title}</p><small>{doc.status==='fixture'?'레퍼런스 수록 범위의 끝입니다.':'문서의 끝입니다.'} 참고문헌은 번역하지 않습니다.</small></footer></>}
    </div></main></div>
    {pdfOpen&&<PdfPreview docId={doc.id} pages={doc.page_count} onClose={()=>setPdfOpen(false)} onProcess={()=>onProcess?.()}/>}
    {ask.selected&&!ask.anchor&&<SelectionActions anchor={ask.selected} onOpen={ask.open}/>}
    {ask.anchor&&<AskPanel key={`${ask.anchor.block_id}-${ask.threadId??'new'}-${ask.anchor.field}-${ask.anchor.start}-${ask.anchor.end}`} doc={doc} anchor={ask.anchor} threadId={ask.threadId} preset={ask.preset} onClose={ask.close} onSaved={refreshDoc} onThreads={ask.refresh}/>}
    <div className="reading-status"><span>{Math.round(progress)}%</span><button aria-label="현재 위치 링크 복사" title="현재 위치 링크 복사" onClick={async () => { try { await navigator.clipboard.writeText(`#${latestPosition.current.block_id ?? 'b0000'}`); setCopy('복사됨'); } catch { setCopy('복사 실패'); } }}>{copy || <Icon name="link"/>}</button><button aria-label="맨 위로" onClick={() => { history.replaceState(null, '', location.pathname); window.scrollTo({ top: 0, behavior: 'instant' }); }}><Icon name="arrowUp"/></button></div>
  </div>;
}

const QA_NAMES:Record<string,string>={V1:'번역 누락',V2:'번역 길이 부족',V3:'번역 숫자 누락',V4:'표 구조·수치 불일치',V5:'주석 숫자·근거 확인',V6:'용어 번역 불일치',NOTE_TERM:'용어의 첫 등장 위치·설명 확인',NOTE_MATH:'수식 설명 3요소 확인',ANNOTATE_REVIEW:'주석 생성 재시도 필요',EXTRACT_REVIEW:'원본 추출 확인'};
function BlockTools({block,onUpdate}:{block:Block;onUpdate:()=>Promise<void>}) {
  const [value,setValue]=useState(block.ko??block.caption_ko??'');const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const caption=!!block.caption_en;
  const run=async(regenerate:boolean)=>{setBusy(true);setError('');try{const result=await request<Block>(`/documents/${block.doc_id}/blocks/${block.id}${regenerate?'/regenerate':''}`,{method:regenerate?'POST':'PATCH',body:regenerate?undefined:JSON.stringify({[caption?'caption_ko':'ko']:value})});await onUpdate();setValue(result.ko??result.caption_ko??'');}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  if(block.note)return <NoteTools block={block} onUpdate={onUpdate}/>;
  if(block.type==='eq')return <div className="block-editor">{block.qa_flags.length>0&&<span className="qa-label">검수 필요 · {block.qa_flags.join(', ')} </span>}<button disabled={busy} onClick={()=>void run(true)}>이 수식 복원 재시도</button>{error&&<p role="alert">{error}</p>}</div>;
  if(!caption&&!['sec','sub','ssub','p','li'].includes(block.type))return null;
  return <details className="block-editor"><summary>{block.qa_flags.length?`검수 필요 · ${block.qa_flags.map(f=>QA_NAMES[f]||f).join(', ')}`:'번역 수정'}</summary><label>한국어 {caption?'캡션':'번역'}<textarea value={value} onChange={e=>setValue(e.target.value)} rows={4}/></label><button disabled={busy} onClick={()=>void run(false)}>수정 저장</button> <button disabled={busy} onClick={()=>void run(true)}>{block.qa_flags.includes('ANNOTATE_REVIEW')?'이 섹션 주석 재시도':block.type==='tab'&&block.image_path&&block.qa_flags.includes('V4')?'원본 이미지로 표 다시 복원':'이 블록 재번역'}</button>{busy&&<span role="status"> 처리 중…</span>}{error&&<p role="alert">{error}</p>}</details>;
}

function NoteTools({block,onUpdate}:{block:Block;onUpdate:()=>Promise<void>}) {
  const [note,setNote]=useState(block.note!);const [refs,setRefs]=useState(note.refs.join(', '));const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const run=async(regenerate:boolean)=>{setBusy(true);setError('');try{const result=await request<Block>(`/documents/${block.doc_id}/blocks/${block.id}${regenerate?'/regenerate':''}`,{method:regenerate?'POST':'PATCH',body:regenerate?undefined:JSON.stringify({note:{...note,refs:[...new Set(refs.split(/[\s,]+/).filter(Boolean))]}})});setNote(result.note!);setRefs(result.note!.refs.join(', '));await onUpdate();}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <details className="block-editor note-editor"><summary>{block.qa_flags.length?`검수 필요 · ${block.qa_flags.map(f=>QA_NAMES[f]||f).join(', ')}`:'주석 수정·재생성'}</summary><label>주석 제목<input value={note.title} maxLength={300} onChange={e=>setNote({...note,title:e.target.value})}/></label><label>주석 내용<textarea value={note.body_md} rows={6} maxLength={12000} onChange={e=>setNote({...note,body_md:e.target.value})}/></label><label>주장 유형<select value={note.claim} onChange={e=>setNote({...note,claim:e.target.value as typeof note.claim})}><option value="stated">논문 명시</option><option value="interpretation">해석</option><option value="mixed">혼합</option></select></label><label>근거 블록 ID (쉼표로 구분)<input value={refs} onChange={e=>setRefs(e.target.value)}/></label><p className="note-validation-help">숫자는 근거 블록에 있어야 합니다. 계산한 값은 별도 문단에 “계산값:”으로 시작해 식과 가정을 적어 주세요. 자동 검사는 주장의 사실 여부를 보증하지 않습니다.</p><button disabled={busy||!note.title.trim()||!note.body_md.trim()||!refs.trim()} onClick={()=>void run(false)}>주석 수정 저장</button> <button disabled={busy} onClick={()=>void run(true)}>이 주석 재생성</button>{busy&&<span role="status"> 주석 처리 중…</span>}{error&&<p role="alert">{error}</p>}</details>;
}
