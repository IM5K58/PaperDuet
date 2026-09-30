import { memo, useEffect, useMemo, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { AnswerMarkdown } from './markdown';
import katex from 'katex';
import type { Block, Cell, NoteKind } from './types';
import { request } from './api';
import { Icon } from './icons';

function Crop({block}:{block:Block}) {
  const [url,setUrl]=useState('');const [error,setError]=useState(false);const ref=useRef<HTMLDivElement>(null);
  const [expanded,setExpanded]=useState(false);
  useEffect(()=>{let stopped=false;const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){observer.disconnect();void request<{data_url:string}>(`/documents/${block.doc_id}/blocks/${block.id}/image`).then(data=>{if(!stopped)setUrl(data.data_url);}).catch(()=>{if(!stopped)setError(true);});}},{rootMargin:'400px'});if(ref.current)observer.observe(ref.current);return()=>{stopped=true;observer.disconnect();};},[block.doc_id,block.id]);
  return <div ref={ref} className="pdf-crop">{url?<><button className="crop-open" onClick={()=>setExpanded(true)} aria-label={`${block.n??'PDF'} 원본 확대`}><img src={url} alt={`${block.n??'PDF'} 원본 영역`}/><span><Icon name="expand"/>확대 보기</span></button>{expanded&&<ImageViewer url={url} label={block.n??'PDF'} onClose={()=>setExpanded(false)}/>}</>:<p>{error?'원본 이미지를 불러오지 못했습니다.':'원본 이미지'}</p>}</div>;
}

function ImageViewer({url,label,onClose}:{url:string;label:string;onClose:()=>void}) {
  const ref=useRef<HTMLDialogElement>(null);const [zoom,setZoom]=useState(1);
  const viewport=useRef<HTMLDivElement>(null);const picture=useRef<HTMLImageElement>(null);const [fitWidth,setFitWidth]=useState<number>();
  const fit=()=>{const v=viewport.current;const p=picture.current;if(v&&p?.naturalWidth)setFitWidth(Math.min(v.clientWidth,v.clientHeight*p.naturalWidth/p.naturalHeight));};
  useEffect(()=>{const dialog=ref.current;const opener=document.activeElement as HTMLElement|null;const overflow=document.body.style.overflow;dialog?.showModal();document.body.style.overflow='hidden';return()=>{dialog?.close();document.body.style.overflow=overflow;opener?.focus({preventScroll:true});};},[]);
  useEffect(()=>{const observer=new ResizeObserver(fit);if(viewport.current)observer.observe(viewport.current);return()=>observer.disconnect();},[]);
  return <dialog ref={ref} className="image-viewer" aria-label={`${label} 확대 보기`} onCancel={e=>{e.preventDefault();onClose();}} onKeyDown={e=>{e.stopPropagation();if(e.key==='+')setZoom(z=>Math.min(4,z+.25));if(e.key==='-')setZoom(z=>Math.max(.5,z-.25));if(e.key==='0')setZoom(1);}}>
    <header><b>{label} · 원본 이미지</b><div className="zoom-controls"><button className="icon-button" aria-label="이미지 축소" disabled={zoom<=.5} onClick={()=>setZoom(z=>z-.25)}><Icon name="minus"/></button><output aria-label="확대 비율">{Math.round(zoom*100)}%</output><button className="icon-button" aria-label="이미지 확대" disabled={zoom>=4} onClick={()=>setZoom(z=>z+.25)}><Icon name="plus"/></button><button onClick={()=>setZoom(1)}>화면 맞춤</button><button className="icon-button" aria-label="확대 보기 닫기" onClick={onClose}><Icon name="close"/></button></div></header>
    <div ref={viewport} className="image-viewport" tabIndex={0} role="region" aria-label="확대 이미지 스크롤"><img ref={picture} onLoad={fit} src={url} alt={`${label} 원본 영역 확대`} style={{width:fitWidth?`${fitWidth*zoom}px`:'100%'}}/></div><footer>+/− 확대·축소 · 0 화면 맞춤 · 방향키 스크롤 · Esc 닫기</footer>
  </dialog>;
}

export const NOTE_NAMES: Record<NoteKind, string> = { key: '핵심', res: '결과', lim: '한계', ins: '해석', mth: '수식', trm: '용어' };
export function Inline({ text = '', className = '', field }: { text?: string; className?: string;field?:string }) {
  const html = useMemo(() => DOMPurify.sanitize(text, { ALLOWED_TAGS: ['b', 'i', 'sub', 'sup', 'code'], ALLOWED_ATTR: [] }).replace(/\\\(([\s\S]+?)\\\)/g,(_match,latex:string)=>{const el=document.createElement('span');el.innerHTML=latex;const value=el.textContent||'';el.setAttribute('data-source-text',`\\(${value}\\)`);el.innerHTML=katex.renderToString(value,{displayMode:false,throwOnError:false,trust:false,strict:'ignore',maxExpand:1000,maxSize:20});return el.outerHTML;}), [text]);
  return <span className={className} data-ask-field={field} dangerouslySetInnerHTML={{ __html: html }} />;
}

function Equation({ latex }: { latex: string }) {
  const [copied, setCopied] = useState(false);
  const [copyError,setCopyError]=useState(false);
  const html = useMemo(() => {try{return katex.renderToString(latex, { displayMode: true, throwOnError: true, trust: false, strict:'error', maxExpand:1000, maxSize:20, output: 'htmlAndMathml' });}catch{return null;}}, [latex]);
  return <><button className="copy-equation" onClick={async () => {
    try { await navigator.clipboard.writeText(latex); setCopied(true);setCopyError(false); } catch { setCopied(false);setCopyError(true); }
  }} aria-label="LaTeX 복사">{copied ? '복사됨' : 'LaTeX 복사'}</button>
    {html?<div className="equation-scroll" tabIndex={0} role="region" aria-label="수식" dangerouslySetInnerHTML={{ __html: html }} />:<div className="equation-error" role="status">수식 문법 검수 필요 · 원본 이미지와 비교하고 복원을 재시도하세요.<pre>{latex}</pre></div>}{copyError&&<p role="status">복사하지 못했습니다. 아래 LaTeX 원문을 선택해 복사하세요.</p>}<details className="latex-source"><summary>LaTeX 원문</summary><pre>{latex}</pre></details></>;
}

function NoteCard({ block: b, evidence, anchor }: { block: Block; evidence?: Map<string,string>; anchor: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const note = b.note!;
  return <aside id={b.id} data-block data-note-kind={note.kind} className={`note n-${note.kind}${collapsed ? ' collapsed' : ''}`}>
    <div className="note-heading"><span className="note-kind">{NOTE_NAMES[note.kind]}</span><strong>{note.title}</strong>
      <span className="claim">{{ stated: '논문 명시', interpretation: '해석', mixed: '혼합' }[note.claim]}</span>{note.origin==='ai_answer'&&<span className="ai-answer-badge">AI 답변</span>}
      <button className="note-collapse" aria-expanded={!collapsed} aria-label={`${note.title} ${collapsed ? '펼치기' : '접기'}`} onClick={() => setCollapsed(c => !c)}>{collapsed ? '펼치기' : '접기'}</button>{anchor}</div>
    {!collapsed&&<><AnswerMarkdown text={note.body_md}/>
    <footer className="note-refs"><span>{b.doc_id==='rex-omni'?'근거 후보':'근거'}</span>{note.refs.map(ref => evidence?.has(ref)?<a key={ref} href={`#${ref}`} title={ref}>{evidence.get(ref)}</a>:<span key={ref}>없는 블록 {ref}</span>)}<span className="review-label">{note.origin==='user'?'직접 편집 · ':''}{b.qa_flags.length?'근거·형식 검토 필요':'숫자 추적성 확인'}</span></footer></>}
  </aside>;
}

export const ReaderBlock = memo(function ReaderBlock({ block: b, evidence }: { block: Block; evidence?:Map<string,string> }) {
  const anchor = <a className="block-anchor" href={`#${b.id}`} aria-label={`${b.id} 고유 링크`}>#</a>;
  if (b.type === 'sec' || b.type === 'sub' || b.type === 'ssub') {
    const Heading = b.type === 'sec' ? 'h2' : b.type === 'sub' ? 'h3' : 'h4';
    return <Heading id={b.id} data-block className={`heading ${b.type}`}>
      <span className="section-number">{b.type === 'sec' && b.n !== 'Abstract' ? 'Section ' : ''}{b.n}</span>
      <Inline text={b.en} field="en" className="heading-en" />
      <Inline text={b.ko||b.en} field={b.ko?'ko':'en'} className="heading-ko" />{anchor}
    </Heading>;
  }
  if (b.note) return <NoteCard block={b} evidence={evidence} anchor={anchor}/>;
  if (b.table) {
    const renderCells = (cells: Cell[], ri: number, header: boolean) => cells.map((cell, ci) => {
      const Tag = cell.is_header ? 'th' : 'td';
      return <Tag key={ci} colSpan={cell.colspan} rowSpan={cell.rowspan} scope={header ? 'col' : cell.is_header ? 'rowgroup' : undefined}
        className={`${cell.numeric ? 'numeric' : 'text-cell'} ${!header && b.table!.best_cells.some(([r, c]) => r === ri && c === ci) ? 'best' : ''}`}>
        <span className={cell.text_ko&&cell.text_ko!==cell.text_en?'cell-en':''}><Inline text={cell.text_en} /></span>{cell.text_ko&&cell.text_ko!==cell.text_en&&<span className="cell-ko"><Inline text={cell.text_ko}/></span>}
      </Tag>;
    });
    return <figure id={b.id} data-block className="table-block">
      <figcaption><b>{b.n}</b><span className={b.caption_en&&b.caption_ko&&b.caption_en!==b.caption_ko?'caption-en':''}><Inline text={b.caption_en||b.caption_ko}/></span>{b.caption_en&&b.caption_ko&&b.caption_en!==b.caption_ko&&<span className="caption-ko"><Inline text={b.caption_ko}/></span>}{anchor}</figcaption>
      <div className="table-scroll" role="region" aria-label={`${b.n} 가로 스크롤`} tabIndex={0}>
        <table><thead>{b.table.header.map((r, i) => <tr key={i}>{renderCells(r, i, true)}</tr>)}</thead>
          <tbody>{b.table.body.map((r, i) => <tr key={i} className={b.table!.highlight_rows.includes(i) ? 'highlight' : r.every(c => c.is_header) ? 'group-row' : ''}>{renderCells(r, i, false)}</tr>)}</tbody>
        </table>
      </div>
      {!!(b.table.highlight_rows.length||b.table.best_cells.length)&&<p className="table-legend">{b.table.highlight_rows.length>0&&<span><i/> 제안 모델</span>}{b.table.best_cells.length>0&&<span><strong>굵은 수치</strong> 열별 최댓값 (동률 포함)</span>}</p>}
      {b.image_path&&<details className="table-original"><summary>원본 표 이미지 확인</summary><Crop block={b}/></details>}
    </figure>;
  }
  if (b.type === 'fig') return <figure id={b.id} data-block className="figure-block">
    {b.image_path&&<Crop block={b}/>}<figcaption><b>{b.n}</b>{anchor}<div className="en"><Inline text={b.caption_en} field="caption_en" /></div><div className="ko"><Inline text={b.caption_ko||b.caption_en} field={b.caption_ko?'caption_ko':'caption_en'} /></div></figcaption>
  </figure>;
  if (b.type === 'eq') return <section id={b.id} data-block className="card equation-card"><header>식 {b.n}{anchor}</header>{b.latex?<><Equation latex={b.latex}/>{b.image_path&&<details className="equation-original"><summary>원본 수식 이미지 확인</summary><Crop block={b}/></details>}</>:<>{b.image_path&&<Crop block={b}/>}<p className="translation-pending">AI 연결 후 수식을 복원합니다.</p></>}</section>;
  if(b.type==='tab')return <figure id={b.id} data-block className="table-block"><figcaption><b>{b.n}</b> <Inline text={b.caption_ko||b.caption_en}/>{anchor}</figcaption>{b.image_path&&<Crop block={b}/>}<pre className="plain-table"><Inline text={b.en}/></pre><p className="translation-pending">표 구조 복원 대기 · 추출한 원문을 표시합니다.</p></figure>;
  if (b.card) return <section id={b.id} data-block className="card"><header><Inline text={b.ko} />{anchor}</header><pre>{b.card.body}</pre><div className="card-explanation"><Inline text={b.card.explain_ko} /></div></section>;
  return <div id={b.id} data-block className={`parallel ${b.type}`}>
    {anchor}<div className="en" lang="en"><Inline text={b.en} field="en" /></div><div className="ko" lang={b.ko?'ko':'en'}>{!b.ko&&<small className="translation-pending">{b.section_path.some(p=>/^(references|bibliography)$/i.test(p))?'참고문헌 · 원문 유지':'번역 대기 · 원문'}<br/></small>}<Inline text={b.ko||b.en} field={b.ko?'ko':'en'} /></div>
  </div>;
});
