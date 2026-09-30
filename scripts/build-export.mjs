import { build } from 'vite';
import react from '@vitejs/plugin-react';
import { readFile,writeFile,mkdir,readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
await build({configFile:false,define:{'process.env.NODE_ENV':JSON.stringify('production')},plugins:[react(),{name:'offline-transport',enforce:'pre',resolveId(source,importer){if(source==='./api'&&importer?.replaceAll('\\','/').includes('/src/'))return resolve('src/export-api.ts');}}],logLevel:'warn',build:{outDir:'build/export',emptyOutDir:true,cssCodeSplit:false,assetsInlineLimit:Number.MAX_SAFE_INTEGER,lib:{entry:resolve('src/export-entry.tsx'),name:'PaperDuetExport',formats:['iife'],fileName:()=> 'reader.js'},rollupOptions:{output:{inlineDynamicImports:true}}}});
const files=await readdir('build/export');
// Every @font-face lists woff2 then a woff fallback; both get inlined. The exported reader targets
// browsers that read woff2, so drop the woff copies (~half of the template size).
const css=(await Promise.all(files.filter(f=>f.endsWith('.css')).map(f=>readFile('build/export/'+f,'utf8')))).join('\n')
  .replace(/,\s*url\(data:font\/woff;base64,[A-Za-z0-9+/=]+\)\s*format\(["']?woff["']?\)/g,'');
if(/url\((?!["']?data:)/.test(css))throw new Error('Export contains external CSS assets');
const js=(await readFile('build/export/reader.js','utf8')).replace(/<\/script/gi,'<\\/script');
const hash=createHash('sha256').update(js).digest('base64');
const html=`<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-${hash}'; style-src 'unsafe-inline'; font-src data:; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>PaperDuet 독립 리더</title><style>${css}</style></head><body><div id="root"></div><script type="application/json" id="paperduet-data">__PAPERDUET_DATA__</script><script>${js}</script></body></html>`;
await mkdir('backend/resources',{recursive:true});await writeFile('backend/resources/export-template.html',html);
console.log(`Standalone reader template: ${(Buffer.byteLength(html)/1024/1024).toFixed(1)} MiB`);
