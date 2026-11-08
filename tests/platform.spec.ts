import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';

// These scenarios have their own small paper: no private Rex-Omni fixture needed.
test('macOS: Command shortcuts, keychain labels, API-only connections and floating tools',async({page},info)=>{
  const directory=await mkdtemp(join(tmpdir(),'PaperDuet macOS UI ')),token=randomBytes(32).toString('hex');
  const python=resolve(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
  const child=spawn(python,[resolve('backend/tests/m3_server.py')],{env:{...process.env,PAPERDUET_DATA_DIR:directory,PAPERDUET_SESSION_TOKEN:token,PAPERDUET_ORIGIN:'http://127.0.0.1:1420'},windowsHide:true});
  try{
    const port=await new Promise<number>((ready,reject)=>{const timeout=setTimeout(()=>reject(new Error('Backend readiness timeout')),30000);createInterface({input:child.stdout}).once('line',line=>{clearTimeout(timeout);ready(JSON.parse(line).port);});child.once('error',e=>{clearTimeout(timeout);reject(e);});child.once('exit',code=>{clearTimeout(timeout);reject(new Error(`Backend exited ${code}`));});});
    await fetch(`http://127.0.0.1:${port}/settings/onboarding`,{method:'PUT',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:'{"completed":true}'});
    await page.addInitScript(connection=>{
      Object.defineProperty(navigator,'platform',{get:()=> 'MacIntel'});
      Object.defineProperty(AbortSignal,'any',{value:undefined});
      Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{invoke:async(command:string)=>{
        if(command==='backend_connection')return connection;
        if(command==='check_update'||command==='update_status')return {configured:false,current_version:'0.10.1'};
        throw new Error(`Unexpected native command: ${command}`);
      }}});
    },{port,token,generation:0});
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    await page.setViewportSize({width:1440,height:960});await page.goto('/?doc=m2-paper');await expect(page.locator('[data-block]')).toHaveCount(8);
    await page.keyboard.press('Meta+3');await expect(page.locator('body')).toHaveAttribute('data-view','ko');
    await page.keyboard.press('Meta+2');await expect(page.locator('body')).toHaveAttribute('data-view','split');
    await page.keyboard.press('Meta+f');await expect(page.getByRole('textbox',{name:'문서 검색'})).toBeVisible();await page.keyboard.press('Escape');
    await page.locator('#b0001 [data-ask-field=en]').evaluate(element=>{const range=document.createRange();range.setStart(element.firstChild!,0);range.setEnd(element.firstChild!,2);const selection=getSelection()!;selection.removeAllRanges();selection.addRange(range);});
    await page.keyboard.press('Meta+k');const ask=page.getByRole('dialog',{name:'Ask AI',exact:true});await expect(ask.locator('.selected-quote')).toHaveText('We');
    await ask.getByRole('textbox',{name:'질문',exact:true}).fill('선택한 문장을 설명해 줘');await ask.getByRole('textbox',{name:'질문',exact:true}).press('Meta+Enter');
    await expect(ask).toContainText('입력 123 · 출력 45 토큰');await expect(ask.locator('.answer-markdown .katex')).toHaveCount(1);
    const before=(await ask.boundingBox())!,handle=ask.locator('[data-resize=w]'),box=(await handle.boundingBox())!;
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x-40,box.y+box.height/2,{steps:6});await page.mouse.up();
    const after=(await ask.boundingBox())!;expect(after.width).toBeGreaterThan(before.width+30);expect(after.x+after.width).toBeCloseTo(before.x+before.width,0);
    await ask.getByText('AI 연결·질문 설정',{exact:true}).click();await ask.getByRole('button',{name:'연결 설정',exact:true}).click();
    const connection=page.getByRole('dialog',{name:'AI 연결 설정',exact:true});await expect(connection).toContainText('macOS 키체인');await expect(connection.getByLabel('연결 방식',{exact:true}).first().locator('option')).toHaveCount(1);
    await connection.locator('summary').filter({hasText:'단계별 기본 모델'}).click();await expect(connection.getByLabel('연결 방식',{exact:true}).last().locator('option')).toHaveCount(1);
    await connection.getByRole('button',{name:'창 닫기',exact:true}).click();await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
    const figure=page.getByRole('button',{name:'Figure 1 원본 확대',exact:true});await figure.scrollIntoViewIfNeeded();await figure.click({button:'right'});await page.getByRole('menuitem',{name:'Ask AI',exact:true}).click();
    await expect(ask.locator('.selected-quote')).toHaveText('Figure 1');await expect(ask.locator('.ask-message')).toHaveCount(0);
    await ask.getByText('전송 컨텍스트 확인',{exact:true}).click();await expect(ask.getByLabel('원본 이미지 첨부')).toBeChecked();await expect(ask.locator('.context-debug pre')).toContainText('"attached": true');await ask.getByRole('button',{name:'AI 질문 닫기',exact:true}).click();
    await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'메모장',exact:true}).click();
    const notebook=page.getByRole('dialog',{name:'메모장',exact:true});await notebook.getByRole('textbox',{name:'메모 제목',exact:true}).fill('맥 메모');await notebook.getByRole('textbox',{name:'메모 내용',exact:true}).fill('맥에서도 논문별 메모 저장');await notebook.getByRole('textbox',{name:'메모 내용',exact:true}).press('Meta+s');
    await expect(notebook).toContainText('저장했습니다.');await page.reload();await page.getByRole('toolbar',{name:'보조 기능'}).getByRole('button',{name:'메모장',exact:true}).click();await notebook.getByLabel('내 메모',{exact:true}).selectOption({label:'맥 메모'});await expect(notebook.getByRole('textbox',{name:'메모 내용',exact:true})).toHaveValue('맥에서도 논문별 메모 저장');
    await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
    await notebook.getByRole('button',{name:'메모장 닫기',exact:true}).click();await page.setViewportSize({width:1440,height:960});
    await page.getByRole('button',{name:'읽기 설정',exact:true}).click();const settings=page.getByRole('dialog',{name:'읽기 설정',exact:true});await settings.getByRole('button',{name:'다크',exact:true}).click();await page.getByRole('button',{name:'읽기 설정',exact:true}).click();
    await page.getByRole('link',{name:'PaperDuet · 서재로 이동',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
    await page.getByRole('searchbox',{name:'논문 제목 검색'}).fill('M2 Test Paper');await expect(page.locator('.shelf-page[aria-hidden=false] .shelf-book')).toHaveCount(1);
    await page.screenshot({path:info.outputPath('macos-shelf-dark.png'),fullPage:true});
    await page.getByRole('button',{name:'논문 추가',exact:true}).click();const add=page.getByRole('dialog',{name:'논문 추가',exact:true});await expect(add.getByRole('button',{name:'PDF 선택'})).toBeVisible();await expect(add.getByLabel('arXiv ID 또는 URL')).toBeVisible();await page.keyboard.press('Escape');
    await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBe(0);
    expect(errors).toEqual([]);
  }finally{child.kill();await new Promise<void>(resolveExit=>{if(child.exitCode!==null)resolveExit();else child.once('exit',()=>resolveExit());});}
});
