import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import type { Block, Position } from './types';

export type ReadingHandle = { jump: (id: string, offset?: number) => void; first: () => void };
export type ProgressHandle = { update: (percent:number) => void };
export const ReadingProgress=forwardRef<ProgressHandle>(function ReadingProgress(_,ref){
  const fill=useRef<HTMLDivElement>(null);
  useImperativeHandle(ref,()=>({update(percent){if(fill.current){fill.current.style.width=`${percent}%`;fill.current.setAttribute('aria-valuenow',String(Math.round(percent)));}}}),[]);
  return <div className="progress-track"><div ref={fill} className="progress-fill" role="progressbar" aria-label="읽기 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={0} style={{width:'0%'}}/></div>;
});

// The paginated reader stored character anchors as negative offsets. Keep those
// positions readable while using the original vertical pixel offsets again.
function characterRect(element: Element, offset: number) {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const length = node.textContent?.length || 0;
    if (offset < length) {
      const range = document.createRange(); range.setStart(node, offset); range.setEnd(node, offset + 1);
      return range.getClientRects()[0];
    }
    offset -= length;
  }
}
const readingTop = () => (document.querySelector('.topbar')?.getBoundingClientRect().height || 64) + 24;

export const ReadingContent = forwardRef<ReadingHandle, {
  children: ReactNode; blocks: Block[]; position: Position; layoutKey: string;
  onPosition: (position: Position, percent: number) => void;
}>(function ReadingContent({ children, blocks, position, layoutKey, onPosition }, ref) {
  const content = useRef<HTMLElement>(null), anchor = useRef(position), anchorOrder = useRef(0);
  const elements=useRef<HTMLElement[]>([]),orders=useMemo(()=>new Map(blocks.map(b=>[b.id,b.order])),[blocks]);
  const callback = useRef(onPosition); callback.current = onPosition;
  const report = useCallback(() => {
    if (!content.current) return;
    const top = readingTop();let low=0,high=elements.current.length-1,current:HTMLElement|undefined,currentTop=0;
    while(low<=high){const middle=(low+high)>>>1,element=elements.current[middle],y=element.getBoundingClientRect().top;if(y<=top+1){current=element;currentTop=y;low=middle+1;}else high=middle-1;}
    const next = current ? { block_id: current.id, offset: Math.max(0, top-currentTop) } : { block_id: null, offset: 0 };
    anchor.current = next;
    const order=current?orders.get(current.id):undefined;if(order!==undefined)anchorOrder.current=order;
    const max = document.documentElement.scrollHeight - innerHeight;
    callback.current(next, max > 0 ? Math.min(100, Math.max(0, scrollY / max * 100)) : 0);
  }, [orders]);
  const jump = useCallback((id: string, offset = 0) => {
    const element = document.getElementById(id); if (!element || !content.current?.contains(element)) return;
    const box = element.getBoundingClientRect();
    const point = offset < 0 ? characterRect(element, -offset - 1) : undefined;
    const inside = offset < 0 ? Math.max(0, (point?.top ?? box.top) - box.top) : Math.min(offset, Math.max(0, box.height - 24));
    window.scrollTo({ top: box.top + scrollY - readingTop() + inside, behavior: 'instant' });
    report();
  }, [report]);
  useImperativeHandle(ref, () => ({ jump, first: () => { window.scrollTo({ top: 0, behavior: 'instant' }); report(); } }), [jump, report]);
  useEffect(() => {
    let frame = 0;
    const scroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; report(); }); };
    window.addEventListener('scroll', scroll, { passive: true });
    return () => { window.removeEventListener('scroll', scroll); cancelAnimationFrame(frame); };
  }, [report]);
  useLayoutEffect(() => {
    const article = content.current; if (!article) return;
    elements.current=Array.from(article.querySelectorAll<HTMLElement>('[data-block]'));
    let frame = 0, stopped = false;
    const layout = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => {
      if (stopped) return;
      const saved = anchor.current;
      if (saved.block_id) {
        const target = blocks.find(block => block.id === saved.block_id) || blocks.find(block => block.order >= anchorOrder.current) || blocks.at(-1);
        if (target) jump(target.id, target.id === saved.block_id ? saved.offset : 0); else report();
      } else report();
    }); };
    const observer = new ResizeObserver(layout); observer.observe(article);
    article.addEventListener('load', layout, true); void document.fonts.ready.then(layout); layout();
    return () => { stopped = true; elements.current=[];cancelAnimationFrame(frame); observer.disconnect(); article.removeEventListener('load', layout, true); };
  }, [blocks, layoutKey, jump, report]);
  return <section className="book-stage" aria-label="논문 읽기"><div className="book-sheet">
    <div className="book-viewport"><article ref={content} className="reading-flow" aria-label="논문 본문">{children}</article></div>
  </div></section>;
});
