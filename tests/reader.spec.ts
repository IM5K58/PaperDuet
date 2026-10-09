import { test as base, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';
import { existsSync } from 'node:fs';

// M3 uses real FastAPI, context assembly, SQLite and SSE with only AI transport replaced.

const test = base.extend<{ backend: { port: number; token: string; generation: number } }>({
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
      await use({ port, token, generation: 0 });
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

test('M2: V5 review, evidence jump, regeneration and manual correction persist',async({page},testInfo)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  await expect(page.locator('.note')).toHaveCount(6);
  await expect(page.locator('.document-status')).toContainText('검수 필요 1건');
  const result=page.locator('[data-note-kind=res]');const id=await result.getAttribute('id');
  await result.locator('.note-refs a').click();await expect(page).toHaveURL(/#b0001$/);
  const editor=result.locator('..').locator('.note-editor');
  await editor.locator('summary').click();await expect(editor.locator('summary')).toContainText('숫자·근거');
  await page.screenshot({path:testInfo.outputPath('m2-v5-review.png')});
  await editor.getByRole('button',{name:'이 주석 재생성'}).click();
  await expect(page.locator('.document-status')).toContainText('처리 완료');
  await expect(result).toHaveAttribute('id',id!);await expect(result).toContainText('42.0');
  await editor.getByRole('textbox',{name:'주석 내용',exact:true}).fill('결과는 88888이다.');
  await editor.getByRole('button',{name:'주석 수정 저장'}).click();
  await expect(page.locator('.document-status')).toContainText('검수 필요 1건');
  await editor.getByRole('textbox',{name:'주석 내용',exact:true}).fill('결과는 42.0이다.');
  await editor.getByRole('button',{name:'주석 수정 저장'}).click();
  await expect(page.locator('.document-status')).toContainText('처리 완료');
  await page.reload();await expect(page.locator('.note')).toHaveCount(6);
  await expect(page.locator(`#${id}`)).toContainText('직접 편집');
  await expect(page.locator('.document-status')).toContainText('처리 완료');
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
  await expect(equation.locator('.equation-error')).toContainText('수식 문법 검수 필요');
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
  await page.getByRole('button',{name:'추출 텍스트 보기'}).click();
  await expect(page.getByRole('heading',{name:'Two Column Study',exact:true})).toBeVisible();
  await expect(page.locator('body')).toHaveAttribute('data-view','en');
  await page.getByRole('button',{name:'목차 열기'}).click();
  await expect(page.getByRole('navigation',{name:'목차'}).getByRole('link',{name:'1 Introduction'})).toBeVisible();
  await page.locator('.toc').getByRole('button',{name:'목차 닫기'}).click();
  const order=await page.locator('[data-block] .en').allTextContents();
  expect(order.findIndex(t=>t.includes('Left second'))).toBeLessThan(order.findIndex(t=>t.includes('Right first')));
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  await page.getByRole('button',{name:'번역',exact:true}).click();
  await expect(page.getByText('참고문헌 · 원문 유지').first()).toBeVisible();
  await page.reload();await page.getByRole('button',{name:'추출 텍스트 보기'}).click();await expect(page.getByRole('heading',{name:'Two Column Study',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'PaperDuet · 서재로 이동'}).click();
  await page.getByLabel('PDF 파일',{exact:true}).setInputFiles(source);
  await expect(dialog.getByText('AI 연결 필요',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'창 닫기'}).click();
  await expect(page.locator('.paper-item')).toHaveCount(2);
  await page.screenshot({path:testInfo.outputPath('library.png')});
  expect(errors).toEqual([]);
});

test('M1: narrow library, system dark theme, provider settings and keyboard dialog dismissal',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});
  await page.goto('/?library=1');await expect(page.locator('.paper-item')).toHaveCount(1);
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
        while (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: '테마 변경' }).click();
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
        const colors = await page.evaluate(() => ({ bg: getComputedStyle(document.body).backgroundColor, fg: getComputedStyle(document.body).color, scheme: getComputedStyle(document.documentElement).colorScheme }));
        expect(colors.scheme).toBe(theme); expect(colors.bg).not.toBe(colors.fg);
        const outside = await page.locator('[data-block]').evaluateAll(blocks => blocks.filter(b => b.getBoundingClientRect().right > document.documentElement.clientWidth + 1).map(b => b.id));
        expect(outside).toEqual([]);
      }
    }
    await page.getByRole('button', { name: '대역', exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath(`reader-${width}-dark.png`) });
    await page.getByRole('button', { name: '테마 변경' }).click(); // dark -> light
    await page.screenshot({ path: testInfo.outputPath(`reader-${width}-light.png`) });
    expect(external).toEqual([]); expect(errors).toEqual([]);
  });
}

test('TOC, scroll spy, progress, local table scrolling, note filters and keyboard search', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.goto('/?doc=rex-omni');
  await expect(page.locator('[data-block]')).toHaveCount(285);
  const table = page.getByRole('region', { name: 'Table 2 가로 스크롤', exact: true });
  await page.getByRole('navigation', { name: '목차' }).getByRole('link', { name: '5.1 일반 객체 검출' }).click();
  await expect(page.locator('.toc a[aria-current=location]')).toContainText('일반 객체 검출');
  expect(Number(await page.getByRole('progressbar').getAttribute('aria-valuenow'))).toBeGreaterThan(0);
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
});

test('font, view, theme and reading position survive reload; system dark mode and six note colors', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 }); await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/?doc=rex-omni');
  await expect(page.locator('[data-block]')).toHaveCount(285);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe('dark');
  const colors = await page.locator('.note').evaluateAll(notes => Object.fromEntries(notes.map(n => [n.getAttribute('data-note-kind'), getComputedStyle(n).borderLeftColor])));
  expect(new Set(Object.values(colors)).size).toBe(6);
  await page.getByRole('button', { name: '글자 크게' }).click();
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
  await panel.getByRole('button',{name:'답변 재생성',exact:true}).click();await expect(panel.locator('.ask-message.assistant')).toHaveCount(3);
  await expect(panel.locator('.ask-message.assistant').last()).toContainText('입력 123');
  await panel.getByRole('button',{name:'Markdown 복사',exact:true}).last().click();expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('$$x=42.0$$');
  await panel.getByRole('button',{name:'평문 복사',exact:true}).last().click();expect(await page.evaluate(()=>navigator.clipboard.readText())).not.toContain('**논문 명시**');
  await panel.getByRole('button',{name:'주석으로 저장',exact:true}).last().click();await expect(page.locator('.ai-answer-badge')).toHaveCount(1);await expect(page.locator('.note .katex')).toHaveCount(1);
  await panel.getByRole('button',{name:'질문 규칙·프리셋'}).click();const editor=page.getByRole('dialog',{name:'질문 규칙·프리셋'});
  await editor.getByRole('button',{name:'프리셋 추가'}).click();await editor.getByLabel('액션 이름',{exact:true}).last().fill('내 검증');
  await editor.getByLabel('액션 지시',{exact:true}).last().fill('근거부터 설명해 줘');await editor.getByRole('button',{name:'질문 설정 저장'}).click();
  await expect(panel.getByRole('button',{name:'내 검증',exact:true})).toBeVisible();
  await page.screenshot({path:info.outputPath('m3-ask-desktop.png')});
  await panel.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();await page.reload();
  await expect(page.locator('.ai-answer-badge')).toHaveCount(1);await page.getByRole('button',{name:'b0001 대화 보기'}).click();
  await expect(page.locator('.ask-message.assistant')).toHaveCount(3);expect(errors).toEqual([]);
});

test('M3: paper summary tab makes, checks, edits and links the summary',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');
  const tabs=page.getByRole('tablist',{name:'보기'});await tabs.getByRole('tab',{name:'요약'}).click();
  const view=page.getByRole('region',{name:'논문 요약'});
  await expect(view).toContainText('예상 · AI 호출 3회');await view.getByRole('button',{name:'요약 만들기'}).click();
  await expect(view.getByRole('heading',{name:'구조화 요약'})).toBeVisible();
  await expect(view.locator('.summary-field.kind-key')).toContainText('Nova가 풀려는 검출 문제를 다룬다.');
  await expect(view.locator('.summary-field.kind-res .qa-label')).toHaveCount(1);  // 99.9 is not in Table 1
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
  await result.getByRole('button',{name:'발표 노트에 추가'}).click();await expect(view.getByRole('status')).toContainText('발표 노트에 추가했습니다');
  await result.getByRole('button',{name:'AI에게 질문'}).click();
  const panel=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(panel.locator('.selected-quote')).toContainText('Nova는 AP 42.0으로 가장 높다.');
  await panel.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await page.reload();await page.getByRole('tablist',{name:'보기'}).getByRole('tab',{name:'요약'}).click();
  await expect(page.getByRole('region',{name:'논문 요약'})).toContainText('직접 수정함');
  await page.getByRole('button',{name:'다시 만들기'}).click();await expect(page.getByRole('alert')).toContainText('직접 고친 내용이 사라집니다');
  expect(errors).toEqual([]);
});

test('M3: dark 390px question overlay, focus and no horizontal overflow',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark'});await page.goto('/?doc=m2-paper');
  await page.getByRole('button',{name:'b0001 AI에게 질문',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Ask AI',exact:true});await panel.getByRole('button',{name:'질문 보내기',exact:true}).click();
  await expect(panel.locator('.katex')).toHaveCount(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  expect(await panel.evaluate(el=>el.scrollWidth-el.clientWidth)).toBe(0);
  await page.screenshot({path:info.outputPath('m3-ask-390-dark.png')});await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);expect(errors).toEqual([]);
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
  await page.locator('.toc').getByRole('link',{name:'7 결론',exact:true}).click();await expect(page.locator('.toc')).not.toHaveClass(/open/);
  await page.getByRole('button',{name:'맨 위로'}).click();await page.getByRole('button',{name:'문서 검색',exact:true}).click();await page.getByRole('textbox',{name:'문서 검색'}).fill('Rex-Omni');await expect(page.locator('.search-panel [role=status]')).not.toHaveText('0건');
  await page.getByRole('button',{name:'검색 닫기'}).click();await page.screenshot({path:info.outputPath('export-390-dark.png')});
  await page.setViewportSize({width:1440,height:960});expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  expect(network).toEqual([]);expect(errors).toEqual([]);
});

test('M4: answer presentation notes can edit, reorder, reload and export',async({page})=>{
  await page.goto('/?doc=m2-paper');await page.getByRole('button',{name:'b0001 AI에게 질문',exact:true}).click();
  const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await ask.getByRole('button',{name:'질문 보내기'}).click();await expect(ask.getByRole('button',{name:'발표 노트에 추가'})).toBeVisible();
  await ask.getByRole('button',{name:'발표 노트에 추가'}).click();await expect(ask).toContainText('발표 노트에 추가했습니다.');await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
  await page.getByRole('button',{name:'발표 노트',exact:true}).click();const notes=page.getByRole('dialog',{name:'발표 노트',exact:true});
  await notes.getByRole('button',{name:'1. 본문 발표 메모',exact:true}).click();await expect(notes.getByLabel('발표 내용')).toContainText('42.0');
  await notes.getByLabel('메모 제목').fill('발표 핵심');await notes.getByLabel('발표 내용').fill('논문 명시: 결과는 42.0이다.\n\n$$x=42.0$$');await notes.getByRole('button',{name:'메모 저장',exact:true}).click();
  await expect(notes.getByRole('button',{name:'1. 발표 핵심',exact:true})).toBeVisible();
  await notes.getByRole('button',{name:'새 메모 작성'}).click();await notes.getByLabel('메모 제목').fill('다음 장');await notes.getByLabel('발표 내용').fill('직접 작성한 메모');await notes.getByRole('button',{name:'메모 저장',exact:true}).click();
  await expect(notes.getByRole('button',{name:'2. 다음 장',exact:true})).toBeVisible();await notes.getByRole('button',{name:'다음 장 위로'}).click();await expect(notes.getByRole('button',{name:'1. 다음 장',exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await page.reload();await page.getByRole('button',{name:'발표 노트',exact:true}).click();await expect(notes.getByRole('button',{name:'1. 다음 장',exact:true})).toBeVisible();await notes.getByRole('button',{name:'발표 노트 내보내기'}).click();
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'내보내기',exact:true}).click();const exp=page.getByRole('dialog',{name:'논문 내보내기'});await exp.getByRole('button',{name:'저장 위치 선택'}).click();await expect(exp.getByRole('status')).toContainText('저장했습니다');
});

test('M4: standalone exported figures remain embedded and settings explain local updates',async({page,backend},info)=>{
  const response=await fetch(`http://127.0.0.1:${backend.port}/documents/m2-paper/export?format=html`,{headers:{Authorization:`Bearer ${backend.token}`}});const file=info.outputPath('figure-offline.html');await writeFile(file,await response.text());
  await page.goto(pathToFileURL(file).href);await page.locator('.figure-block').first().scrollIntoViewIfNeeded();await expect(page.getByRole('button',{name:'Figure 1 원본 확대',exact:true})).toBeVisible();await page.getByRole('button',{name:'Figure 1 원본 확대',exact:true}).click();await expect(page.getByRole('dialog',{name:'Figure 1 확대 보기'}).getByRole('img')).toBeVisible();
  await page.goto('/?library=1');await page.getByRole('button',{name:'앱 설정',exact:true}).click();const settings=page.getByRole('dialog',{name:'앱 설정'});await expect(settings).toContainText('0.5.0');await expect(settings).toContainText('배포 경로가 연결되지 않았습니다');await expect(settings.getByRole('button',{name:'새 저장 폴더 선택'})).toBeVisible();
});

test('M4: arXiv input uses HTML tables and math, with exact selection after inline math',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.setViewportSize({width:390,height:844});await page.goto('/?library=1');
  await page.getByLabel('arXiv ID 또는 URL').fill('https://arxiv.org/abs/2603.07952v1');await page.getByRole('button',{name:'arXiv 가져오기'}).click();
  const progress=page.getByRole('dialog',{name:'논문 처리'});await progress.getByRole('button',{name:'원문·대역 읽기'}).click();
  await expect(page.getByRole('heading',{name:'Synthetic Paper',exact:true})).toBeVisible();await expect(page.getByRole('dialog',{name:'원본 PDF 보기'})).toHaveCount(0);
  await expect(page.locator('.equation-card .katex')).toHaveCount(1);await expect(page.locator('.table-block th').first()).toHaveAttribute('rowspan','2');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
  const paragraph=page.locator('[data-ask-field=en]').filter({has:page.locator('[data-source-text]')}).first();
  await paragraph.evaluate(el=>{const node=el.lastChild!;const range=document.createRange();range.selectNodeContents(node);const s=window.getSelection()!;s.removeAllRanges();s.addRange(range);});
  await page.keyboard.press('Control+k');const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(ask.locator('.selected-quote')).toContainText('and original text.');await ask.getByRole('button',{name:'질문 보내기',exact:true}).click();await expect(ask.locator('.ask-message.assistant .katex')).toHaveCount(1);
  await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();await page.screenshot({path:info.outputPath('arxiv-390.png')});expect(errors).toEqual([]);
});

test('Tutorial: first launch opens guide without marking AI setup complete or sending content',async({page,backend})=>{
  const writes:string[]=[];page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname);});
  await page.goto('/');const welcome=page.getByRole('dialog',{name:'PaperDuet 시작하기'});
  await welcome.getByRole('button',{name:'먼저 사용 가이드 보기'}).click();
  await expect(page.getByRole('heading',{name:'먼저, AI와 연결해요.'})).toBeVisible();
  await expect(welcome).toHaveCount(0);await expect(page.locator('.tutorial-page input[type=password]')).toHaveCount(0);
  await page.getByRole('button',{name:'Codex CLI',exact:true}).click();await expect(page.locator('.tutorial-command')).toHaveText('codex login');
  await page.getByRole('button',{name:'Claude CLI',exact:true}).click();await expect(page.locator('.tutorial-command')).toHaveText('claude auth login');
  await page.getByRole('button',{name:'실제 AI 연결 설정 열기 ↗'}).click();await expect(page.getByRole('dialog',{name:'AI 연결 설정',exact:true})).toBeVisible();await page.keyboard.press('Escape');
  const status=await fetch(`http://127.0.0.1:${backend.port}/settings/onboarding`,{headers:{Authorization:`Bearer ${backend.token}`}}).then(r=>r.json());
  expect(status.completed).toBe(false);expect(writes.filter(path=>!path.endsWith('/position'))).toEqual([]);
  await page.reload();await expect(page.getByRole('heading',{name:'먼저, AI와 연결해요.'})).toBeVisible();await expect(welcome).toHaveCount(0);
});

test('Tutorial: all eleven scenes at 390px, both themes, examples and progress survive reload',async({page},info)=>{
  const errors:string[]=[];const writes:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname);});
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'light'});await page.goto('/?tutorial=connect');
  const scenes=['connect','import','translate','read','original','ask','review','summary','present','export','settings'];
  for(const theme of ['light','dark']){
    while(await page.locator('html').getAttribute('data-theme')!==theme)await page.getByRole('button',{name:'가이드 테마 변경'}).click();
    for(const id of scenes){await page.getByLabel('설명 선택').selectOption(id);await expect(page.locator('.tutorial-preview')).toBeVisible();expect(await page.evaluate(()=>Math.max(0,document.documentElement.scrollWidth-innerWidth))).toBe(0);}
  }
  await page.getByLabel('설명 선택').selectOption('read');await page.getByRole('group',{name:'리더 보기 예시'}).getByRole('button',{name:'번역',exact:true}).click();await expect(page.locator('.tutorial-parallel [lang=en]')).toHaveCount(0);await expect(page.locator('.tutorial-parallel [lang=ko]')).toBeVisible();
  await page.getByLabel('설명 선택').selectOption('review');for(const name of ['핵심','결과','한계','해석','수식','용어']){await page.getByRole('group',{name:'주석 종류 예시'}).getByRole('button',{name,exact:true}).click();await expect(page.locator('.tutorial-note>div>span')).toHaveText(name);}
  await page.getByLabel('설명 선택').selectOption('present');await page.getByRole('button',{name:'↑ ↓ 예시 순서 바꾸기'}).click();await expect(page.locator('.tutorial-demo-note-list strong').first()).toHaveText('핵심 방법');
  await page.getByLabel('설명 선택').selectOption('export');await page.getByLabel('내보내기 형식 예시').selectOption('presentation');await expect(page.locator('.tutorial-export-file b')).toHaveText('presentation.md');await page.getByRole('button',{name:'이 설명 확인했어요',exact:true}).click();
  await page.screenshot({path:info.outputPath('tutorial-390-dark.png')});await page.reload();await expect(page.getByLabel('설명 선택')).toHaveValue('export');await expect(page.getByRole('button',{name:'✓ 확인 완료',exact:true})).toHaveAttribute('aria-pressed','true');expect(writes).toEqual([]);expect(errors).toEqual([]);
});

test('Tutorial: library and reader entry points, browser history, real settings and sample exit',async({page},info)=>{
  await page.setViewportSize({width:1440,height:1000});await page.goto('/?library=1');await page.getByRole('button',{name:'사용 가이드',exact:true}).click();
  await expect(page.getByRole('heading',{name:'먼저, AI와 연결해요.'})).toBeVisible();await page.screenshot({path:info.outputPath('tutorial-desktop.png')});
  await page.getByRole('button',{name:'다음 설명 →'}).click();await expect(page).toHaveURL(/tutorial=import/);await page.goBack();await expect(page.getByRole('heading',{name:'먼저, AI와 연결해요.'})).toBeVisible();
  await page.getByRole('navigation',{name:'가이드 설명'}).getByRole('button',{name:'11 저장·문제 해결'}).click();await page.getByRole('button',{name:'실제 앱 설정 열기 ↗'}).click();await expect(page.getByRole('dialog',{name:'앱 설정'})).toBeVisible();await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'서재로 시작하기 →'}).click();await expect(page.getByRole('heading',{name:'나의 논문 서재'})).toBeVisible();await page.getByRole('button',{name:'사용 가이드',exact:true}).click();await expect(page.getByRole('heading',{name:'계속 사용할 준비가 됐어요.'})).toBeVisible();
  await page.getByRole('button',{name:'서재로',exact:true}).click();await expect(page.getByRole('heading',{name:'나의 논문 서재'})).toBeVisible();
});
