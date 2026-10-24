import brandIcon from '../src-tauri/icons/128x128.png?inline';

// Line icons (24px grid, 1.6px stroke). Inline SVG so the offline export needs no assets.
const PATHS = {
  search: ['M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0z', 'm20 20-3.5-3.5'],
  book: ['M4 5.5A1.5 1.5 0 0 1 5.5 4H11v15H5.5A1.5 1.5 0 0 0 4 20.5z', 'M20 5.5A1.5 1.5 0 0 0 18.5 4H13v15h5.5a1.5 1.5 0 0 1 1.5 1.5z'],
  gear: ['M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z', 'M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z'],
  more: ['M6 12h.01', 'M12 12h.01', 'M18 12h.01'],
  activity: ['M4 12h4l2-5 4 10 2-5h4'],
  notebook: ['M6 3h13a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6V3z', 'M3 7h5', 'M3 12h5', 'M3 17h5', 'M11 8h5', 'M11 12h5'],
  resize: ['m10 20 10-10', 'm15 20 5-5'],
  sort: ['M7 5v14', 'm4 16 3 3 3-3', 'M17 19V5', 'm14 8 3-3 3 3'],
  textSmaller: ['M3 18 8 6l5 12', 'M4.7 14h6.6', 'M15.5 12h5.5'],
  type: ['M3 18 7.5 6 12 18', 'M4.5 14h6', 'm15 18 3-8 3 8', 'M16 15h4'],
  textLarger: ['M3 18 8 6l5 12', 'M4.7 14h6.6', 'M15.5 12h5.5', 'M18.25 9.25v5.5'],
  sun: ['M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0z', 'M12 2.5v2', 'M12 19.5v2', 'm5.3 5.3 1.4 1.4', 'm17.3 17.3 1.4 1.4', 'M2.5 12h2', 'M19.5 12h2', 'm5.3 18.7 1.4-1.4', 'm17.3 6.7 1.4-1.4'],
  moon: ['M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z'],
  system: ['M5 4h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z', 'M8 20h8', 'M12 16v4'],
  help: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z', 'M9.6 9.3a2.5 2.5 0 1 1 3.6 2.4c-.7.4-1.2.9-1.2 1.7v.4', 'M12 17h.01'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  close: ['m6 6 12 12', 'M18 6 6 18'],
  arrowUp: ['M12 19V5', 'm6 11 6-6 6 6'],
  chevronUp: ['m6 15 6-6 6 6'],
  chevronDown: ['m6 9 6 6 6-6'],
  chevronLeft: ['m15 6-6 6 6 6'],
  chevronRight: ['m9 6 6 6-6 6'],
  minus: ['M5 12h14'],
  expand: ['M15 4h5v5', 'M9 20H4v-5', 'm20 4-6.5 6.5', 'm4 20 6.5-6.5'],
  link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  message: ['M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z'],
  columns: ['M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z', 'M12 4v16'],
  plus: ['M12 5v14', 'M5 12h14'],
  trash: ['M4 7h16', 'M9 7V4.5h6V7', 'm6 7 1 12.5a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5L18 7', 'M10 11v6', 'M14 11v6'],
  upload: ['M12 15V4', 'm7.5 8.5 4.5-4.5 4.5 4.5', 'M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4'],
  arrowRight: ['M5 12h14', 'm13 6 6 6-6 6'],
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name }: { name: IconName }) {
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{PATHS[name].map(d => <path key={d} d={d} />)}</svg>;
}

// Share the installed app icon; inline the PNG so exported readers stay offline.
export function BrandMark({ className = 'brand-mark' }: { className?: string }) {
  return <img className={className} src={brandIcon} alt="" aria-hidden="true" draggable={false}/>;
}
