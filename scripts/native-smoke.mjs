// Tests the actual packaged Tauri/WebView2 application, without IPC mocks.
// Remote debugging is enabled only on this short-lived test process.
import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createServer } from 'node:net';

const exe = resolve(process.argv[2] || 'src-tauri/target/release/paperduet.exe');
const pdf = process.argv[3] ? resolve(process.argv[3]) : null;
const output = resolve('artifacts/native-smoke');
await mkdir(output, { recursive: true });
const shell = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
const ps = script => execFileSync(shell, ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8', windowsHide: true });
const allProcesses = () => JSON.parse(ps('Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress'));
const descendants = root => {
  const list = allProcesses(); const ids = new Set([root]);
  let changed = true;
  while (changed) { changed = false; for (const p of list) if (ids.has(p.ParentProcessId) && !ids.has(p.ProcessId)) { ids.add(p.ProcessId); changed = true; } }
  return list.filter(p => ids.has(p.ProcessId));
};
const server = createServer();
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;
await new Promise(r => server.close(r));
// Exclude developer runtimes from the app's environment, while the test runner
// itself stays outside the app. This is not a substitute for a clean VM test.
const env = { ...process.env, PATH: `${process.env.SystemRoot}\\System32;${process.env.SystemRoot}`, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}`, WEBVIEW2_USER_DATA_FOLDER: join(output, 'webview-profile') };
for (const key of ['PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'NODE_PATH']) delete env[key];
const app = spawn(exe, [], { env, windowsHide: true, stdio: 'ignore' });
let browser;
let page;
let tracked = [];
const imageFailures=[];
try {
  let endpoint;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (app.exitCode !== null) throw new Error(`App exited before fixture loaded (${app.exitCode})`);
    try { const response = await fetch(`http://127.0.0.1:${port}/json/version`); endpoint = (await response.json()).webSocketDebuggerUrl; break; } catch { await new Promise(r => setTimeout(r, 500)); }
  }
  if (!endpoint) throw new Error('WebView2 test endpoint did not become ready');
  browser = await chromium.connectOverCDP(endpoint);
  for (let attempt = 0; attempt < 60; attempt++) {
    page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().includes('tauri.localhost'));
    if (page) break;
    await new Promise(r => setTimeout(r, 500));
  }
  if (!page) throw new Error('Native app page not found');
  const builtIndex = await readFile(resolve('dist/index.html'), 'utf8');
  const expectedScript = builtIndex.match(/src="([^\"]+\.js)"/)[1];
  await expect(page.locator('script[type=module]')).toHaveAttribute('src', expectedScript);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text().replace(/data:[^'\"]+/g, '[embedded asset]')); });
  page.on('response',r=>{if(r.url().endsWith('/image')&&r.status()>=400)imageFailures.push({path:new URL(r.url()).pathname,status:r.status()});});
  page.on('requestfailed',r=>{if(r.url().endsWith('/image'))imageFailures.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText});});
  await expect(page.getByRole('heading', { name: 'Detect Anything via Next Point Prediction', exact: true })).toBeVisible({ timeout: 45_000 });
  const intro=page.getByRole('dialog',{name:'PaperDuet 시작하기'});
  const onboarding=await page.evaluate(async()=>{const c=await window.__TAURI_INTERNALS__.invoke('backend_connection');return (await fetch(`http://127.0.0.1:${c.port}/settings/onboarding`,{headers:{Authorization:`Bearer ${c.token}`}})).json();});
  if(!onboarding.completed){await expect(intro).toBeVisible({timeout:45000});await intro.getByRole('button',{name:'지금은 원문만 읽기'}).click();await expect(intro).toHaveCount(0);}
  await expect(page.locator('[data-block]')).toHaveCount(285);
  await expect(page.locator('.table-block')).toHaveCount(17);
  await expect(page.locator('.figure-block')).toHaveCount(18);
  await page.evaluate(() => document.fonts.ready);
  // Verify persistence through native IPC in the exact installed frontend too.
  await page.locator('.toc').getByRole('link', { name: '7 결론', exact: true }).click();
  await page.waitForResponse(r => r.url().endsWith('/position') && r.status() === 204);
  const topSaved = page.waitForResponse(r => r.url().endsWith('/position') && r.status() === 204);
  await page.getByRole('button', { name: '맨 위로' }).click();
  await topSaved;
  await page.reload();
  await expect(page.locator('[data-block]')).toHaveCount(285);
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await page.screenshot({ path: join(output, 'installed-reader.png') });
  for (let i=0;i<2;i++) {
    const equation=page.locator('.equation-card').nth(i);
    await equation.scrollIntoViewIfNeeded();
    await expect(equation.locator('.katex')).toHaveCount(1);
    await expect(equation.locator('.equation-error')).toHaveCount(0);
    await equation.getByRole('button',{name:'LaTeX 복사',exact:true}).click();
    await expect(equation.locator('.copy-equation')).toHaveText('복사됨');
  }
  let pdfReport = null;
  const askReport=await page.evaluate(async()=>{
    const c=await window.__TAURI_INTERNALS__.invoke('backend_connection');
    const headers={Authorization:`Bearer ${c.token}`,'Content-Type':'application/json'};
    const doc=await (await fetch(`http://127.0.0.1:${c.port}/documents/rex-omni`,{headers})).json();
    const block=doc.blocks.find(b=>b.type==='p'&&/Table 2/.test(b.en||''));
    const response=await fetch(`http://127.0.0.1:${c.port}/ask/context`,{method:'POST',headers,body:JSON.stringify({doc_id:'rex-omni',anchor:{block_id:block.id},question:'Table 2의 수치를 설명해 주세요.',model:'default',provider:'openai',mode:'cli'})});
    const context=await response.json();
    const config=await (await fetch(`http://127.0.0.1:${c.port}/settings/providers?provider=openai&mode=cli`,{headers})).json();
    const health=config.configured?await (await fetch(`http://127.0.0.1:${c.port}/providers/health?provider=openai&mode=cli`,{headers})).json():{status:'CLI_NOT_FOUND'};
    return {context_status:response.status,table2_resolved:context.snapshot.context.references.some(b=>b.n==='Table 2'&&b.table),cli_status:health.status||health.detail,cli_version:health.version||null};
  });
  expect(askReport.context_status).toBe(200);expect(askReport.table2_resolved).toBe(true);
  expect(['ok','login_required','CLI_NOT_FOUND']).toContain(askReport.cli_status);
  if (pdf) {
    await page.getByRole('button', { name: '서재', exact: true }).click();
    await page.getByLabel('PDF 파일', { exact: true }).setInputFiles(pdf);
    const dialog = page.getByRole('dialog', { name: '논문 처리' });
    await expect(dialog.getByRole('button', { name: '원문·대역 읽기' })).toBeEnabled({ timeout: 120_000 });
    await dialog.getByRole('button', { name: '원문·대역 읽기' }).click();
    await page.getByRole('button',{name:'추출 텍스트 보기'}).click();
    await expect(page.locator('.table-block')).toHaveCount(17, { timeout: 30_000 });
    await expect(page.locator('.figure-block')).toHaveCount(24);
    await expect(page.locator('.equation-card')).toHaveCount(5);
    await expect(page.locator('.metadata')).toContainText('48 PAGES');
    const original = page.getByRole('img', { name: 'Figure 1 원본 영역', exact: true });
    await page.locator('.figure-block').first().scrollIntoViewIfNeeded();
    await expect(original).toBeVisible();
    expect(await original.evaluate(img => img.naturalWidth)).toBeGreaterThan(0);
    await page.getByRole('button',{name:'Figure 1 원본 확대',exact:true}).click();
    const zoom=page.getByRole('dialog',{name:'Figure 1 확대 보기'});
    await expect(zoom).toBeVisible();
    await zoom.getByRole('button',{name:'이미지 확대',exact:true}).click();
    await expect(zoom.getByLabel('확대 비율')).toHaveText('125%');
    await page.screenshot({path:join(output,'installed-figure-zoom.png')});
    await zoom.getByRole('button',{name:'확대 보기 닫기'}).click();
    await expect(zoom).toHaveCount(0);
    const table2 = page.getByRole('region', { name: 'Table 2 가로 스크롤', exact: true });
    await table2.scrollIntoViewIfNeeded();
    await expect(table2.getByRole('cell', { name: '0.42', exact: true })).toBeVisible();
    await expect(table2.getByRole('columnheader', { name: 'COCO', exact: true })).toHaveAttribute('colspan', '10');
    expect(await table2.locator('tr.highlight').count()).toBeGreaterThan(0);
    expect(await table2.locator('.best').count()).toBeGreaterThan(0);
    // Native Windows scrollbars occupy 15px; innerWidth includes that gutter.
    // Compare the scrollable document with its actual content viewport instead.
    expect(await page.evaluate(() => Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth))).toBe(0);
    await page.screenshot({ path: join(output, 'installed-pdf-table2.png') });
    pdfReport = { pages: 48, blocks: await page.locator('[data-block]').count(), tables: 17, figures: 24, equations: 5, original_table_columns: 14, offline_reader: true, figure_zoom: true, table_highlight: true };
    await page.reload();
    await expect(page.locator('.table-block')).toHaveCount(17);
    pdfReport.survived_reload = true;
  }
  const m4=await page.evaluate(async()=>{
    const c=await window.__TAURI_INTERNALS__.invoke('backend_connection');const headers={Authorization:`Bearer ${c.token}`};
    const response=await fetch(`http://127.0.0.1:${c.port}/documents/rex-omni/export?format=html`,{headers});const html=await response.text();
    const markdown=await fetch(`http://127.0.0.1:${c.port}/documents/rex-omni/export?format=md`,{headers});
    const presentation=await fetch(`http://127.0.0.1:${c.port}/documents/rex-omni/presentation`,{headers});
    const update=await window.__TAURI_INTERNALS__.invoke('update_status');
    return {html_status:response.status,html_bytes:new TextEncoder().encode(html).length,embedded_fonts:html.includes('data:font/'),offline_policy:html.includes("connect-src 'none'"),markdown_status:markdown.status,presentation_status:presentation.status,update};
  });
  expect(m4.html_status).toBe(200);expect(m4.embedded_fonts).toBe(true);expect(m4.offline_policy).toBe(true);expect(m4.markdown_status).toBe(200);expect(m4.presentation_status).toBe(200);expect(m4.update.current_version).toBe('0.8.2');
  tracked = descendants(app.pid);
  if (!tracked.some(p => p.Name === 'paperduet-backend.exe')) throw new Error('Bundled Python backend not running');
  if (tracked.some(p => /^(python|node)(\.exe)?$/i.test(p.Name))) throw new Error('App used external Python or Node');
  const security = await page.evaluate(async () => {
    // Read the same native connection command the application uses, only inside
    // this isolated test. Never return the token to test logs or artifacts.
    const connection = await window.__TAURI_INTERNALS__.invoke('backend_connection');
    return { port: connection.port };
  });
  const unauthorized = await fetch(`http://127.0.0.1:${security.port}/health`);
  const wrongOrigin = await fetch(`http://127.0.0.1:${security.port}/health`, { headers: { Origin: 'https://evil.example' } });
  if (unauthorized.status !== 401 || wrongOrigin.status !== 403) throw new Error('AC14 failed in installed app');
  expect(errors).toEqual([]);
  const closed = ps(`(Get-Process -Id ${app.pid}).CloseMainWindow()`);
  if (!closed.includes('True')) throw new Error('Native close request failed');
  await new Promise((r, reject) => { const timeout = setTimeout(() => reject(new Error('App close timeout')), 10_000); app.once('exit', () => { clearTimeout(timeout); r(); }); });
  let remaining = [];
  for (let attempt = 0; attempt < 50; attempt++) {
    remaining = allProcesses().filter(p => tracked.some(t => t.ProcessId === p.ProcessId) && p.Name === 'paperduet-backend.exe');
    if (!remaining.length) break;
    await new Promise(r => setTimeout(r, 100));
  }
  expect(remaining).toEqual([]);
  const report = { fixture_blocks: 285, tables: 17, figures: 18, equations_1_2_render_and_copy: true, pdf: pdfReport, ask:askReport,m4, external_python_or_node: false, tokenless_status: unauthorized.status, wrong_origin_status: wrongOrigin.status, orphan_backends: remaining.length, console_errors: errors, tested_executable: exe, clean_vm_verified: false };
  await writeFile(join(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch(error) {
  if(page){
    await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});
    const state=await page.evaluate(()=>({scrollY,dialogs:[...document.querySelectorAll('dialog[open]')].map(d=>d.getAttribute('aria-label')),figures:[...document.querySelectorAll('.figure-block')].slice(0,3).map(f=>({id:f.id,label:f.querySelector('figcaption b')?.textContent,placeholder:f.querySelector('.pdf-crop p')?.textContent,img:!!f.querySelector('img'),top:f.getBoundingClientRect().top,height:f.getBoundingClientRect().height}))})).catch(()=>null);
    await writeFile(join(output,'failure-state.json'),JSON.stringify({state,imageFailures},null,2));
  }
  throw error;
} finally {
  // Terminate only our app if the test fails. Kill-on-close Job owns descendants.
  if (app.exitCode === null) app.kill();
  await browser?.close().catch(() => {});
}
