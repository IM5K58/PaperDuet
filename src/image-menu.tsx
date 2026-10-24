import { useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { Icon } from './icons';

/** A top-layer menu also stays usable inside the modal image viewer. */
export function useImageMenu(onAsk?: () => void) {
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  const menu = useRef<HTMLDivElement>(null), opener = useRef<HTMLElement | null>(null);
  const dismiss = (restore = false) => { setPoint(null); if (restore) opener.current?.focus({ preventScroll: true }); };
  useLayoutEffect(() => {
    const popup = menu.current;
    if (!point || !popup) return;
    popup.showPopover();
    popup.style.left = `${Math.max(8, Math.min(point.x, document.documentElement.clientWidth - popup.offsetWidth - 8))}px`;
    popup.style.top = `${Math.max(8, Math.min(point.y, innerHeight - popup.offsetHeight - 8))}px`;
    popup.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true });
    const close = () => setPoint(null);
    document.addEventListener('scroll', close, true); window.addEventListener('resize', close); window.addEventListener('blur', close);
    return () => {
      document.removeEventListener('scroll', close, true); window.removeEventListener('resize', close); window.removeEventListener('blur', close);
      if (popup.matches(':popover-open')) popup.hidePopover();
    };
  }, [point]);
  const open = (event: MouseEvent<HTMLElement>) => {
    if (!onAsk) return;
    event.preventDefault(); event.stopPropagation(); opener.current = event.currentTarget;
    const box = event.currentTarget.getBoundingClientRect();
    setPoint({ x: event.clientX || box.left + 12, y: event.clientY || box.top + 12 });
  };
  const key = (event: KeyboardEvent<HTMLElement>) => {
    if (!onAsk || !(event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return;
    event.preventDefault(); event.stopPropagation(); opener.current = event.currentTarget;
    const box = event.currentTarget.getBoundingClientRect(); setPoint({ x: box.left + 12, y: box.top + 12 });
  };
  const element = point && <div ref={menu} popover="auto" role="menu" aria-label="그림 메뉴" className="image-ask-menu"
    onToggle={event => { if (!event.currentTarget.matches(':popover-open')) setPoint(null); }}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismiss(true); }
      else if (event.key === 'Tab') dismiss();
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('button')?.focus(); }
    }}>
    <button type="button" role="menuitem" onClick={event => { event.stopPropagation(); dismiss(); onAsk?.(); }}><Icon name="message"/>Ask AI</button>
  </div>;
  return { onContextMenu: open, onKeyDown: key, element };
}
