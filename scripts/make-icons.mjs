// Rebuild native icons, the shared SVG wrapper and installer artwork from the
// approved source PNG. The source artwork is preserved without regeneration.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../', import.meta.url);
const icons = new URL('src-tauri/icons/', root);
const source = new URL('icon-source.png', icons);
const cli = new URL('node_modules/@tauri-apps/cli/tauri.js', root);
execFileSync(process.execPath, [fileURLToPath(cli), 'icon', fileURLToPath(source), '--output', fileURLToPath(icons)], {
  cwd: fileURLToPath(root), stdio: 'inherit', windowsHide: true,
});
const image = readFileSync(new URL('128x128@2x.png', icons)).toString('base64');
writeFileSync(new URL('icon.svg', icons), `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><image width="256" height="256" href="data:image/png;base64,${image}"/></svg>\n`);
await import('./make-installer-art.mjs');
