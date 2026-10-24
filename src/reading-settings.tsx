import { useEffect, useRef, useState } from 'react';
import { Icon } from './icons';
import type { Settings } from './types';

export function ReadingSettings({ value, onChange, onGuide }: { value: Settings; onChange: (value: Settings) => void; onGuide?: () => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>('[role=dialog] button:not(:disabled)')?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  return <div className="reading-settings" ref={root} onKeyDown={event => {
    if (open && event.key === 'Escape') { event.stopPropagation(); setOpen(false); trigger.current?.focus({ preventScroll: true }); }
  }}>
    <button ref={trigger} className="icon-button" aria-label="읽기 설정" title="읽기 설정" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(value => !value)}><Icon name="type"/></button>
    {open && <div className="reading-settings-panel" role="dialog" aria-label="읽기 설정">
      <header><b>읽기 설정</b><button className="icon-button" aria-label="읽기 설정 닫기" onClick={() => { setOpen(false); trigger.current?.focus({ preventScroll: true }); }}><Icon name="close"/></button></header>
      <div className="reading-setting-row"><span>글자 크기</span><div className="font-size-controls">
        <button className="icon-button" aria-label="글자 작게" disabled={value.font_size <= 13} onClick={() => onChange({ ...value, font_size: Math.max(13, value.font_size - 1) })}><Icon name="minus"/></button>
        <output aria-label="글자 크기">{value.font_size}px</output>
        <button className="icon-button" aria-label="글자 크게" disabled={value.font_size >= 21} onClick={() => onChange({ ...value, font_size: Math.min(21, value.font_size + 1) })}><Icon name="plus"/></button>
      </div></div>
      <div className="reading-setting-row"><span>테마</span><div className="theme-choices" role="group" aria-label="테마">
        <button aria-pressed={value.theme === 'light'} onClick={() => onChange({ ...value, theme: 'light' })}><Icon name="sun"/>라이트</button>
        <button aria-pressed={value.theme === 'dark'} onClick={() => onChange({ ...value, theme: 'dark' })}><Icon name="moon"/>다크</button>
      </div></div>
      {onGuide && <button className="reading-settings-guide" onClick={() => { setOpen(false); onGuide(); }}><Icon name="help"/>사용 가이드</button>}
    </div>}
  </div>;
}
