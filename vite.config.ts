import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tauriConfig from './src-tauri/tauri.conf.json';

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  logLevel: 'warn',
  server: { host: '127.0.0.1', port: 1420, strictPort: true,
    watch: { ignored: ['**/.tools/**', '**/.venv/**', '**/src-tauri/**', '**/artifacts/**', '**/test-results/**', '**/build/**'] },
  },
  preview: { headers: { 'Content-Security-Policy': tauriConfig.app.security.csp } },
  // Emit every font as a local asset: native font-src 'self' intentionally
  // disallows Vite's default small-font data: URL inlining.
  build: { target: 'es2022', chunkSizeWarningLimit: 800, reportCompressedSize: false, assetsInlineLimit: 0, rollupOptions: {output: {manualChunks: {rendering: ['katex','rehype-katex','remark-math','react-markdown','remark-gfm']}}} },
});
