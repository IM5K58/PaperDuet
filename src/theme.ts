export type Theme = 'light' | 'dark';

// Only light and dark are offered. A setting saved before that ('system', or nothing
// yet on a fresh install) starts from the Windows preference once.
export function resolveTheme(theme?: string | null): Theme {
  if (theme === 'light' || theme === 'dark') return theme;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
