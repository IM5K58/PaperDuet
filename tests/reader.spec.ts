import { test as base, expect, type Page } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';
import { existsSync } from 'node:fs';

// M3 uses real FastAPI, context assembly, SQLite and SSE with only AI transport replaced.

const test = base.extend<{ backend: { port: number; token: string; generation: number; dataDir:string } }>({
  backend: async ({}, use, testInfo) => {
    const token = randomBytes(32).toString('hex');
    const dataDir = await mkdtemp(join(tmpdir(), 'PaperDuet E2E 한글 '));
    const child = spawn(resolve('.venv/Scripts/python.exe'), [resolve((testInfo.title.startsWith('M3:')||testInfo.title.startsWith('M4:'))?'backend/tests/m3_server.py':testInfo.title.startsWith('M2:')?'backend/tests/m2_server.py':'backend/entrypoint.py')], {
      env: { ...process.env, PAPERDUET_TEST_ARXIV:testInfo.title.startsWith('M4:')?'1':'',PAPERDUET_SESSION_TOKEN: token, PAPERDUET_DATA_DIR: dataDir, PAPERDUET_ORIGIN: 'http://127.0.0.1:1420' }, windowsHide: true,
    });
    try {
      const port = await new Promise<number>((resolvePort, reject) => {
        const timeout = setTimeout(() => reject(new Error('Sidecar readiness timeout')), 30_000);
        createInterface({ input: child.stdout }).once('line', line => { clearTimeout(timeout); resolvePort(JSON.parse(line).port); });
        child.once('error', e => { clearTimeout(timeout); reject(e); });
        child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Sidecar exited: ${code}`)); });
      });
      if(!testInfo.title.includes('first launch'))await fetch(`http://127.0.0.1:${port}/settings/onboarding`,{method:'PUT',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({completed:true})});
      await use({ port, token, generation: 0, dataDir });
    } finally { child.kill(); await new Promise<void>(resolveExit => { if (child.exitCode !== null) resolveExit(); else child.once('exit', () => resolveExit()); }); }
  },
  page: async ({ page, backend }, use) => {
    // Only native IPC is mocked. Every document/settings request hits the real
    // authenticated FastAPI process and its own SQLite DB.
    await page.addInitScript(connection => {
      Object.defineProperty(window, '__TAURI_INTERNALS__', { value: { invoke: async (command: string) => {
        if(command==='check_update'||command==='update_status')return {configured:false,current_version:'0.5.0'};
        if(command==='export_document')return 'C:/테스트/논문.html';
        if (command !== 'backend_connection') throw new Error('Unexpected native command');
        return connection;
      } } });
    }, backend);
    await use(page);
  },
});

// Every scenario reads the Rex-Omni sample, which is kept out of the public repository.
test.skip(!existsSync(resolve('fixtures/rex-omni.blocks.json')), 'Rex-Omni sample fixture is not in this checkout');

async function setReaderTheme(page:Page,theme?:string){
  await page.getByRole('button',{name:'읽기 설정',exact:true}).click();
  const next=theme||(await page.locator('html').getAttribute('data-theme')==='dark'?'light':'dark');
  await page.getByRole('dialog',{name:'읽기 설정',exact:true}).getByRole('button',{name:next==='dark'?'다크':'라이트',exact:true}).click();
  await page.getByRole('button',{name:'읽기 설정',exact:true}).click();
}

test('Library: bookshelf pages, progress, search, menus, import dialog and responsive themes',async({page,backend},info)=>{
  test.setTimeout(120000);
  execFileSync(resolve('.venv/Scripts/python.exe'),[resolve('backend/tests/shelf_sample.py'),backend.dataDir],{windowsHide:true});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.setViewportSize({width:1440,height:1118});await page.goto('/?library=1');
  const shelf=page.locator('.shelf-page[aria-hidden=false]');
  await expect(shelf.locator('.shelf-book')).toHaveCount(14);
  await expect(page.locator('.continue-stats')).toContainText('8쪽에서 멈춤');
  await expect(page.locator('.continue-stats')).toContainText('42% 읽음');
  await expect(shelf.locator('.cover-badge').first()).toContainText('86%');
  await expect(shelf.getByText('처리 실패',{exact:true})).toBeVisible();
  await expect(shelf.locator('.cover-image')).toHaveCount(14);
  await expect.poll(()=>shelf.locator('.cover-image').evaluateAll(images=>images.every(image=>(image as HTMLImageElement).naturalWidth>0))).toBe(true);
  await page.evaluate(()=>document.fonts.ready);
  await page.screenshot({path:info.outputPath('shelf-light.png'),fullPage:true,animations:'disabled'});
  await shelf.locator('.shelf-book').nth(1).screenshot({path:info.outputPath('shelf-processing.png'),animations:'disabled'});
  await shelf.locator('.shelf-book').nth(2).hover();await shelf.locator('.book-menu-button').nth(2).click();
  await expect(page.getByRole('menu')).toBeVisible();await page.screenshot({path:info.outputPath('shelf-menu.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('menuitem',{name:'처리 상태'}).click();await expect(page.getByRole('dialog',{name:'논문 처리'})).toBeVisible();await page.keyboard.press('Escape');
  await shelf.locator('.book-menu-button').nth(2).click();await page.getByRole('menuitem',{name:'삭제',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'논문 삭제'})).toBeVisible();await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'다음 페이지',exact:true}).click();
  await expect(shelf.locator('.shelf-book')).toHaveCount(6);await expect(page.getByRole('button',{name:'2페이지',exact:true})).toHaveAttribute('aria-current','page');
  await expect(page.getByRole('searchbox',{name:'논문 제목 검색'})).toBeVisible();
  await page.getByRole('button',{name:'논문 검색',exact:true}).click();await page.getByRole('searchbox',{name:'논문 제목 검색'}).fill('Attention');
  await expect(shelf.locator('.shelf-book')).toHaveCount(1);await page.getByRole('searchbox').press('Escape');await expect(page.getByRole('searchbox')).toBeVisible();
  await page.getByLabel('정렬',{exact:true}).selectOption('title');expect(await page.evaluate(()=>localStorage.getItem('paperduet-library-sort'))).toBe('title');
  await page.getByLabel('정렬',{exact:true}).selectOption('recent');
  await page.getByRole('button',{name:'논문 추가',exact:true}).click();
  const add=page.getByRole('dialog',{name:'논문 추가',exact:true});await expect(add.getByRole('button',{name:'PDF 선택'})).toBeVisible();
  await expect(add.getByLabel('arXiv ID 또는 URL')).toBeVisible();await page.screenshot({path:info.outputPath('shelf-add.png'),fullPage:true,animations:'disabled'});await page.keyboard.press('Escape');
  await page.evaluate(()=>{document.documentElement.dataset.theme='dark';});await page.screenshot({path:info.outputPath('shelf-dark.png'),fullPage:true,animations:'disabled'});
  for(const width of [390,720,1024,1440]){
    await page.setViewportSize({width,height:960});await expect(shelf.locator('.shelf-book')).toHaveCount(width===390?6:width===720?10:14);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  }
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'논문 검색'}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);await page.screenshot({path:info.outputPath('shelf-390-dark.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'논문 추가',exact:true}).click();expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.screenshot({path:info.outputPath('shelf-add-390-dark.png'),fullPage:true,animations:'disabled'});await page.keyboard.press('Escape');
  await page.emulateMedia({reducedMotion:'reduce'});expect(await page.locator('.shelf-track').evaluate(el=>getComputedStyle(el).transitionDuration)).toBe('0s');
  await page.getByRole('searchbox').press('Escape');await shelf.locator('.book-open').first().click();await expect(page).toHaveURL(/doc=shelf-00/);
  expect(errors).toEqual([]);
});

test('Library: empty state, modal PDF selection, deletion and whole-page drop',async({page,backend},info)=>{
  await fetch(`http://127.0.0.1:${backend.port}/documents/rex-omni`,{method:'DELETE',headers:{Authorization:`Bearer ${backend.token}`}});
  await page.setViewportSize({width:390,height:844});await page.goto('/?library=1');
  await expect(page.locator('.library-empty')).toBeVisible();await expect(page.locator('.shelf-viewport')).toHaveCount(0);
  await page.screenshot({path:info.outputPath('shelf-empty-390.png'),fullPage:true});
  await page.locator('.library-empty').getByRole('button',{name:'논문 추가'}).click();
  const pdf=execFileSync(resolve('.venv/Scripts/python.exe'),['-c',"import sys; sys.path[:0]=['backend','backend/tests']; from test_m1 import make_pdf; sys.stdout.buffer.write(make_pdf())"],{windowsHide:true});
  const file=info.outputPath('empty-library.pdf');await writeFile(file,pdf);
  const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'PDF 선택',exact:true}).click();await (await chooser).setFiles(file);
  const progress=page.getByRole('dialog',{name:'논문 처리'});await expect(progress.getByText('AI 연결 필요',{exact:true})).toBeVisible({timeout:30000});
  await progress.getByRole('button',{name:'창 닫기'}).click();await expect(page.locator('.shelf-book')).toHaveCount(1);
  await page.locator('.book-menu-button').click();await page.getByRole('menuitem',{name:'삭제',exact:true}).click();
  await page.getByRole('dialog',{name:'논문 삭제'}).getByRole('button',{name:'삭제',exact:true}).click();await expect(page.locator('.library-empty')).toBeVisible();
  const transfer=await page.evaluateHandle(base64=>{const bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0));const transfer=new DataTransfer();transfer.items.add(new File([bytes],'dropped.pdf',{type:'application/pdf'}));return transfer;},pdf.toString('base64'));
  await page.locator('.library-page').dispatchEvent('dragover',{dataTransfer:transfer});await expect(page.locator('.library-page')).toHaveClass(/dragging/);
  await page.locator('.library-page').dispatchEvent('drop',{dataTransfer:transfer});await expect(progress.getByText('AI 연결 필요',{exact:true})).toBeVisible({timeout:30000});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
});

test('AC5: equations (1) and (2) render with KaTeX and copy exact LaTeX',async({page})=>{
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  await page.goto('/?doc=rex-omni');await expect(page.locator('[data-block]')).toHaveCount(285);
  const expected=[String.raw`A_i = \frac{r_i-\operatorname{mean}(r_1,\ldots,r_G)}{\operatorname{std}(r_1,\ldots,r_G)}`,
    String.raw`\mathcal{J}_{\mathrm{GRPO}}(\theta)=\frac{1}{G}\sum_{i=1}^{G}\frac{1}{|o_i|}\sum_{t=1}^{|o_i|}\left[\min\left(\rho_{i,t}\hat{A}_{i,t},\operatorname{clip}(\rho_{i,t},1-\epsilon,1+\epsilon)\hat{A}_{i,t}\right)-\beta D_{\mathrm{KL}}[\pi_\theta\|\pi_{\mathrm{ref}}]\right]`];
  for(let i=0;i<2;i++){
    const equation=page.locator('.equation-card').nth(i);await equation.scrollIntoViewIfNeeded();
    await expect(equation.locator('.katex')).toHaveCount(1);await expect(equation.locator('math')).toHaveCount(1);
    await expect(equation.locator('.katex-error,.equation-error')).toHaveCount(0);
    await equation.getByRole('button',{name:'LaTeX 복사',exact:true}).click();
    expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(expected[i]);
  }
});

test('M2: normalized headings, no quality verdicts, evidence jumps and manual edits persist',async({page},testInfo)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  await expect(page.locator('.note')).toHaveCount(6);
  await expect(page.locator('.document-status')).toContainText('번역 완료');
  const result=page.locator('[data-note-kind=res]');const id=await result.getAttribute('id');
  await result.locator('.note-refs a').click();await expect(page).toHaveURL(/#b0001$/);
  const editor=result.locator('..').locator('.note-editor');
  await editor.locator('summary').click();await expect(editor.locator('summary')).toHaveText('주석 수정·재생성');
  await expect(page.locator('.qa-block,.qa-label,.review-label')).toHaveCount(0);
  await expect(page.locator('#b0002 .heading-en')).toHaveCSS('font-size','28px');
  await expect(page.locator('#b0002 .heading-ko')).toHaveCSS('font-size','28px');
  await editor.getByRole('button',{name:'이 주석 재생성'}).click();
  await expect(page.locator('.document-status')).toContainText('번역 완료');
  await expect(result).toHaveAttribute('id',id!);await expect(result).toContainText('42.0');
  await editor.getByRole('textbox',{name:'주석 내용',exact:true}).fill('결과는 88888이다.');
  await editor.getByRole('button',{name:'주석 수정 저장'}).click();
  await expect(page.locator('.document-status')).toContainText('번역 완료');
  await editor.getByRole('textbox',{name:'주석 내용',exact:true}).fill('결과는 42.0이다.');
  await editor.getByRole('button',{name:'주석 수정 저장'}).click();
  await expect(result).toContainText('결과는 42.0이다.');
  await expect(page.locator('.document-status')).toContainText('번역 완료');
  await page.reload();await expect(page.locator('.note')).toHaveCount(6);
  await expect(page.locator(`#${id}`)).toContainText('결과는 42.0이다.');
  await expect(page.locator('.document-status')).toContainText('번역 완료');
  await expect(page.getByText(/숫자 추적성 확인|검수 필요|번역 길이 부족|근거 확인 필요/)).toHaveCount(0);
  await page.getByRole('button',{name:'목차 열기'}).click();await page.getByRole('navigation',{name:'목차'}).getByRole('link',{name:'1 결과',exact:true}).click();
  await expect(page.getByRole('navigation',{name:'목차'})).toBeVisible();await page.getByRole('button',{name:'목차 열기'}).click();
  await page.screenshot({path:testInfo.outputPath('headings-light.png'),animations:'disabled'});
  await setReaderTheme(page,'dark');await page.screenshot({path:testInfo.outputPath('headings-dark.png'),animations:'disabled'});
  await page.setViewportSize({width:390,height:844});await expect(page.locator('#b0002 .heading-ko')).toHaveCSS('font-size','20px');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.screenshot({path:testInfo.outputPath('headings-390-dark.png'),animations:'disabled'});
  expect(errors).toEqual([]);
});

test('M2: figure zoom, table emphasis, dark 390px layout and offline reload',async({page},testInfo)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});await page.goto('/?doc=m2-paper');
  await expect(page.locator('.note')).toHaveCount(6);
  const table=page.locator('.table-block');await table.scrollIntoViewIfNeeded();
  await expect(table.locator('tr.highlight')).toContainText('Nova');await expect(table.locator('.best')).toHaveCount(3);
  await expect(table.getByRole('columnheader',{name:'Score 점수',exact:true})).toHaveAttribute('colspan','2');
  await page.locator('.figure-block').first().scrollIntoViewIfNeeded();await page.getByRole('button',{name:'Figure 1 원본 확대',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Figure 1 확대 보기'});await expect(dialog).toBeVisible();
  expect(await dialog.getByRole('img').evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBe(640);
  for(let i=0;i<4;i++)await dialog.getByRole('button',{name:'이미지 확대',exact:true}).click();
  await expect(dialog.getByLabel('확대 비율')).toHaveText('200%');
  const viewport=dialog.getByRole('region',{name:'확대 이미지 스크롤'});
  expect(await viewport.evaluate(el=>el.scrollWidth>el.clientWidth)).toBe(true);
  expect(await page.evaluate(()=>Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth))).toBe(0);
  await page.screenshot({path:testInfo.outputPath('m2-zoom-390-dark.png')});
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Figure 1 원본 확대',exact:true})).toBeFocused();
  await page.reload();await expect(page.locator('.note')).toHaveCount(6);
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
  expect(await page.evaluate(()=>Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth))).toBe(0);
  expect(errors).toEqual([]);
});

test('M2: invalid LaTeX falls back visibly without crashing or requesting external resources',async({page})=>{
  const errors:string[]=[];const external:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  page.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith('http://127.0.0.1:'))external.push(r.url());});
  await page.route('**/documents/m2-paper',async route=>{
    const response=await route.fetch();const doc=await response.json();
    doc.blocks.find((b:{id:string})=>b.id==='b0004').latex=String.raw`\def\loop{\loop}\loop`;
    await route.fulfill({response,json:doc});
  });
  await page.setViewportSize({width:390,height:844});await page.goto('/?doc=m2-paper');
  const equation=page.locator('#b0004');await equation.scrollIntoViewIfNeeded();
  await expect(equation.locator('.equation-error')).toContainText('수식을 표시할 수 없어 LaTeX 원문을 보여 줍니다.');
  await expect(equation.locator('..').getByRole('button',{name:'이 수식 복원 재시도'})).toBeVisible();
  expect(await page.evaluate(()=>Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth))).toBe(0);
  expect(errors).toEqual([]);expect(external).toEqual([]);
});

test('M1: PDF upload, authenticated SSE, offline original reader, duplicate detection and reopening', async ({page},testInfo)=>{
  await page.setViewportSize({width:720,height:960});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  const pdf=execFileSync(resolve('.venv/Scripts/python.exe'),['-c',"import sys; sys.path[:0]=['backend','backend/tests']; from test_m1 import make_pdf; sys.stdout.buffer.write(make_pdf())"],{windowsHide:true});
  const source=testInfo.outputPath('한글 경로 논문.pdf');await writeFile(source,pdf);
  await page.goto('/?library=1');
  await expect(page.getByRole('heading',{name:'나의 논문 서재'})).toBeVisible();
  await page.getByLabel('PDF 파일',{exact:true}).setInputFiles(source);
  const dialog=page.getByRole('dialog',{name:'논문 처리'});
  await expect(dialog.getByText('AI 연결 필요',{exact:true})).toBeVisible({timeout:30000});
  await expect(dialog.getByText('예상 사용량',{exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('pipeline-offline.png')});
  await dialog.getByRole('button',{name:'원문·대역 읽기'}).click();
  await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'원본 PDF 보기',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'원본 PDF 보기'}).getByRole('img')).toBeVisible();
  await page.mouse.click(2,2);await expect(page.getByRole('dialog',{name:'원본 PDF 보기'})).toHaveCount(0);
  await page.getByRole('button',{name:'원본 PDF 보기',exact:true}).click();await page.getByRole('button',{name:'추출 텍스트 보기'}).click();
  await expect(page.getByRole('heading',{name:'Two Column Study',exact:true})).toBeVisible();
  await expect(page.locator('body')).toHaveAttribute('data-view','en');
  await page.getByRole('button',{name:'목차 열기'}).click();
  await expect(page.getByRole('navigation',{name:'목차'}).getByRole('link',{name:'1 Introduction'})).toBeVisible();
  await page.locator('.toc').getByRole('button',{name:'목차 닫기'}).click();
  const order=await page.locator('[data-block] .en').allTextContents();
  expect(order.findIndex(t=>t.includes('Left second'))).toBeLessThan(order.findIndex(t=>t.includes('Right first')));
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.getByRole('button',{name:'번역',exact:true}).click();
  await page.getByText('참고문헌 · 원문 유지').first().scrollIntoViewIfNeeded();
  await expect(page.getByText('참고문헌 · 원문 유지').first()).toBeVisible();
  await page.reload();await expect(page.getByRole('heading',{name:'Two Column Study',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'PaperDuet · 서재로 이동'}).click();
  await page.getByLabel('PDF 파일',{exact:true}).setInputFiles(source);
  await expect(dialog.getByText('AI 연결 필요',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'창 닫기'}).click();
  await expect(page.locator('.shelf-book')).toHaveCount(2);
  await page.screenshot({path:testInfo.outputPath('library.png')});
  expect(errors).toEqual([]);
});

test('M1: narrow library, system dark theme, provider settings and keyboard dialog dismissal',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});
  await page.goto('/?library=1');await expect(page.locator('.shelf-book')).toHaveCount(1);
  await page.getByRole('button',{name:'AI 연결 설정'}).click();
  await expect(page.getByRole('dialog',{name:'AI 연결 설정'})).toBeVisible();
  await expect(page.getByLabel('API 키',{exact:true})).toHaveAttribute('type','password');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.screenshot({path:testInfo.outputPath('provider-dark-390.png')});
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
});

for (const width of [390, 720, 1024, 1440]) {
  test(`AC6 ${width}px: three modes, both themes, no document overflow or console errors`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 960 });
    const errors: string[] = []; const external: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:')) external.push(request.url()); });
    await page.goto('/?doc=rex-omni');
    await expect(page.locator('[data-block]')).toHaveCount(285);
    await page.evaluate(() => document.fonts.ready);
    for (const mode of ['원문', '대역', '번역']) {
      await page.getByRole('button', { name: mode, exact: true }).click();
      for (const theme of ['light', 'dark']) {
        if (await page.locator('html').getAttribute('data-theme') !== theme) await setReaderTheme(page,theme);
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
        const colors = await page.evaluate(() => ({ bg: getComputedStyle(document.body).backgroundColor, fg: getComputedStyle(document.body).color, scheme: getComputedStyle(document.documentElement).colorScheme }));
        expect(colors.scheme).toBe(theme); expect(colors.bg).not.toBe(colors.fg);
        const book = await page.locator('.book-viewport').boundingBox();
        expect(book!.x).toBeGreaterThanOrEqual(0);expect(book!.x+book!.width).toBeLessThanOrEqual(width);
        await expect(page.locator('.reading-flow')).toBeVisible();await expect(page.locator('.book-pagination')).toHaveCount(0);
      }
    }
    await page.getByRole('button', { name: '대역', exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath(`reader-${width}-dark.png`) });
    await setReaderTheme(page,'light');
    await page.screenshot({ path: testInfo.outputPath(`reader-${width}-light.png`) });
    expect(external).toEqual([]); expect(errors).toEqual([]);
  });
}

test('TOC, scroll spy, progress, local table scrolling, note filters and keyboard search', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.goto('/?doc=rex-omni');
  await expect(page.locator('[data-block]')).toHaveCount(285);
  const table = page.getByRole('region', { name: 'Table 2 가로 스크롤', exact: true });
  const toc = page.getByRole('navigation', { name: '목차' });
  const checkPinnedHeading = async (screenshot: string) => {
    const heading = toc.locator('.toc-heading');
    const top = (await heading.boundingBox())!.y;
    const scroller = toc.locator('.toc-scroll');
    await scroller.evaluate(e => { e.scrollTop = e.scrollHeight; });
    expect(await scroller.evaluate(e => e.scrollTop)).toBeGreaterThan(0);
    expect((await heading.boundingBox())!.y).toBeCloseTo(top, 1);
    await expect(heading.getByText('목차', { exact: true })).toBeInViewport();
    await expect(toc.getByRole('button', { name: '목차 닫기' })).toBeInViewport();
    await page.screenshot({ path: info.outputPath(screenshot) });
    await toc.getByRole('button', { name: '목차 닫기' }).click();
    await expect(toc).toBeHidden();
    await page.getByRole('button', { name: '목차 열기' }).click();
    await expect(toc.locator('a[aria-current=location]')).toBeInViewport();
  };
  await page.getByRole('button', { name: '목차 열기' }).click();
  await checkPinnedHeading('toc-header-light.png');
  await page.getByRole('navigation', { name: '목차' }).getByRole('link', { name: '5.1 일반 객체 검출' }).click();
  await expect(page.locator('.toc a[aria-current=location]')).toContainText('일반 객체 검출');
  expect(Number(await page.getByRole('progressbar').getAttribute('aria-valuenow'))).toBeGreaterThan(0);
  await expect(page.getByRole('navigation',{name:'목차'})).toBeVisible();await page.getByRole('button',{name:'목차 열기'}).click();
  await page.setViewportSize({ width: 720, height: 960 });
  await table.scrollIntoViewIfNeeded();
  expect(await table.evaluate(e => e.scrollWidth > e.clientWidth)).toBe(true);
  await table.evaluate(e => { e.scrollLeft = 200; });
  expect(await table.evaluate(e => e.scrollLeft)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0);
  await page.getByRole('button', { name: '목차 열기' }).click();
  await expect(page.locator('.toc')).toHaveClass(/open/);
  await page.getByRole('button', { name: '용어', exact: true }).click();
  await expect(page.locator('[data-note-kind=trm]')).toHaveCount(0);
  await page.locator('.toc').getByRole('button', { name: '목차 닫기' }).click();
  await page.keyboard.press('Control+3'); await expect(page.locator('body')).toHaveAttribute('data-view', 'ko');
  await page.keyboard.press('Control+f'); await page.getByRole('textbox', { name: '문서 검색' }).fill('Geometry-aware Rewards');
  await page.getByRole('textbox', { name: '문서 검색' }).press('Enter');
  // This English phrase first appears in the abstract, even in Korean view.
  await expect(page).toHaveURL(/#b0004$/);
  await expect(page.getByRole('search').getByRole('status')).toHaveText('1 / 5');
  for (let i = 0; i < 3; i++) await page.getByRole('button', { name: '다음 검색 결과' }).click();
  await expect(page).toHaveURL(/#b0118$/);
  await expect(page.locator('.toc a[aria-current=location]')).toContainText('기하 인식 보상');
  await page.keyboard.press('Escape'); await expect(page.getByRole('search')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await setReaderTheme(page, 'dark');
  await page.getByRole('button', { name: '목차 열기' }).click();
  await checkPinnedHeading('toc-header-390-dark.png');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0);
});

test('font, view, theme and reading position survive reload; system dark mode and six note colors', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/?doc=rex-omni');
  await expect(page.locator('[data-block]')).toHaveCount(285);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
  const colors = await page.locator('.note').evaluateAll(notes => Object.fromEntries(notes.map(n => [n.getAttribute('data-note-kind'), getComputedStyle(n).borderLeftColor])));
  expect(new Set(Object.values(colors)).size).toBe(6);
  await page.getByRole('button',{name:'읽기 설정',exact:true}).click();await page.getByRole('button', { name: '글자 크게' }).click();await page.getByRole('button',{name:'읽기 설정',exact:true}).click();
  await page.getByRole('button', { name: '원문', exact: true }).click();
  await page.getByRole('navigation', { name: '목차' }).getByRole('link', { name: '7 결론', exact: true }).click();
  await page.waitForResponse(r => r.url().endsWith('/position') && r.status() === 204);
  // Remove hash to exercise the persisted SQLite position, not browser anchors.
  await page.evaluate(() => history.replaceState(null, '', '/'));
  await page.reload(); await expect(page.locator('[data-block]')).toHaveCount(285);
  await expect(page.locator('body')).toHaveAttribute('data-view', 'en');
  await expect(page.locator('html')).toHaveCSS('--fs', '17px');
  await expect(page.locator('.toc a[aria-current=location]')).toContainText('결론');
  const savedTop = page.waitForResponse(r => r.url().endsWith('/position') && r.status() === 204);
  await page.getByRole('button', { name: '맨 위로' }).click();
  await savedTop;
  await page.reload(); await expect(page.locator('[data-block]')).toHaveCount(285);
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});


test('M3: first launch connection-first onboarding can skip to offline reader',async({page})=>{
  await page.goto('/');const intro=page.getByRole('dialog',{name:'PaperDuet 시작하기'});
  await expect(intro).toBeVisible();await expect(intro.getByRole('button',{name:'서재에서 시작하기'})).toBeDisabled();
  await intro.getByLabel('연결 방식',{exact:true}).selectOption('cli');
  await expect(intro).toContainText('claude auth login');
  await intro.getByLabel('제공자',{exact:true}).selectOption('google');
  await expect(intro.getByLabel('연결 방식',{exact:true})).toHaveValue('api_key');
  await intro.getByRole('button',{name:'지금은 원문만 읽기'}).click();
  await page.reload();await expect(intro).toHaveCount(0);await expect(page.locator('[data-block]')).toHaveCount(285);
});

test('M3: AC7-11 selection, references, followups, presets and saved AI notes persist',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  await page.locator('#b0001 [data-ask-field=en]').evaluate(el=>{const range=document.createRange();range.setStart(el.firstChild!,0);range.setEnd(el.firstChild!,2);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
  await expect(page.getByRole('toolbar',{name:'선택 텍스트 액션'})).toBeVisible();await page.keyboard.press('Control+k');
  const panel=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(panel).toBeVisible();await expect(panel.locator('.selected-quote')).toHaveText('We');
  await panel.getByRole('button',{name:'쉽게 설명',exact:true}).click();await panel.getByRole('button',{name:'질문 보내기',exact:true}).click();
  await expect(panel.locator('.ask-message.assistant .katex')).toHaveCount(1);await expect(panel).toContainText('입력 123 · 출력 45 토큰');
  await panel.getByText('전송 컨텍스트 확인',{exact:true}).click();
  await expect(panel.locator('.context-debug pre')).toContainText('"references"');await expect(panel.locator('.context-debug pre')).toContainText('"b0003"');
  await panel.getByRole('textbox',{name:'질문',exact:true}).fill('왜 그렇게 되지?');await panel.getByRole('button',{name:'질문 보내기',exact:true}).click();
  await expect(panel.locator('.ask-message.assistant')).toHaveCount(2);
  await expect(panel.locator('.ask-message.assistant').last()).toContainText('입력 123');
  await panel.getByRole('textbox',{name:'질문',exact:true}).fill('재생성 중에도 남는 질문 초안');
  await panel.getByRole('button',{name:'답변 재생성',exact:true}).click();await expect(panel.locator('.ask-message.assistant')).toHaveCount(3);
  await expect(panel.locator('.ask-message.assistant').last()).toContainText('입력 123');
  await expect(panel.getByRole('textbox',{name:'질문',exact:true})).toHaveValue('재생성 중에도 남는 질문 초안');
  await panel.getByRole('button',{name:'Markdown 복사',exact:true}).last().click();expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('$$x=42.0$$');
  await panel.getByRole('button',{name:'평문 복사',exact:true}).last().click();expect(await page.evaluate(()=>navigator.clipboard.readText())).not.toContain('**논문 명시**');
  await panel.getByRole('button',{name:'주석으로 저장',exact:true}).last().click();await expect(page.locator('.ai-answer-badge')).toHaveCount(1);await expect(page.locator('.note .katex')).toHaveCount(1);
  await panel.getByText('AI 연결·질문 설정',{exact:true}).click();await panel.getByRole('button',{name:'질문 규칙·프리셋'}).click();const editor=page.getByRole('dialog',{name:'질문 규칙·프리셋'});
  await editor.getByRole('button',{name:'프리셋 추가'}).click();await editor.getByLabel('액션 이름',{exact:true}).last().fill('내 검증');
  await editor.getByLabel('액션 지시',{exact:true}).last().fill('근거부터 설명해 줘');await editor.getByRole('button',{name:'질문 설정 저장'}).click();
  await expect(panel.getByRole('button',{name:'내 검증',exact:true})).toBeVisible();
  await page.screenshot({path:info.outputPath('m3-ask-desktop.png')});
  await panel.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();await page.reload();
  await expect(page.locator('.ai-answer-badge')).toHaveCount(1);await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  await expect(page.locator('.ask-message.assistant')).toHaveCount(3);expect(errors).toEqual([]);
});

test('M3: context diagnostics load only when expanded and history uses compact messages',async({page,backend})=>{
  const contexts:string[]=[],threadLoads:string[]=[];
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/ask/context'||/\/messages\/[^/]+\/context$/.test(url.pathname))contexts.push(url.pathname);if(/^\/threads\/[^/]+$/.test(url.pathname)&&request.method()==='GET')threadLoads.push(url.search);});
  await page.goto('/?doc=m2-paper');
  const open=()=>page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  await open();const panel=page.getByRole('dialog',{name:'Ask AI',exact:true}),question=panel.getByRole('textbox',{name:'질문',exact:true});
  await question.fill('문맥을 확인하는 질문');await page.waitForTimeout(450);expect(contexts).toEqual([]);
  await panel.getByText('전송 컨텍스트 확인',{exact:true}).click();await expect(panel.locator('.context-debug pre')).toContainText('문맥을 확인하는 질문');
  expect(contexts).toEqual(['/ask/context']);
  await panel.getByText('전송 컨텍스트 확인',{exact:true}).click();await panel.getByRole('button',{name:'질문 보내기',exact:true}).click();
  await expect(panel).toContainText('입력 123 · 출력 45 토큰');
  const headers={Authorization:`Bearer ${backend.token}`};
  const threads=await (await fetch(`http://127.0.0.1:${backend.port}/threads?doc_id=m2-paper`,{headers})).json();
  const full=await (await fetch(`http://127.0.0.1:${backend.port}/threads/${threads[0].id}`,{headers})).json();
  const mid=full.messages.at(-1).id;
  await page.reload();await open();await expect(panel).toContainText('입력 123 · 출력 45 토큰');
  expect(threadLoads).toContain('?compact=true');expect(contexts).toEqual(['/ask/context']);
  await panel.getByText('전송 컨텍스트 확인',{exact:true}).click();await expect(panel.locator('.context-debug pre')).toContainText('문맥을 확인하는 질문');
  expect(contexts).toEqual(['/ask/context',`/threads/${threads[0].id}/messages/${mid}/context`]);
  expect(JSON.parse(await panel.locator('.context-debug pre').innerText())).toEqual(full.messages.at(-1).context_snapshot);
});

test('M3: selected text always starts a new conversation and preserves drafts and running answers',async({page,backend},info)=>{
  const errors:string[]=[],sent:{thread_id:string|null;anchor:{block_id:string;field:string}}[]=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/ask')sent.push(r.postDataJSON());});
  await page.setViewportSize({width:1440,height:960});
  await page.addInitScript(()=>localStorage.setItem('paperduet-window-ask',JSON.stringify({x:876,y:100,width:540,height:650})));
  await page.goto('/?doc=m2-paper');
  const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true});
  await dock.click();const ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),input=ask.getByRole('textbox',{name:'질문',exact:true}),picker=ask.getByLabel('이 논문의 대화');
  await expect(ask).toContainText('질문 대상 · 이 논문');await expect(ask.getByRole('button',{name:'질문 보내기',exact:true})).toBeDisabled();
  await input.fill('작성 중이던 질문');await dock.click();await expect(input).toHaveValue('작성 중이던 질문');
  await expect(page.locator('.block-ask-actions,.thread-badge')).toHaveCount(0);
  const originalDraft=await picker.inputValue();
  await ask.getByRole('button',{name:'새 대화',exact:true}).click();await input.fill('동시 생성 A');await input.press('Control+Enter');
  await expect(ask.locator('.ask-message.assistant')).toContainText('동시 생성 A 시작');const runningId=await picker.inputValue();
  const first=page.locator('#b0001 [data-ask-field=en]');await first.scrollIntoViewIfNeeded();const rect=(await first.boundingBox())!;
  await page.mouse.move(rect.x+2,rect.y+8);await page.mouse.down();await page.mouse.move(rect.x+100,rect.y+8,{steps:5});await page.mouse.up();await expect(ask).toBeVisible();
  const select=async(selector:string,length:number)=>page.locator(selector).evaluate((el,n)=>{if(document.activeElement instanceof HTMLElement)document.activeElement.blur();const range=document.createRange();range.setStart(el.firstChild!,0);range.setEnd(el.firstChild!,n);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);return range.toString();},length);
  await select('#b0001 [data-ask-field=en]',2);
  const selection=page.getByRole('toolbar',{name:'선택 텍스트 액션'});await expect(selection.getByRole('button')).toHaveCount(1);
  await expect(ask.locator('.selected-quote')).toHaveCount(0);await selection.getByRole('button').click();
  await expect(ask.locator('.selected-quote')).toHaveText('We');await expect(input).toHaveValue('');await expect(ask.locator('.ask-message')).toHaveCount(0);expect(sent).toHaveLength(1);
  await expect(ask.getByText('다른 대화 1개에서 답변 생성 중',{exact:true})).toBeVisible();
  await ask.getByRole('button',{name:'쉽게 설명',exact:true}).click();await expect(input).toHaveValue('쉬운 말로 정확하게 설명해 주세요.');expect(sent).toHaveLength(1);
  await input.press('Control+Enter');await expect(ask.locator('.ask-message.assistant')).toContainText('입력 123');
  const tid=await picker.inputValue();expect(tid).not.toBe(runningId);expect(sent[1].thread_id).toBeNull();expect(sent[1].anchor.block_id).toBe('b0001');
  await input.fill('다음 질문 초안');const caption=await select('#b0005 [data-ask-field=caption_en]',5);await page.keyboard.press('Control+k');
  await expect(ask.locator('.selected-quote')).toHaveText(caption);await expect(input).toHaveValue('');await expect(ask.locator('.ask-message')).toHaveCount(0);expect(sent).toHaveLength(2);
  const captionDraft=await picker.inputValue();expect(captionDraft).not.toBe(tid);await input.fill('선택한 그림을 설명해 줘');
  await picker.selectOption(originalDraft);await expect(input).toHaveValue('작성 중이던 질문');await expect(ask.locator('.selected-quote')).toHaveCount(0);
  await picker.selectOption(tid);await expect(input).toHaveValue('다음 질문 초안');await expect(ask.locator('.selected-quote')).toHaveText('We');await expect(ask.locator('.ask-message.assistant')).toHaveCount(1);
  await picker.selectOption(runningId);await expect(ask.getByRole('button',{name:'응답 중지'})).toBeVisible();
  await writeFile(join(backend.dataDir,'ask-release-A'),'release');await expect(ask.locator('.ask-message.assistant')).toContainText('동시 생성 A 완료');
  await picker.selectOption(captionDraft);await expect(ask.locator('.selected-quote')).toHaveText(caption);await input.fill('선택한 그림을 설명해 줘');await input.press('Control+Enter');
  await expect(ask.locator('.ask-message.assistant')).toContainText('입력 123');const captionId=await picker.inputValue();expect(captionId).not.toBe(tid);
  expect(sent[2].thread_id).toBeNull();expect(sent[2].anchor.block_id).toBe('b0005');
  await page.locator('#b0005 [data-ask-field=caption_en]').scrollIntoViewIfNeeded();await select('#b0005 [data-ask-field=caption_en]',5);await selection.getByRole('button').click();
  await expect(ask.locator('.selected-quote')).toHaveText(caption);await expect(input).toHaveValue('');await expect(ask.locator('.ask-message')).toHaveCount(0);
  const repeatedDraft=await picker.inputValue();expect(repeatedDraft).not.toBe(captionId);expect(repeatedDraft).not.toBe(captionDraft);expect(sent).toHaveLength(3);
  await input.fill('닫아도 남는 초안');await dock.click();await expect(picker).toHaveValue(repeatedDraft);await expect(input).toHaveValue('닫아도 남는 초안');
  await page.screenshot({path:info.outputPath('ask-selection-new-conversation-light.png'),animations:'disabled'});
  await ask.getByRole('button',{name:'AI 질문 닫기'}).click();await expect(ask).toBeHidden();await dock.click();await expect(input).toHaveValue('닫아도 남는 초안');
  await page.mouse.click(1430,800);await expect(ask).toBeVisible();await page.keyboard.press('Escape');await expect(ask).toBeHidden();
  await page.reload();await dock.click();await expect(input).toHaveValue('닫아도 남는 초안');await expect(ask.locator('.selected-quote')).toHaveText(caption);await expect(ask.locator('.ask-message')).toHaveCount(0);
  await picker.selectOption(tid);await expect(ask.locator('.ask-message.assistant')).toHaveCount(1);await expect(input).toHaveValue('다음 질문 초안');
  await setReaderTheme(page,'dark');await page.setViewportSize({width:390,height:844});await dock.click();
  await page.screenshot({path:info.outputPath('ask-selection-preserved-conversation-390-dark.png'),animations:'disabled'});expect(await ask.evaluate(e=>e.scrollWidth-e.clientWidth)).toBe(0);expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  const threads=await fetch(`http://127.0.0.1:${backend.port}/threads?doc_id=m2-paper`,{headers:{Authorization:`Bearer ${backend.token}`}}).then(r=>r.json());expect(threads).toHaveLength(3);
  await ask.getByRole('button',{name:'AI 질문 닫기'}).click();await page.goto('/?doc=rex-omni');await dock.click();await expect(input).toHaveValue('');await expect(ask.locator('.ask-message.assistant')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('M3: selected text opens fresh before the existing conversation list finishes loading',async({page})=>{
  await page.goto('/?doc=m2-paper');const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true});await dock.click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),input=ask.getByRole('textbox',{name:'질문',exact:true}),picker=ask.getByLabel('이 논문의 대화');
  await input.fill('이전에 저장한 질문');await input.press('Control+Enter');await expect(ask.locator('.ask-message.assistant')).toContainText('입력 123');
  const oldId=await picker.inputValue();await page.evaluate(()=>localStorage.removeItem('paperduet-ask-m2-paper'));
  await page.reload();
  let release=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/threads?doc_id=m2-paper',async route=>{await gate;await route.continue();});
  await page.locator('#b0001 [data-ask-field=en]').evaluate(el=>{const range=document.createRange();range.setStart(el.firstChild!,0);range.setEnd(el.firstChild!,2);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);});
  const listRequest=page.waitForRequest(r=>new URL(r.url()).pathname==='/threads');
  await page.keyboard.press('Control+k');await listRequest;release();
  await expect(picker).toBeEnabled();await expect(ask.locator('.selected-quote')).toHaveText('We');await expect(input).toHaveValue('');await expect(ask.locator('.ask-message')).toHaveCount(0);
  expect(await picker.inputValue()).not.toBe(oldId);
  await picker.selectOption(oldId);await expect(ask.locator('.ask-message.assistant')).toContainText('입력 123');await expect(ask.locator('.selected-quote')).toHaveCount(0);
});

test('M3: unused selection drafts are pruned while questions and custom titles survive',async({page})=>{
  await page.addInitScript(()=>{
    if(localStorage.getItem('paperduet-ask-m2-paper'))return;
    const draft={threadId:null,title:'',anchor:{block_id:'b0001',field:'en',start:0,end:2,text:'We'},question:'',preset:'',full:false,includeImage:true};
    localStorage.setItem('paperduet-ask-m2-paper',JSON.stringify({active:'draft:written',sessions:{
      'draft:unused-1':draft,'draft:unused-2':draft,'draft:written':{...draft,question:'작성 중이던 질문'},'draft:named':{...draft,title:'직접 붙인 제목'},
    }}));
  });
  let sent=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/ask')sent++;});
  await page.goto('/?doc=m2-paper');const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true});await dock.click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),input=ask.getByRole('textbox',{name:'질문',exact:true}),picker=ask.getByLabel('이 논문의 대화');
  await expect(picker.locator('option')).toHaveCount(2);await expect(input).toHaveValue('작성 중이던 질문');
  let previous=await picker.inputValue();
  for(let index=0;index<3;index++){
    await page.locator('#b0001 [data-ask-field=en]').evaluate(el=>{if(document.activeElement instanceof HTMLElement)document.activeElement.blur();const range=document.createRange();range.setStart(el.firstChild!,0);range.setEnd(el.firstChild!,2);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);});
    await page.keyboard.press('Control+k');await expect(ask.locator('.selected-quote')).toHaveText('We');await expect(input).toHaveValue('');await expect(picker.locator('option')).toHaveCount(3);
    const current=await picker.inputValue();expect(current).not.toBe(previous);previous=current;
  }
  await ask.getByRole('button',{name:'AI 질문 닫기'}).click();await dock.click();await expect(picker).toHaveValue(previous);
  await page.reload();await dock.click();await expect(picker.locator('option')).toHaveCount(3);await expect(ask.locator('.selected-quote')).toHaveText('We');
  await picker.selectOption('draft:written');await expect(input).toHaveValue('작성 중이던 질문');await expect(picker.locator('option')).toHaveCount(2);
  await picker.selectOption('draft:named');await expect(ask.getByRole('button',{name:'대화 제목: 직접 붙인 제목',exact:true})).toBeVisible();expect(sent).toBe(0);
});

test('M3: selection conversations use quoted titles and preserve manual names',async({page,backend},info)=>{
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),input=ask.getByRole('textbox',{name:'질문',exact:true}),picker=ask.getByLabel('이 논문의 대화'),title=ask.getByRole('button',{name:/^대화 제목:/});
  const ids:string[]=[],quotes:string[]=[];
  for(const length of [2,15]){
    const quote=await page.locator('#b0001 [data-ask-field=en]').evaluate((el,n)=>{if(document.activeElement instanceof HTMLElement)document.activeElement.blur();const range=document.createRange();range.setStart(el.firstChild!,0);range.setEnd(el.firstChild!,n);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);return range.toString();},length);
    quotes.push(quote);await page.keyboard.press('Control+k');await expect(title).toHaveText(quote);
    await ask.getByRole('button',{name:'쉽게 설명',exact:true}).click();await ask.getByRole('button',{name:'질문 보내기',exact:true}).click();
    await expect(ask.locator('.ask-message.assistant')).toContainText('입력 123');await expect(input).toBeEnabled();await expect(title).toHaveText(quote);ids.push(await picker.inputValue());
  }
  expect(ids[0]).not.toBe(ids[1]);await expect(picker.locator('option')).toHaveCount(2);
  await title.dblclick();const name=ask.getByRole('textbox',{name:'대화 제목',exact:true});await name.fill('방법 비교');await name.press('Enter');await expect(title).toHaveText('방법 비교');
  await ask.getByRole('button',{name:'선택 문장 해제'}).click();await input.fill('논문 전체에서 다시 설명해 줘');await input.press('Control+Enter');await expect(ask.locator('.ask-message.assistant')).toHaveCount(2);await expect(input).toBeEnabled();await expect(title).toHaveText('방법 비교');
  await page.screenshot({path:info.outputPath('selection-conversation-titles-light.png'),animations:'disabled'});
  await page.reload();await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();await expect(title).toHaveText('방법 비교');
  await picker.selectOption(ids[0]);await expect(title).toHaveText(quotes[0]);
  const threads=await fetch(`http://127.0.0.1:${backend.port}/threads?doc_id=m2-paper`,{headers:{Authorization:`Bearer ${backend.token}`}}).then(r=>r.json());
  expect(threads.find((t:{id:string})=>t.id===ids[0]).title).toBe(quotes[0]);expect(threads.find((t:{id:string})=>t.id===ids[1]).title).toBe('방법 비교');
});

test('M3: stopping before a conversation starts restores the question and preset',async({page,backend})=>{
  await page.goto('/?doc=m2-paper');await page.locator('#b0001 [data-ask-field=en]').evaluate(el=>{const range=document.createRange();range.setStart(el.firstChild!,0);range.setEnd(el.firstChild!,2);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);});await page.keyboard.press('Control+k');
  const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}),ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),input=ask.getByRole('textbox',{name:'질문',exact:true}),picker=ask.getByLabel('이 논문의 대화');
  for(const phase of [{path:'/settings/ask',method:'PUT',preset:true},{path:'/ask',method:'POST',preset:false}]){
    if(phase.preset)await ask.getByRole('button',{name:'쉽게 설명',exact:true}).click();else await input.fill('  아직 전송되지 않은 질문\n줄바꿈도 유지  ');
    const original=await input.inputValue();let release=()=>{},arrive=()=>{},finish=()=>{};
    const gate=new Promise<void>(resolve=>{release=resolve;}),reached=new Promise<void>(resolve=>{arrive=resolve;}),completed=new Promise<void>(resolve=>{finish=resolve;});
    const pattern='**'+phase.path;
    await page.route(pattern,async route=>{
      if(new URL(route.request().url()).pathname!==phase.path||route.request().method()!==phase.method){await route.continue();return;}
      arrive();await gate;try{await route.abort();}finally{finish();}
    });
    await ask.getByRole('button',{name:'질문 보내기',exact:true}).click();await reached;
    if(!phase.preset){await expect(input).toHaveValue('');await expect(ask.locator('.ask-message.user')).toHaveCount(1);}
    await ask.getByRole('button',{name:'응답 중지',exact:true}).click();release();await completed;
    await expect(input).toBeEnabled();await expect(input).toHaveValue(original);await expect(ask.locator('.ask-message')).toHaveCount(0);await expect(ask.getByText('응답을 중지했습니다.',{exact:true})).toBeVisible();
    if(phase.preset)await expect(ask.getByRole('button',{name:'쉽게 설명',exact:true})).toHaveAttribute('aria-pressed','true');
    await page.unroute(pattern);await page.reload();await dock.click();await expect(picker).toBeEnabled();await expect(input).toHaveValue(original);await expect(ask.locator('.selected-quote')).toHaveText('We');await expect(ask.locator('.ask-message')).toHaveCount(0);
    if(phase.preset)await expect(ask.getByRole('button',{name:'쉽게 설명',exact:true})).toHaveAttribute('aria-pressed','true');
  }
  const threads=await fetch(`http://127.0.0.1:${backend.port}/threads?doc_id=m2-paper`,{headers:{Authorization:`Bearer ${backend.token}`}}).then(r=>r.json());expect(threads).toHaveLength(0);
});

test('M3: conversation titles edit with double click and F2 while streams and drafts stay independent',async({page,backend},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true});await dock.click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),picker=ask.getByLabel('이 논문의 대화'),question=ask.getByRole('textbox',{name:'질문',exact:true});
  const title=()=>ask.getByRole('button',{name:/^대화 제목:/});
  const rename=async(value:string,double=false)=>{if(double)await title().dblclick();else await title().press('F2');const input=ask.getByRole('textbox',{name:'대화 제목',exact:true});await input.fill(value);await input.press('Enter');await expect(title()).toHaveText(value);};
  await rename('첫 대화',true);await question.fill('동시 생성 A');await ask.getByRole('button',{name:'질문 보내기'}).click();
  await expect(ask.locator('.ask-message.assistant')).toContainText('동시 생성 A 시작');await expect(picker).toBeEnabled();
  const a=await picker.inputValue();await rename('방법 이해');
  await expect(picker.locator('option:checked')).toContainText('생성 중');
  await ask.getByRole('button',{name:'새 대화',exact:true}).click();await rename('결과 비교');
  const draftB=await picker.inputValue();await question.fill('동시 생성 B');
  await picker.selectOption(a);await expect(ask.getByRole('button',{name:'응답 중지'})).toBeVisible();
  await picker.selectOption(draftB);await expect(question).toHaveValue('동시 생성 B');
  await ask.getByRole('button',{name:'질문 보내기'}).click();await expect(ask.locator('.ask-message.assistant')).toContainText('동시 생성 B 시작');
  const b=await picker.inputValue();expect(b).not.toBe(a);await expect(ask.getByText('다른 대화 1개에서 답변 생성 중',{exact:true})).toBeVisible();
  await expect(ask.locator('.ask-message.assistant')).not.toContainText('동시 생성 A');
  await page.screenshot({path:info.outputPath('conversation-switch-generating-light.png'),animations:'disabled'});
  await picker.selectOption(a);await expect(title()).toHaveText('방법 이해');await expect(ask.locator('.ask-message.assistant')).toContainText('동시 생성 A');await expect(ask.locator('.ask-message.assistant')).not.toContainText('동시 생성 B');
  await picker.selectOption(b);await ask.getByRole('button',{name:'응답 중지'}).click();await expect(question).toBeEnabled();await expect(ask.getByText('응답을 중지했습니다.',{exact:true})).toBeVisible();await question.fill('보류한 질문 B');
  await picker.selectOption(a);await expect(ask.getByRole('button',{name:'응답 중지'})).toBeVisible();
  await writeFile(join(backend.dataDir,'ask-release-A'),'release');
  await expect(ask.locator('.ask-message.assistant')).toContainText('동시 생성 A 완료');await expect(ask.getByRole('button',{name:'응답 중지'})).toHaveCount(0);
  await question.fill('보류한 질문 A');await title().press('F2');await ask.getByRole('textbox',{name:'대화 제목',exact:true}).fill('취소할 제목');await page.keyboard.press('Escape');await expect(title()).toHaveText('방법 이해');await expect(ask).toBeVisible();
  const headers={Authorization:`Bearer ${backend.token}`};
  await expect.poll(()=>fetch(`http://127.0.0.1:${backend.port}/threads/${b}`,{headers}).then(r=>r.json()).then(t=>t.messages.at(-1).status)).toBe('cancelled');
  await page.reload();await dock.click();await expect(title()).toHaveText('방법 이해');await expect(question).toHaveValue('보류한 질문 A');
  await picker.selectOption(b);await expect(title()).toHaveText('결과 비교');await expect(question).toHaveValue('보류한 질문 B');await expect(ask.locator('.ask-message.assistant')).not.toContainText('동시 생성 A');
  await setReaderTheme(page,'dark');await page.setViewportSize({width:390,height:844});await title().press('F2');
  await expect(ask.getByRole('textbox',{name:'대화 제목',exact:true})).toHaveValue('결과 비교');await page.screenshot({path:info.outputPath('conversation-title-390-dark.png'),animations:'disabled'});
  await page.keyboard.press('Escape');expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);expect(errors).toEqual([]);
});

test('M3: figure context menu starts a fresh image conversation and keeps existing drafts',async({page},info)=>{
  const errors:string[]=[],sent:{thread_id:string|null;thread_title:string;include_image:boolean;anchor:{block_id:string;field:string;text:string}}[]=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/ask')sent.push(r.postDataJSON());});
  await page.addInitScript(()=>localStorage.setItem('paperduet-window-ask',JSON.stringify({x:880,y:90,width:540,height:650})));
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?doc=m2-paper');
  const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true});await dock.click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),input=ask.getByRole('textbox',{name:'질문',exact:true}),picker=ask.getByLabel('이 논문의 대화');
  await input.fill('이전에 작성하던 질문');const original=await picker.inputValue();
  const figure=page.locator('#b0005 .crop-open');await page.locator('#b0005').scrollIntoViewIfNeeded();await expect(figure.getByRole('img')).toBeVisible();
  const menu=page.getByRole('menu',{name:'그림 메뉴',exact:true}),openMenu=()=>figure.click({button:'right',position:{x:60,y:60}});
  await openMenu();await expect(menu).toBeVisible();await expect(menu.getByRole('menuitem',{name:'Ask AI',exact:true})).toBeFocused();expect(sent).toHaveLength(0);
  await page.screenshot({path:info.outputPath('figure-ask-menu-light.png'),animations:'disabled'});
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(ask).toBeVisible();await expect(input).toHaveValue('이전에 작성하던 질문');
  await openMenu();await page.mouse.click(20,150);await expect(menu).toHaveCount(0);
  await openMenu();await page.mouse.wheel(0,60);await expect(menu).toHaveCount(0);
  await openMenu();await menu.getByRole('menuitem',{name:'Ask AI',exact:true}).click();
  await expect(ask).toContainText('선택한 그림');await expect(ask.locator('.selected-quote')).toHaveText('Figure 1');await expect(ask.getByRole('button',{name:'대화 제목: Figure 1',exact:true})).toBeVisible();
  await expect(input).toHaveValue('');await expect(ask.locator('.ask-message')).toHaveCount(0);expect(sent).toHaveLength(0);
  const draft=await picker.inputValue();expect(draft).not.toBe(original);await input.fill('이 그림의 흐름을 설명해 줘');
  await picker.selectOption(original);await expect(input).toHaveValue('이전에 작성하던 질문');await picker.selectOption(draft);await expect(input).toHaveValue('이 그림의 흐름을 설명해 줘');
  await ask.getByText('전송 컨텍스트 확인',{exact:true}).click();await expect(ask.getByLabel('원본 이미지 첨부')).toBeChecked();await expect(ask.locator('.context-debug pre')).toContainText('"attached": true');
  await ask.getByRole('button',{name:'질문 보내기',exact:true}).click();await expect(ask.locator('.ask-message.assistant')).toContainText('입력 123');
  expect(sent).toHaveLength(1);expect(sent[0]).toMatchObject({thread_id:null,thread_title:'Figure 1',include_image:true,anchor:{block_id:'b0005',field:'caption_en',text:''}});
  await figure.focus();await figure.press('Shift+F10');await expect(menu).toBeVisible();await menu.getByRole('menuitem',{name:'Ask AI',exact:true}).press('Enter');
  await expect(ask.locator('.selected-quote')).toHaveText('Figure 1');await expect(ask.locator('.ask-message')).toHaveCount(0);await expect(input).toHaveValue('');expect(sent).toHaveLength(1);expect(errors).toEqual([]);
});

test('M3: enlarged figure menu stays above the viewer and fits dark narrow screens',async({page},info)=>{
  const errors:string[]=[];let sent=0;page.on('pageerror',error=>errors.push(error.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/ask')sent++;});
  await page.setViewportSize({width:390,height:844});await page.goto('/?doc=m2-paper');await setReaderTheme(page,'dark');
  const figure=page.locator('#b0005 .crop-open');await page.locator('#b0005').scrollIntoViewIfNeeded();await figure.click();
  const viewer=page.getByRole('dialog',{name:'Figure 1 확대 보기',exact:true}),picture=viewer.getByRole('img'),menu=page.getByRole('menu',{name:'그림 메뉴',exact:true});await expect(picture).toBeVisible();
  const box=(await picture.boundingBox())!;await picture.click({button:'right',position:{x:box.width-3,y:box.height-3}});await expect(menu).toBeVisible();
  const popup=(await menu.boundingBox())!;expect(popup.x).toBeGreaterThanOrEqual(8);expect(popup.y).toBeGreaterThanOrEqual(8);expect(popup.x+popup.width).toBeLessThanOrEqual(382);expect(popup.y+popup.height).toBeLessThanOrEqual(836);
  await expect(menu).toHaveCSS('background-color',await viewer.evaluate(el=>getComputedStyle(el).backgroundColor));await page.screenshot({path:info.outputPath('figure-ask-menu-expanded-390-dark.png'),animations:'disabled'});
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(viewer).toBeVisible();
  const viewport=viewer.getByRole('region',{name:'확대 이미지 스크롤',exact:true});await viewport.focus();await viewport.press('Shift+F10');await expect(menu).toBeVisible();await menu.getByRole('menuitem',{name:'Ask AI',exact:true}).press('Enter');
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(viewer).toHaveCount(0);await expect(ask).toBeVisible();await expect(ask.locator('.selected-quote')).toHaveText('Figure 1');await expect(ask.getByRole('textbox',{name:'질문',exact:true})).toBeFocused();
  expect(await page.evaluate(()=>document.body.style.overflow)).toBe('');expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);expect(sent).toBe(0);expect(errors).toEqual([]);
});

test('M3: selection Ask AI follows the text, waits for release and fits viewport edges',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1440,height:960});await page.emulateMedia({reducedMotion:'reduce'});await page.goto('/?doc=m2-paper');
  const first=page.locator('#b0001 [data-ask-field=en]'),menu=page.getByRole('toolbar',{name:'선택 텍스트 액션'});
  await first.scrollIntoViewIfNeeded();const line=(await first.boundingBox())!;
  await page.mouse.move(line.x+1,line.y+8);await page.mouse.down();await page.mouse.move(line.x+120,line.y+8,{steps:5});
  await expect(menu).toBeHidden();await page.mouse.up();await expect(menu).toBeVisible();
  const select=async(selector:string,backwards=false)=>page.locator(selector).first().evaluate((el,reverse)=>{const text=el.firstChild!;const s=window.getSelection()!;s.setBaseAndExtent(text,reverse?(text.textContent?.length??0):0,text,reverse?0:(text.textContent?.length??0));},backwards);
  const selectedRect=()=>page.evaluate(()=>{const range=window.getSelection()!.getRangeAt(0);const rect=Array.from(range.getClientRects()).filter(r=>r.width&&r.height).at(-1)!;return {top:rect.top,bottom:rect.bottom,left:rect.left,right:rect.right};});
  await expect(menu).toHaveAttribute('data-placement','below');
  await expect.poll(async()=>Math.abs((await menu.boundingBox())!.y-(await selectedRect()).bottom-8)).toBeLessThan(2);
  const initial=(await menu.boundingBox())!;await page.evaluate(()=>window.scrollBy(0,60));
  await expect.poll(async()=>Math.abs((await menu.boundingBox())!.y-initial.y+60)).toBeLessThan(2);
  await page.screenshot({path:info.outputPath('selection-menu-below-light.png'),animations:'disabled'});
  await page.mouse.click(1400,850);await expect(menu).toBeHidden();
  await select('#b0001 [data-ask-field=en]');await expect(menu).toBeVisible();
  await page.evaluate(()=>window.scrollTo(0,1000));await expect(menu).toBeHidden();
  await page.evaluate(()=>window.scrollTo(0,0));await expect(menu).toBeVisible();
  await setReaderTheme(page,'dark');await page.setViewportSize({width:390,height:844});
  const last=page.locator('#b0007 [data-ask-field=en]').first();await last.scrollIntoViewIfNeeded();
  await last.evaluate(el=>{const r=el.getBoundingClientRect();window.scrollBy(0,r.bottom-(innerHeight-12));});
  await select('#b0007 [data-ask-field=en]',true);await expect(menu).toBeVisible();await expect(menu).toHaveAttribute('data-placement','above');
  const end=await selectedRect(),box=(await menu.boundingBox())!;
  expect(box.y+box.height).toBeLessThanOrEqual(end.top-6);expect(box.x).toBeGreaterThanOrEqual(8);expect(box.x+box.width).toBeLessThanOrEqual(382);
  await page.screenshot({path:info.outputPath('selection-menu-above-390-dark.png'),animations:'disabled'});
  await menu.getByRole('button').click();const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});
  await expect(ask.locator('.selected-quote')).toHaveText('[1] Reference 2025.');await expect(menu).toBeHidden();await expect(ask.getByRole('button',{name:'질문 보내기',exact:true})).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);expect(errors).toEqual([]);
});

test('M3: paper summary tab makes, checks, edits and links the summary',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  const tabs=page.getByRole('tablist',{name:'보기'});await tabs.getByRole('tab',{name:'요약'}).click();
  const view=page.getByRole('region',{name:'논문 요약'});
  await expect(view).toContainText('예상 · AI 호출 3회');await view.getByRole('button',{name:'요약 만들기'}).click();
  await expect(view.getByRole('heading',{name:'구조화 요약'})).toBeVisible();
  await expect(view.locator('.summary-field.kind-key')).toContainText('Nova가 풀려는 검출 문제를 다룬다.');
  await expect(view.locator('.summary-field.kind-res .qa-label')).toHaveCount(0);
  await expect(view.locator('.summary-field.kind-res')).toContainText('99.9');
  await expect(view.locator('.summary-step')).toHaveCount(2);await expect(view.locator('.summary-visual')).toContainText('핵심 수치를 담은 표');
  await page.screenshot({path:info.outputPath('summary-desktop.png'),fullPage:true});
  // An evidence chip returns to the paper at that block.
  await view.locator('.summary-field.kind-res .summary-ref').first().click();
  await expect(tabs.getByRole('tab',{name:'본문'})).toHaveAttribute('aria-selected','true');await expect(page.locator('#b0003')).toBeInViewport();
  await tabs.getByRole('tab',{name:'요약'}).click();
  // Edit, keep across reloads, send to the presentation notes, and ask about a point.
  const result=view.locator('.summary-field.kind-res .summary-point').last();
  await result.getByRole('button',{name:'고치기'}).click();await result.getByRole('textbox').fill('Nova는 AP 42.0으로 가장 높다.');await result.getByRole('button',{name:'저장'}).click();
  await expect(result).toContainText('직접 수정');await expect(result.locator('.qa-label')).toHaveCount(0);
  await result.getByRole('button',{name:'메모장에 추가'}).click();await expect(view.getByRole('status')).toContainText('메모장에 추가했습니다');
  await expect(view.getByRole('button',{name:'AI에게 질문'})).toHaveCount(0);
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(panel).toContainText('질문 대상 · 이 논문');
  await panel.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await page.reload();await page.getByRole('tablist',{name:'보기'}).getByRole('tab',{name:'요약'}).click();
  await expect(page.getByRole('region',{name:'논문 요약'})).toContainText('직접 수정함');
  await page.getByRole('button',{name:'다시 만들기'}).click();await expect(page.getByRole('alert')).toContainText('직접 고친 내용이 사라집니다');
  expect(errors).toEqual([]);
});

test('M3: dark 390px question overlay, focus and no horizontal overflow',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});await page.goto('/?doc=m2-paper');
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Ask AI',exact:true});await panel.getByRole('textbox',{name:'질문',exact:true}).fill('이 논문의 핵심은?');await panel.getByRole('button',{name:'질문 보내기',exact:true}).click();
  await expect(panel.locator('.katex')).toHaveCount(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  expect(await panel.evaluate(el=>el.scrollWidth-el.clientWidth)).toBe(0);
  await page.screenshot({path:info.outputPath('m3-ask-390-dark.png')});await page.keyboard.press('Escape');await expect(panel).toBeHidden();expect(errors).toEqual([]);
});

test('M3: VisualAD original page preview and corrected figure/equation extraction',async({page},info)=>{
  const pdf=process.env.PAPERDUET_VISUALAD_PDF||'artifacts/m3/visualad.pdf';test.skip(!existsSync(pdf),'VisualAD PDF unavailable');
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:390,height:844});await page.goto('/?library=1');
  await page.getByLabel('PDF 파일',{exact:true}).setInputFiles(pdf);const progress=page.getByRole('dialog',{name:'논문 처리'});
  await expect(progress.getByRole('button',{name:'원문·대역 읽기'})).toBeEnabled({timeout:60000});await progress.getByRole('button',{name:'원문·대역 읽기'}).click();
  const preview=page.getByRole('dialog',{name:'원본 PDF 보기'});await expect(preview.getByRole('img',{name:'원본 PDF 1쪽'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.screenshot({path:info.outputPath('m3-visualad-original.png')});await preview.getByRole('button',{name:'다음 페이지'}).click();
  await expect(preview.getByRole('img',{name:'원본 PDF 2쪽'})).toBeVisible();await preview.getByRole('button',{name:'추출 텍스트 보기'}).click();
  await expect(page.locator('.figure-block')).toHaveCount(6);await expect(page.locator('.equation-card')).toHaveCount(13);await expect(page.locator('.table-block')).toHaveCount(5);
  await page.locator('.figure-block').first().scrollIntoViewIfNeeded();await page.getByRole('button',{name:'Figure 1 원본 확대',exact:true}).click();await expect(page.getByRole('dialog',{name:'Figure 1 확대 보기'}).getByRole('img')).toBeVisible();
  await page.screenshot({path:info.outputPath('m3-visualad-figure.png')});expect(errors).toEqual([]);
});
test('M4: standalone HTML works offline at 390px, all modes, math, search and dark theme',async({page,backend},info)=>{
  test.setTimeout(120000);
  const response=await fetch(`http://127.0.0.1:${backend.port}/documents/rex-omni/export?format=html`,{headers:{Authorization:`Bearer ${backend.token}`}});
  expect(response.status).toBe(200);const file=info.outputPath('rex-omni-offline.html');await writeFile(file,await response.text());
  const errors:string[]=[];const network:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>{if(/^https?:/.test(r.url()))network.push(r.url());});
  await page.context().setOffline(true);await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});
  await page.goto(pathToFileURL(file).href);await expect(page.locator('[data-block]')).toHaveCount(285);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await expect(page.locator('.table-block')).toHaveCount(17);await expect(page.locator('.equation-card .katex')).toHaveCount(5);
  await expect(page.getByRole('button',{name:'Ask AI',exact:true})).toHaveCount(0);
  for(const mode of ['원문','대역','번역']){await page.getByRole('button',{name:mode,exact:true}).click();await expect(page.getByRole('button',{name:mode,exact:true})).toHaveAttribute('aria-pressed','true');}
  await page.getByRole('button',{name:'목차 열기'}).click();await expect(page.getByRole('navigation',{name:'목차'})).toHaveClass(/open/);
  await page.locator('.toc').getByRole('link',{name:'7 결론',exact:true}).click();await expect(page.locator('.toc')).toHaveClass(/open/);
  await page.getByRole('button',{name:'목차 열기'}).click();await expect(page.locator('.toc')).not.toHaveClass(/open/);
  await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'맨 위로'}).click();await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'문서 검색',exact:true}).click();await page.getByRole('textbox',{name:'문서 검색'}).fill('Rex-Omni');await expect(page.locator('.search-panel [role=status]')).not.toHaveText('0건');
  await page.getByRole('button',{name:'검색 닫기'}).click();await page.screenshot({path:info.outputPath('export-390-dark.png')});
  await page.setViewportSize({width:1440,height:960});expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  expect(network).toEqual([]);expect(errors).toEqual([]);
});

test('M4: answers become personal memos that edit, reload and export',async({page})=>{
  await page.goto('/?doc=m2-paper');await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await ask.getByRole('textbox',{name:'질문',exact:true}).fill('이 논문의 핵심은?');await ask.getByRole('button',{name:'질문 보내기'}).click();await expect(ask.getByRole('button',{name:'메모장에 추가'})).toBeVisible();
  await ask.getByRole('button',{name:'메모장에 추가'}).click();await expect(ask).toContainText('메모장에 추가했습니다.');await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await page.getByRole('button',{name:'메모장',exact:true}).click();const notes=page.getByRole('dialog',{name:'메모장',exact:true});
  await notes.getByLabel('내 메모').selectOption({label:'논문 메모'});await expect(notes.getByLabel('메모 내용')).toContainText('42.0');
  await notes.getByLabel('메모 제목').fill('핵심 정리');await notes.getByLabel('메모 내용').fill('논문 명시: 결과는 42.0이다.\n\n$$x=42.0$$');await notes.getByRole('button',{name:'메모 저장',exact:true}).click();
  await expect(notes.getByLabel('내 메모').locator('option:checked')).toHaveText('핵심 정리');
  await notes.getByRole('button',{name:'새 메모',exact:true}).click();await notes.getByLabel('메모 제목').fill('다음 장');await notes.getByLabel('메모 내용').fill('직접 작성한 메모');await notes.getByRole('button',{name:'메모 저장',exact:true}).click();
  await expect(notes.getByLabel('내 메모').locator('option')).toHaveCount(3);
  await notes.getByRole('button',{name:'메모장 닫기',exact:true}).click();await page.reload();await page.getByRole('button',{name:'메모장',exact:true}).click();await notes.getByLabel('내 메모').selectOption({label:'다음 장'});await expect(notes.getByLabel('메모 내용')).toHaveValue('직접 작성한 메모');
  await notes.getByRole('button',{name:'메모장 닫기',exact:true}).click();await page.getByRole('button',{name:'내보내기',exact:true}).click();const exp=page.getByRole('dialog',{name:'논문 내보내기'});await exp.getByLabel('파일 형식').selectOption('presentation');await exp.getByRole('button',{name:'저장 위치 선택'}).click();await expect(exp.getByRole('status')).toContainText('저장했습니다');
});

test('M4: standalone exported figures remain embedded and settings explain local updates',async({page,backend},info)=>{
  const response=await fetch(`http://127.0.0.1:${backend.port}/documents/m2-paper/export?format=html`,{headers:{Authorization:`Bearer ${backend.token}`}});const file=info.outputPath('figure-offline.html');await writeFile(file,await response.text());
  await page.goto(pathToFileURL(file).href);await page.locator('.figure-block').first().scrollIntoViewIfNeeded();await expect(page.getByRole('button',{name:'Figure 1 원본 확대',exact:true})).toBeVisible();await page.getByRole('button',{name:'Figure 1 원본 확대',exact:true}).click();await expect(page.getByRole('dialog',{name:'Figure 1 확대 보기'}).getByRole('img')).toBeVisible();
  await page.goto('/?library=1');await page.getByRole('button',{name:'앱 설정',exact:true}).click();const settings=page.getByRole('dialog',{name:'앱 설정'});await expect(settings).toContainText('0.5.0');await expect(settings).toContainText('배포 경로가 연결되지 않았습니다');await expect(settings.getByRole('button',{name:'새 저장 폴더 선택'})).toBeVisible();
});

test('M4: arXiv input uses HTML tables and math, with exact selection after inline math',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.setViewportSize({width:390,height:844});await page.goto('/?library=1');
  await page.getByRole('button',{name:'논문 추가',exact:true}).click();
  await page.getByLabel('arXiv ID 또는 URL').fill('https://arxiv.org/abs/2603.07952v1');await page.getByRole('button',{name:'가져오기',exact:true}).click();
  const progress=page.getByRole('dialog',{name:'논문 처리'});await progress.getByRole('button',{name:'원문·대역 읽기'}).click();
  await expect(page.getByRole('heading',{name:'Synthetic Paper',exact:true})).toBeVisible();await expect(page.getByRole('dialog',{name:'원본 PDF 보기'})).toHaveCount(0);
  await expect(page.locator('.equation-card .katex')).toHaveCount(1);await expect(page.locator('.table-block th').first()).toHaveAttribute('rowspan','2');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  const paragraph=page.locator('[data-ask-field=en]').filter({has:page.locator('[data-source-text]')}).first();
  await paragraph.evaluate(el=>{const node=el.lastChild!;const range=document.createRange();range.selectNodeContents(node);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);});
  await page.keyboard.press('Control+k');const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(ask.locator('.selected-quote')).toContainText('and original text.');await ask.getByRole('textbox',{name:'질문',exact:true}).fill('이 문장을 설명해 줘');await ask.getByRole('button',{name:'질문 보내기',exact:true}).click();await expect(ask.locator('.ask-message.assistant .katex')).toHaveCount(1);
  await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();await page.screenshot({path:info.outputPath('arxiv-390.png')});expect(errors).toEqual([]);
});

test('Tutorial: first launch opens guide without marking AI setup complete or sending content',async({page,backend})=>{
  const writes:string[]=[];page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname);});
  await page.goto('/');const welcome=page.getByRole('dialog',{name:'PaperDuet 시작하기'});
  await welcome.getByRole('button',{name:'먼저 사용 가이드 보기'}).click();
  await expect(page.getByRole('heading',{name:'AI 연결',exact:true})).toBeVisible();
  await expect(welcome).toHaveCount(0);await expect(page.locator('.tutorial-page input[type=password]')).toHaveCount(0);
  await page.getByText('공식 CLI 로그인 명령 보기',{exact:true}).click();await expect(page.locator('.tutorial-cli code')).toHaveText(['claude auth login','codex login']);
  await page.getByRole('button',{name:'AI 연결 설정 열기',exact:true}).click();await expect(page.getByRole('dialog',{name:'AI 연결 설정',exact:true})).toBeVisible();await page.keyboard.press('Escape');
  const status=await fetch(`http://127.0.0.1:${backend.port}/settings/onboarding`,{headers:{Authorization:`Bearer ${backend.token}`}}).then(r=>r.json());
  expect(status.completed).toBe(false);expect(writes.filter(path=>!path.endsWith('/position'))).toEqual([]);
  await page.reload();await expect(page.getByRole('heading',{name:'AI 연결',exact:true})).toBeVisible();await expect(welcome).toHaveCount(0);
});

test('Tutorial: seven focused topics at 390px, both themes and progress survive reload',async({page},info)=>{
  const errors:string[]=[];const writes:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname);});
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'light'});await page.goto('/?tutorial=connect');
  const scenes=['connect','import','ask','summary','memo','export','settings'];
  await expect(page.getByLabel('설명 선택').locator('option')).toHaveCount(7);
  for(const theme of ['light','dark']){
    while(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'가이드 테마 변경'}).click();
    for(const id of scenes){
      await page.getByLabel('설명 선택').selectOption(id);await expect(page.locator('.tutorial-flow')).toBeVisible();
      await expect(page.locator('.tutorial-steps>li')).toHaveCount(3);
      await expect(page.locator('.tutorial-preview,.tutorial-check,.tutorial-tip')).toHaveCount(0);
      expect(await page.evaluate(()=>Math.max(0,document.documentElement.scrollWidth-innerWidth))).toBe(0);
      await page.screenshot({path:info.outputPath(`guide-${id}-${theme}-390.png`),fullPage:true,animations:'disabled'});
    }
  }
  await page.getByLabel('설명 선택').selectOption('memo');await page.getByRole('button',{name:'다음',exact:true}).click();
  await expect(page.getByLabel('설명 선택')).toHaveValue('export');await page.reload();await expect(page.getByLabel('설명 선택')).toHaveValue('export');
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('paperduet-tutorial-v1')!).done)).toContain('memo');
  expect(writes).toEqual([]);expect(errors).toEqual([]);
});

test('Tutorial: library and reader entry points, browser history, real settings and sample exit',async({page},info)=>{
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?library=1');await page.getByRole('button',{name:'사용 가이드',exact:true}).click();
  await expect(page.getByRole('heading',{name:'AI 연결',exact:true})).toBeVisible();await page.screenshot({path:info.outputPath('guide-desktop-light.png'),fullPage:true,animations:'disabled'});
  await expect(page.getByRole('navigation',{name:'가이드 설명'}).getByRole('button')).toHaveCount(7);
  await page.getByRole('button',{name:'다음',exact:true}).click();await expect(page).toHaveURL(/tutorial=import/);await page.goBack();await expect(page.getByRole('heading',{name:'AI 연결',exact:true})).toBeVisible();
  await page.getByRole('navigation',{name:'가이드 설명'}).getByRole('button',{name:'7 저장',exact:true}).click();await page.getByRole('button',{name:'저장 위치 확인하기',exact:true}).click();await expect(page.getByRole('dialog',{name:'앱 설정'})).toBeVisible();await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'가이드 테마 변경'}).click();await page.screenshot({path:info.outputPath('guide-desktop-dark.png'),fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'서재로 시작하기',exact:true}).click();await expect(page.getByRole('heading',{name:'나의 논문 서재'})).toBeVisible();await page.getByRole('button',{name:'사용 가이드',exact:true}).click();await expect(page.getByRole('heading',{name:'저장',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'서재로',exact:true}).click();await expect(page.getByRole('heading',{name:'나의 논문 서재'})).toBeVisible();
  await page.goto('/?doc=rex-omni');await page.getByRole('button',{name:'읽기 설정',exact:true}).click();await page.getByRole('button',{name:'사용 가이드',exact:true}).click();await expect(page.getByRole('heading',{name:'저장',exact:true})).toBeVisible();
});

test('Tutorial: previous topics and saved progress map to the seven-topic guide',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('paperduet-tutorial-v1',JSON.stringify({last:'review',done:['read','translate','review','export','removed','constructor']})));
  await page.goto('/?library=1');await page.getByRole('button',{name:'사용 가이드',exact:true}).click();await expect(page).toHaveURL(/tutorial=ask/);
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('paperduet-tutorial-v1')!).done)).toEqual(['import','ask','export']);
  await page.goto('/?tutorial=translate');await expect(page).toHaveURL(/tutorial=import/);await expect(page.getByRole('heading',{name:'논문 가져오기',exact:true})).toBeVisible();
  await page.goto('/?tutorial=constructor');await expect(page).toHaveURL(/tutorial=connect/);await expect(page.getByRole('heading',{name:'AI 연결',exact:true})).toBeVisible();
});

test('M3: continuous scroll preserves position, TOC toggles and floating tools move without reflow',async({page,backend},info)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?doc=rex-omni');
  await expect(page.locator('[data-block]')).toHaveCount(285);
  await expect(page.locator('.book-pagination,.book-columns')).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight)).toBe(true);
  await expect(page.getByRole('navigation',{name:'목차'})).toHaveCount(0);
  const menu=page.getByRole('button',{name:'목차 열기',exact:true});await menu.click();await expect(menu).toHaveAttribute('aria-expanded','true');await menu.click();await expect(menu).toHaveAttribute('aria-expanded','false');
  await page.locator('#b0010').scrollIntoViewIfNeeded();await page.mouse.wheel(0,200);await expect.poll(()=>page.evaluate(()=>scrollY)).toBeGreaterThan(400);
  await page.screenshot({path:info.outputPath('reader-light.png'),animations:'disabled'});
  const viewport=await page.locator('.book-viewport').boundingBox();
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(ask).toBeVisible();await page.screenshot({path:info.outputPath('reader-ask.png'),animations:'disabled'});
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'메모장',exact:true}).click();
  const memo=page.getByRole('dialog',{name:'메모장',exact:true});await expect(memo).toBeVisible();
  const before=(await memo.boundingBox())!;const grip=(await memo.getByRole('button',{name:'메모장 창 이동'}).boundingBox())!;
  await page.mouse.move(grip.x+30,grip.y+15);await page.mouse.down();await page.mouse.move(grip.x-150,grip.y-45,{steps:5});await page.mouse.up();
  const moved=(await memo.boundingBox())!;expect(moved.x).toBeLessThan(before.x-100);expect(moved.y).toBeLessThan(before.y);
  const handle=(await memo.getByRole('button',{name:'메모장 창 크기 조절'}).boundingBox())!;
  await page.mouse.move(handle.x+10,handle.y+10);await page.mouse.down();await page.mouse.move(handle.x+100,handle.y+60,{steps:5});await page.mouse.up();
  expect((await memo.boundingBox())!.width).toBeGreaterThan(moved.width+70);
  expect(await page.locator('.book-viewport').boundingBox()).toEqual(viewport);
  await page.screenshot({path:info.outputPath('reader-floating-tools.png'),animations:'disabled'});
  await memo.getByRole('button',{name:'메모장 닫기',exact:true}).click();await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await setReaderTheme(page,'dark');await page.screenshot({path:info.outputPath('reader-dark.png'),animations:'disabled'});
  const auth={Authorization:`Bearer ${backend.token}`};
  await expect.poll(()=>fetch(`http://127.0.0.1:${backend.port}/documents/rex-omni`,{headers:auth}).then(r=>r.json()).then(d=>d.reading_position.block_id)).not.toBeNull();
  await page.reload();await expect.poll(()=>page.evaluate(()=>scrollY)).toBeGreaterThan(400);
  const saved=await fetch(`http://127.0.0.1:${backend.port}/documents/rex-omni`,{headers:auth}).then(r=>r.json());
  await fetch(`http://127.0.0.1:${backend.port}/documents/rex-omni/position`,{method:'PATCH',headers:{...auth,'Content-Type':'application/json'},body:JSON.stringify({block_id:saved.reading_position.block_id,offset:-1})});
  await page.goto('/?doc=rex-omni');await expect(page.locator(`#${saved.reading_position.block_id}`)).toBeInViewport();
  for(const width of [390,720,1440]){await page.setViewportSize({width,height:900});await expect(page.locator('.reading-flow')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);}
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:info.outputPath('reader-390-dark.png'),animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight)).toBe(true);
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'메모장',exact:true}).click();
  const narrow=(await memo.boundingBox())!;expect(narrow.x).toBeGreaterThanOrEqual(0);expect(narrow.x+narrow.width).toBeLessThanOrEqual(390);expect(narrow.y+narrow.height).toBeLessThanOrEqual(844);
  await page.screenshot({path:info.outputPath('reader-memo-390-dark.png'),animations:'disabled'});
  expect(errors).toEqual([]);
});

for(const tool of [{id:'ask',title:'Ask AI',close:'AI 질문 닫기'},{id:'notebook',title:'메모장',close:'메모장 닫기'}]){
  test(`M3: ${tool.title} floating window resizes from all edges and corners with anchored limits`,async({page},info)=>{
    const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(id=>{const key='paperduet-window-'+id;if(!localStorage.getItem(key))localStorage.setItem(key,JSON.stringify({x:380,y:180,width:540,height:600}));},tool.id);
    await page.setViewportSize({width:1440,height:1000});await page.goto('/?doc=m2-paper');
    const dock=page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:tool.title,exact:true});await dock.click();
    const panel=page.getByRole('dialog',{name:tool.title,exact:true}),content=panel.getByRole('textbox',{name:tool.id==='ask'?'질문':'메모 내용',exact:true});
    await expect(panel).toBeVisible();await content.fill('창 크기를 바꿔도 유지할 초안');
    const book=(await page.locator('.book-viewport').boundingBox())!;
    const drag=async(edge:string,dx:number,dy:number)=>{
      const handle=panel.locator(`[data-resize=${edge}]`),box=(await handle.boundingBox())!;
      await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+dx,box.y+box.height/2+dy,{steps:6});await page.mouse.up();
    };
    const same=async(frame:{x:number;y:number;width:number;height:number})=>{const actual=(await panel.boundingBox())!;for(const key of ['x','y','width','height'] as const)expect(actual[key]).toBeCloseTo(frame[key],0);};
    for(const edge of ['n','e','s','w','ne','nw','se','sw']){
      const before=(await panel.boundingBox())!,dx=edge.includes('w')?-40:40,dy=edge.includes('n')?-30:30;
      const cursor=edge==='n'||edge==='s'?'ns-resize':edge==='e'||edge==='w'?'ew-resize':edge==='ne'||edge==='sw'?'nesw-resize':'nwse-resize';
      await expect(panel.locator(`[data-resize=${edge}]`)).toHaveCSS('cursor',cursor);await drag(edge,dx,dy);
      const after=(await panel.boundingBox())!;
      expect(after.x).toBeCloseTo(before.x+(edge.includes('w')?dx:0),0);expect(after.y).toBeCloseTo(before.y+(edge.includes('n')?dy:0),0);
      expect(after.width).toBeCloseTo(before.width+(edge.includes('w')?-dx:edge.includes('e')?dx:0),0);expect(after.height).toBeCloseTo(before.height+(edge.includes('n')?-dy:edge.includes('s')?dy:0),0);
      await drag(edge,-dx,-dy);await same(before);
    }
    const initial=(await panel.boundingBox())!,keyboard=panel.getByRole('button',{name:tool.title+' 창 크기 조절',exact:true});
    await keyboard.focus();await keyboard.press('ArrowRight');await keyboard.press('ArrowDown');await same({...initial,width:initial.width+10,height:initial.height+10});
    await keyboard.press('ArrowLeft');await keyboard.press('ArrowUp');await same(initial);
    await drag('nw',-1000,-1000);await same({x:8,y:8,width:initial.x+initial.width-8,height:initial.y+initial.height-8});
    await drag('se',-1400,-1000);await same({x:8,y:8,width:320,height:280});
    await drag('se',2000,2000);await same({x:8,y:8,width:1424,height:984});
    await drag('nw',2000,2000);await same({x:1112,y:712,width:320,height:280});
    await page.mouse.move(700,500);await same({x:1112,y:712,width:320,height:280});await expect(content).toHaveValue('창 크기를 바꿔도 유지할 초안');
    expect(await page.locator('.book-viewport').boundingBox()).toEqual(book);
    const saved=(await panel.boundingBox())!;await panel.getByRole('button',{name:tool.close,exact:true}).click();await dock.click();await same(saved);
    await page.reload();await dock.click();await same(saved);await expect(content).toHaveValue('창 크기를 바꿔도 유지할 초안');
    await setReaderTheme(page,'dark');await page.setViewportSize({width:390,height:844});if(!await panel.isVisible())await dock.click();
    await drag('nw',-1000,-1000);await same({x:8,y:8,width:374,height:828});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
    await page.screenshot({path:info.outputPath(`floating-${tool.id}-all-edges-390-dark.png`),animations:'disabled'});expect(errors).toEqual([]);
  });
}

test('M3: notebook keeps drafts, saves per paper and retains old presentation notes',async({page,backend})=>{
  const base=`http://127.0.0.1:${backend.port}/documents/m2-paper`,headers={Authorization:`Bearer ${backend.token}`,'Content-Type':'application/json'};
  const legacy=await fetch(base+'/presentation',{method:'POST',headers,body:JSON.stringify({title:'기존 메모',body_md:'기존 내용'})}).then(r=>r.json());
  await page.goto('/?doc=m2-paper');await page.getByRole('button',{name:'메모장',exact:true}).click();
  const memo=page.getByRole('dialog',{name:'메모장',exact:true});await memo.getByLabel('내 메모').selectOption(legacy.id);await expect(memo.getByLabel('메모 내용')).toHaveValue('기존 내용');
  await memo.getByLabel('내 메모').click();await page.keyboard.press('Escape');await expect(memo).toBeVisible();await expect(memo.locator('select:open')).toHaveCount(0);
  await memo.getByRole('button',{name:'새 메모',exact:true}).click();await memo.getByLabel('메모 제목').fill('읽으며 든 생각');await memo.getByLabel('메모 내용').fill('이 결과는 표 1과 함께 비교할 것.');
  await memo.getByRole('button',{name:'메모장 닫기',exact:true}).click();await page.getByRole('button',{name:'메모장',exact:true}).click();await expect(memo.getByLabel('메모 내용')).toHaveValue('이 결과는 표 1과 함께 비교할 것.');
  await memo.getByRole('button',{name:'메모 저장',exact:true}).click();await expect(memo.getByRole('status')).toHaveText('저장했습니다.');
  const saved=await fetch(base+'/memos',{headers}).then(r=>r.json());expect(saved).toHaveLength(2);
  await page.reload();await page.getByRole('button',{name:'메모장',exact:true}).click();await memo.getByLabel('내 메모').selectOption(saved.find((n:{title:string})=>n.title==='읽으며 든 생각').id);await expect(memo.getByLabel('메모 내용')).toHaveValue('이 결과는 표 1과 함께 비교할 것.');
  await page.goto('/?doc=rex-omni');await page.getByRole('button',{name:'메모장',exact:true}).click();await expect(memo.getByLabel('내 메모').locator('option')).toHaveCount(1);await expect(memo.getByLabel('메모 내용')).toHaveValue('');
});

test('M3: reader matches the library theme, settings persist on immediate exit and tools share it',async({page,backend},info)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?library=1');
  const home=await page.locator('.library-page').evaluate(el=>getComputedStyle(el).backgroundColor);
  await page.locator('.book-open').first().click();await expect(page.locator('.book-reader')).toHaveCSS('background-color',home);
  await expect(page.getByRole('button',{name:'글자 크게',exact:true})).toHaveCount(0);
  await page.screenshot({path:info.outputPath('theme-reader-light.png'),animations:'disabled'});
  await page.getByRole('button',{name:'읽기 설정',exact:true}).click();const settings=page.getByRole('dialog',{name:'읽기 설정',exact:true});
  await settings.getByRole('button',{name:'글자 크게',exact:true}).click();await expect(page.locator('html')).toHaveCSS('--fs','17px');
  await page.screenshot({path:info.outputPath('theme-reading-settings.png'),animations:'disabled'});
  await settings.getByRole('button',{name:'다크',exact:true}).click();
  await page.getByRole('link',{name:'PaperDuet · 서재로 이동',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  const auth={Authorization:`Bearer ${backend.token}`};
  await expect.poll(()=>fetch(`http://127.0.0.1:${backend.port}/settings/reader`,{headers:auth}).then(r=>r.json()).then(s=>[s.theme,s.font_size])).toEqual(['dark',17]);
  const dark=await page.locator('.library-page').evaluate(el=>getComputedStyle(el).backgroundColor);
  await page.evaluate(()=>{
    const changes:string[]=[];(window as unknown as {themeChanges:string[]}).themeChanges=changes;
    new MutationObserver(()=>changes.push(document.documentElement.dataset.theme||'')).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  });
  await page.locator('.book-open').first().click();await expect(page.locator('.book-reader')).toHaveCSS('background-color',dark);
  expect(await page.evaluate(()=>(window as unknown as {themeChanges:string[]}).themeChanges)).not.toContain('light');
  await page.screenshot({path:info.outputPath('theme-reader-dark.png'),animations:'disabled'});
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(ask).toHaveCSS('background-color',dark);
  await page.screenshot({path:info.outputPath('theme-ask-dark.png'),animations:'disabled'});
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'메모장',exact:true}).click();
  const memo=page.getByRole('dialog',{name:'메모장',exact:true});await expect(memo).toHaveCSS('background-color',dark);
  await page.screenshot({path:info.outputPath('theme-memo-dark.png'),animations:'disabled'});
  await setReaderTheme(page,'light');await expect(ask).toBeVisible();await expect(memo).toHaveCount(0);
  await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'Ask AI',exact:true}).click();await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'메모장',exact:true}).click();
  await expect(ask).toHaveCSS('background-color',home);await expect(memo).toHaveCSS('background-color',home);
  await memo.getByRole('button',{name:'메모장 닫기',exact:true}).click();await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'읽기 설정',exact:true}).click();
  const box=(await settings.boundingBox())!;expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.screenshot({path:info.outputPath('theme-settings-390.png'),animations:'disabled'});
  await page.keyboard.press('Escape');await expect(settings).toHaveCount(0);await expect(page.getByRole('button',{name:'읽기 설정',exact:true})).toBeFocused();
  expect(errors).toEqual([]);
});


test('M3: outside clicks dismiss dialogs and notebook while Ask AI and contents stay open',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?library=1');
  for(const name of ['논문 추가','앱 설정','AI 연결 설정']){
    const opener=page.getByRole('button',{name,exact:true});await opener.click();
    const modal=page.getByRole('dialog',{name,exact:true});await expect(modal).toBeVisible();
    await modal.getByRole('heading',{name,exact:true}).click();await expect(modal).toBeVisible();
    if(name==='논문 추가'){
      const box=(await modal.boundingBox())!;
      await page.mouse.move(box.x+45,box.y+30);await page.mouse.down();await page.mouse.move(2,2,{steps:4});await page.mouse.up();
      await expect(modal).toBeVisible();
    }
    await page.mouse.click(2,2);await expect(modal).toHaveCount(0);await expect(opener).toBeFocused();
  }
  await page.locator('.book-menu-button').first().click();await page.getByRole('menuitem',{name:'삭제',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'논문 삭제'})).toBeVisible();await page.mouse.click(2,2);
  await expect(page.getByRole('dialog',{name:'논문 삭제'})).toHaveCount(0);await expect(page.locator('.shelf-book')).toHaveCount(2);
  await page.goto('/?doc=m2-paper');const menu=page.getByRole('button',{name:'목차 열기',exact:true}),toc=page.getByRole('navigation',{name:'목차',exact:true});
  await menu.click();await toc.getByRole('link',{name:'1 결과',exact:true}).click();await expect(page).toHaveURL(/#b0002$/);
  await expect(toc).toBeVisible();await page.mouse.click(1420,400);await expect(menu).toHaveAttribute('aria-expanded','true');
  await page.keyboard.press('Control+f');await page.getByRole('textbox',{name:'문서 검색'}).fill('Nova');await page.getByRole('textbox',{name:'문서 검색'}).press('Enter');
  await expect(toc).toBeVisible();await page.getByRole('button',{name:'검색 닫기',exact:true}).click();
  const tools=page.getByRole('toolbar',{name:'보조 기능'}),ask=page.getByRole('dialog',{name:'Ask AI',exact:true}),memo=page.getByRole('dialog',{name:'메모장',exact:true});
  await tools.getByRole('button',{name:'Ask AI',exact:true}).click();await tools.getByRole('button',{name:'메모장',exact:true}).click();
  await memo.getByLabel('메모 제목').fill('바깥 클릭 초안');await memo.getByLabel('메모 내용').fill('창을 닫아도 임시 보관됩니다.');
  await ask.getByRole('textbox',{name:'질문',exact:true}).click({position:{x:15,y:15}});await expect(memo).toBeVisible();await expect(toc).toBeVisible();
  await page.mouse.click(1420,400);await expect(ask).toBeVisible();await expect(memo).toHaveCount(0);await expect(toc).toBeVisible();
  await tools.getByRole('button',{name:'메모장',exact:true}).click();await expect(memo.getByLabel('메모 내용')).toHaveValue('창을 닫아도 임시 보관됩니다.');
  await page.mouse.click(1420,400);await expect(memo).toHaveCount(0);
  await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await page.getByRole('button',{name:'Figure 1 원본 확대',exact:true}).click();const image=page.getByRole('dialog',{name:'Figure 1 확대 보기',exact:true});
  await expect(image.getByRole('img')).toBeVisible();await page.mouse.click(2,2);await expect(image).toHaveCount(0);await expect(toc).toBeVisible();
  await page.screenshot({path:info.outputPath('persistent-toc-light.png'),animations:'disabled'});
  await setReaderTheme(page,'dark');await expect(toc).toBeVisible();
  await page.setViewportSize({width:390,height:844});await page.mouse.click(382,200);await expect(toc).toBeVisible();
  await page.screenshot({path:info.outputPath('persistent-toc-390-dark.png'),animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await menu.click();await expect(toc).toHaveCount(0);await menu.click();await toc.getByRole('button',{name:'목차 닫기'}).click();await expect(toc).toHaveCount(0);
  expect(errors).toEqual([]);
});
