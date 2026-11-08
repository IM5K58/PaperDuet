/** Shared labels and accelerators for the desktop OS and offline reader. */
export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
export const credentialStore = isMac ? 'macOS 키체인' : 'Windows 자격 증명 관리자';
export const shortcut = (key:string) => `${isMac ? '⌘' : 'Ctrl'}+${key}`;
export const primaryKey = (event:{ctrlKey:boolean;metaKey:boolean}) => event.ctrlKey || (isMac && event.metaKey);
