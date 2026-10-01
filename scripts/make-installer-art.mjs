// Installer art for the NSIS installer and the update window:
//   src-tauri/icons/installer-sidebar.bmp  welcome/finish pages (164x314)
//   src-tauri/icons/installer-header.bmp   every other page, incl. updates (150x57)
// Designed in HTML with the app's colours and logo, captured with the installed
// Chrome, and written as the 24-bit BMP files NSIS requires. Previews land in
// build/installer-art/. Re-run after a design change:
//   node scripts/make-installer-art.mjs
import { chromium } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const root = new URL('../', import.meta.url);
const logo = 'data:image/svg+xml;base64,' + readFileSync(new URL('src-tauri/icons/icon.svg', root)).toString('base64');
const latin = "'Segoe UI Variable Display','Segoe UI'";
const korean = "'Malgun Gothic','맑은 고딕'";

// Two columns of text lines: the side-by-side English/Korean reader in miniature.
const lines = (widths, colour) => widths.map(w => `<i style="display:block;height:4px;width:${w}%;border-radius:2px;background:${colour};margin-bottom:7px"></i>`).join('');
const sidebar = `
<div style="position:relative;width:164px;height:314px;overflow:hidden;color:#fff;padding:30px 20px;
  background:linear-gradient(168deg,#2a5285 0%,#1f3a5f 46%,#142740 100%)">
  <div style="position:absolute;right:-56px;top:-48px;width:170px;height:170px;border-radius:50%;background:rgba(167,204,255,.10)"></div>
  <div style="position:absolute;right:-30px;top:96px;width:76px;height:76px;border-radius:50%;background:rgba(24,95,194,.35)"></div>
  <img src="${logo}" width="52" height="52" style="position:relative;display:block;border-radius:12px;box-shadow:0 8px 20px rgba(0,0,0,.35)">
  <div style="position:relative;margin-top:18px;font:700 21px/1.1 ${latin};letter-spacing:-.02em">PaperDuet</div>
  <div style="position:relative;margin-top:10px;font:400 12px/1.6 ${korean};color:rgba(255,255,255,.82)">영어 논문을<br>한국어 대역 리더로</div>
  <div style="position:absolute;left:20px;right:20px;bottom:28px;display:flex;gap:10px;opacity:.9">
    <div style="flex:1">${lines([100, 86, 94, 62, 100, 78], 'rgba(255,255,255,.34)')}</div>
    <div style="flex:1">${lines([92, 100, 70, 96, 84, 58], '#a7ccff')}</div>
  </div>
</div>`;
const header = `
<div style="width:150px;height:57px;background:#fff;display:flex;align-items:center;gap:9px;padding-left:14px">
  <img src="${logo}" width="30" height="30" style="display:block;border-radius:7px">
  <span style="font:700 15px/1 ${latin};letter-spacing:-.02em;color:#1f3a5f">PaperDuet</span>
</div>`;

const art = { 'installer-sidebar': [164, 314, sidebar], 'installer-header': [150, 57, header] };
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ deviceScaleFactor: 1 });
mkdirSync(new URL('build/installer-art/', root), { recursive: true });
for (const [name, [width, height, body]] of Object.entries(art)) {
  await page.setViewportSize({ width, height });
  await page.setContent(`<!doctype html><meta charset="utf-8"><style>*{margin:0;box-sizing:border-box}html,body{width:${width}px;height:${height}px;overflow:hidden}</style>${body}`);
  await page.evaluate(() => document.fonts.ready);
  const png = await page.screenshot({ type: 'png' });
  writeFileSync(new URL(`build/installer-art/${name}.png`, root), png);
  // Canvas pixels -> bottom-up 24-bit BGR rows padded to 4 bytes.
  const bmp = await page.evaluate(async ([data, w, h]) => {
    const image = new Image(); image.src = 'data:image/png;base64,' + data; await image.decode();
    const canvas = Object.assign(document.createElement('canvas'), { width: w, height: h });
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, w, h).data;
    const stride = Math.ceil(w * 3 / 4) * 4, size = 54 + stride * h;
    const out = new Uint8Array(size), view = new DataView(out.buffer);
    out[0] = 0x42; out[1] = 0x4d; view.setUint32(2, size, true); view.setUint32(10, 54, true);
    view.setUint32(14, 40, true); view.setInt32(18, w, true); view.setInt32(22, h, true);
    view.setUint16(26, 1, true); view.setUint16(28, 24, true); view.setUint32(34, stride * h, true);
    view.setInt32(38, 2835, true); view.setInt32(42, 2835, true);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const from = (y * w + x) * 4, to = 54 + (h - 1 - y) * stride + x * 3;
      out[to] = pixels[from + 2]; out[to + 1] = pixels[from + 1]; out[to + 2] = pixels[from];
    }
    let binary = ''; for (const byte of out) binary += String.fromCharCode(byte);
    return btoa(binary);
  }, [png.toString('base64'), width, height]);
  writeFileSync(new URL(`src-tauri/icons/${name}.bmp`, root), Buffer.from(bmp, 'base64'));
  console.log(`src-tauri/icons/${name}.bmp  ${width}x${height}`);
}
await browser.close();
