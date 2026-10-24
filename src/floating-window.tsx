import { useCallback, useEffect, useRef, useState, type ReactNode, type PointerEvent } from 'react';
import { Icon } from './icons';

type Frame = { x: number; y: number; width: number; height: number };
type ResizeEdge = 'n' | 'e' | 's' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
const edges: { edge: ResizeEdge; label: string }[] = [
  { edge: 'n', label: '위쪽' }, { edge: 'e', label: '오른쪽' }, { edge: 's', label: '아래쪽' }, { edge: 'w', label: '왼쪽' },
  { edge: 'ne', label: '오른쪽 위 모서리' }, { edge: 'nw', label: '왼쪽 위 모서리' }, { edge: 'se', label: '오른쪽 아래 모서리' }, { edge: 'sw', label: '왼쪽 아래 모서리' },
];
let front = 100;
const fit = (frame: Frame): Frame => {
  const width = Math.min(Math.max(320, frame.width), innerWidth - 16);
  const height = Math.min(Math.max(280, frame.height), innerHeight - 16);
  return { width, height, x: Math.max(8, Math.min(frame.x, innerWidth - width - 8)), y: Math.max(8, Math.min(frame.y, innerHeight - height - 8)) };
};
const adjust = (frame: Frame, edge: ResizeEdge | 'move', dx: number, dy: number): Frame => {
  if (edge === 'move') return fit({ ...frame, x: frame.x + dx, y: frame.y + dy });
  const minimumWidth = Math.min(320, innerWidth - 16), minimumHeight = Math.min(280, innerHeight - 16);
  const right = frame.x + frame.width, bottom = frame.y + frame.height;
  let { x, y, width, height } = frame;
  if (edge.includes('w')) { x = Math.max(8, Math.min(frame.x + dx, right - minimumWidth)); width = right - x; }
  if (edge.includes('e')) width = Math.max(minimumWidth, Math.min(frame.width + dx, innerWidth - frame.x - 8));
  if (edge.includes('n')) { y = Math.max(8, Math.min(frame.y + dy, bottom - minimumHeight)); height = bottom - y; }
  if (edge.includes('s')) height = Math.max(minimumHeight, Math.min(frame.height + dy, innerHeight - frame.y - 8));
  return { x, y, width, height };
};

/** Modeless dialogs keep the paper available and allow both tools to stay open. */
export function FloatingWindow({ id, title, subtitle, children, onClose, className = '', open = true, activation = 0 }: {
  id: string; title: string; subtitle?: string; children: ReactNode; onClose: () => void; className?: string; open?: boolean; activation?: number;
}) {
  const [frame, setFrame] = useState<Frame>(() => {
    try { const saved = JSON.parse(localStorage.getItem(`paperduet-window-${id}`) || 'null');
      if (saved && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(saved[key]))) return fit(saved);
    } catch { /* The tool also works without browser storage. */ }
    return fit({ width: id === 'ask' ? 540 : 520, height: 650, x: innerWidth / 2 - (id === 'ask' ? 290 : 190), y: Math.max(24, innerHeight / 2 - 325) });
  });
  const [layer, setLayer] = useState(() => ++front);
  const ref = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ pointerId: number; startX: number; startY: number; frame: Frame; edge: ResizeEdge | 'move' } | null>(null);
  useEffect(() => { const resize = () => { gesture.current = null; setFrame(value => fit(value)); }; window.addEventListener('resize', resize); return () => window.removeEventListener('resize', resize); }, []);
  const latestFrame=useRef(frame);latestFrame.current=frame;const savedFrame=useRef('');
  const persist=useCallback(()=>{const json=JSON.stringify(latestFrame.current);if(json===savedFrame.current)return;try{localStorage.setItem(`paperduet-window-${id}`,json);savedFrame.current=json;}catch{/* optional preference */}},[id]);
  useEffect(()=>{const timer=setTimeout(persist,200);return()=>clearTimeout(timer);},[frame,persist]);
  useEffect(()=>persist,[persist]);
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    ref.current?.focus({ preventScroll: true });
    return () => { if (opener?.isConnected) opener.focus({ preventScroll: true }); };
  }, [open, activation]);
  const start = (event: PointerEvent, edge: ResizeEdge | 'move') => {
    if (event.button !== 0 || gesture.current) return;
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, frame, edge };
  };
  const move = (event: PointerEvent) => {
    const value = gesture.current; if (!value || value.pointerId !== event.pointerId) return;
    const dx = event.clientX - value.startX, dy = event.clientY - value.startY;
    const next=adjust(value.frame,value.edge,dx,dy);latestFrame.current=next;setFrame(next);
  };
  const end = (event: PointerEvent) => {
    if (gesture.current?.pointerId !== event.pointerId) return;
    gesture.current = null;
    persist();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const keyboard = (event: React.KeyboardEvent, edge: ResizeEdge | 'move') => {
    const delta = ({ ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10] } as Record<string, number[]>)[event.key];
    if (!delta) return; event.preventDefault(); event.stopPropagation();
    setFrame(value => adjust(value, edge, delta[0], delta[1]));
  };
  return <div ref={ref} role="dialog" aria-label={title} aria-modal="false" tabIndex={-1} hidden={!open}
    className={`floating-window ${className}`} style={{ left: frame.x, top: frame.y, width: frame.width, height: frame.height, zIndex: layer }}
    onPointerDownCapture={() => setLayer(++front)} onFocusCapture={() => setLayer(++front)}
    onKeyDown={event => { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) { event.stopPropagation(); onClose(); } }}>
    <header className="floating-window-header">
      <div className="floating-window-grip" role="button" tabIndex={0} aria-label={`${title} 창 이동`} title="드래그하여 이동 · 방향키로 이동"
        onPointerDown={event => start(event, 'move')} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onKeyDown={event => keyboard(event, 'move')}>
        <h2>{title}</h2>{subtitle && <span>{subtitle}</span>}
      </div>
      <button className="icon-button" aria-label={title === 'Ask AI' ? 'AI 질문 닫기' : `${title} 닫기`} onClick={onClose}><Icon name="close"/></button>
    </header>
    <div className="floating-window-content">{children}</div>
    {edges.map(({ edge, label }) => <button key={edge} type="button" className={`icon-button window-resize-handle resize-${edge}`} data-resize={edge}
      aria-label={edge === 'se' ? `${title} 창 크기 조절` : `${title} 창 ${label} 크기 조절`} title={`${label}에서 드래그하거나 방향키로 크기 조절`}
      tabIndex={edge === 'se' ? 0 : -1} onPointerDown={event => start(event, edge)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onKeyDown={event => keyboard(event, edge)}>
      {edge === 'se' && <Icon name="resize"/>}
    </button>)}
  </div>;
}
