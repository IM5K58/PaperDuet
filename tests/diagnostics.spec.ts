import { test, expect, type Page } from '@playwright/test';
import type { Block, Document } from '../src/types';
import type { BlockSource, DiagnosticReport } from '../src/diagnostics';

const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP4QAAAAASUVORK5CYII=';
const paragraph:Block={id:'p',doc_id:'diagnostic',order:0,type:'p',section_path:['1'],page:1,en:'The original paragraph has 42 samples.',ko:'기존 번역은 표본 42개를 설명한다.'};
const figure:Block={id:'fig',doc_id:'diagnostic',order:1,type:'fig',section_path:['1'],page:2,n:'Figure 1',caption_en:'A missing saved image.',caption_ko:'보존되는 그림 번역.',image_path:'missing.png'};
const note:Block={id:'note',doc_id:'diagnostic',order:2,type:'note',section_path:['1'],note:{kind:'key',title:'보존되는 메모 근거',body_md:'기존 주석과 메모의 근거이다.',claim:'stated',refs:['p'],origin:'user'}};
const paper:Document={id:'diagnostic',title:'Source diagnostics',title_ko:'',source_kind:'pdf',arxiv_id:'',status:'complete',page_count:3,authors:'Author',reading_position:{block_id:null,offset:0},glossary:[],blocks:[paragraph,figure,note]};
const report:DiagnosticReport={doc_id:'diagnostic',generated_at:'2026-11-07T01:00:00Z',busy:false,summary:{total:7,by_kind:{missing_image:1,restore_failed:1,restore_pending:1,untranslated:1,translation_rejected:1,unmatched_source:1,page_without_text:1},blocks:{paragraphs:1,figures:1,tables:1,equations:1},pages:{total:3,with_text:2,without_text:[3]}},notices:['이 목록은 번역 품질 점수가 아닙니다. 원문 대조로 직접 확인하세요.'],issues:[
  {id:'missing',kind:'missing_image',severity:'attention',block_id:'fig',page:2,title:'그림 이미지 누락',detail:'저장된 그림 파일을 찾지 못했습니다.',source_available:true},
  {id:'restore',kind:'restore_failed',severity:'attention',block_id:'p',page:1,title:'표 복원 확인 필요',detail:'복원 결과를 검증하지 못했습니다.',source_available:true},
  {id:'pending',kind:'restore_pending',severity:'info',block_id:'p',page:1,title:'수식 복원 대기',detail:'아직 복원하지 않았습니다.',source_available:true},
  {id:'untranslated',kind:'untranslated',severity:'info',block_id:'p',page:1,title:'미번역 원문',detail:'번역문이 없습니다.',source_available:true},
  {id:'rejected',kind:'translation_rejected',severity:'attention',block_id:'p',page:1,title:'숫자 보존 확인 필요',detail:'받은 번역을 저장하지 않았습니다.',source_available:true},
  {id:'unmatched',kind:'unmatched_source',severity:'info',block_id:null,page:3,region:[10,20,110,220],title:'원본 그림 영역 대조',detail:'리더 항목과 연결되지 않은 영역입니다. 누락 여부는 직접 확인하세요.',source_available:true},
  {id:'empty-page',kind:'page_without_text',severity:'info',block_id:null,page:3,title:'텍스트가 추출되지 않은 페이지',detail:'스캔 또는 그림 페이지인지 원본을 확인하세요.',source_available:true},
]};

async function setup(page:Page,options:{arxiv?:boolean;partial?:boolean;missingPdf?:boolean;empty?:boolean;fixture?:boolean;historical?:boolean;deferPageOne?:boolean;deferSource?:boolean;busy?:boolean}={}) {
  const pages:number[]=[],writes:string[]=[],sourceRequests:string[]=[],errors:string[]=[];
  let releasePage=()=>{},releaseSource=()=>{},pageFinished=()=>{},sourceFinished=()=>{};
  const pageGate=new Promise<void>(done=>{releasePage=done;}),sourceGate=new Promise<void>(done=>{releaseSource=done;});
  const pageDone=new Promise<void>(done=>{pageFinished=done;}),sourceDone=new Promise<void>(done=>{sourceFinished=done;});
  const doc:Document={...paper,status:options.fixture?'fixture':paper.status,source_kind:options.partial?'mixed':options.arxiv?'arxiv_html':'pdf',source_url:options.arxiv||options.partial?'https://arxiv.org/abs/2603.07952v1':'',blocks:options.historical?[{...paragraph,superseded_by:'new'},{...paragraph,id:'new',en:'New source stays separate.',ko:undefined},figure,note]:paper.blocks};
  const info=(blockId:string|null):BlockSource=>({block_id:blockId,page:options.arxiv||!blockId?null:blockId==='fig'?2:1,page_count:3,bbox:options.arxiv||!blockId?null:[50,200,200,400],region:null,page_size:options.arxiv||!blockId?null:{width:500,height:1000},source_kind:doc.source_kind!,source_url:options.missingPdf?'javascript:alert(1)':doc.source_url||'',source_anchor:options.arxiv?'S1':options.partial?'F1':null,available:!options.missingPdf,reason:options.missingPdf?'저장된 원본 PDF가 없습니다.':options.arxiv?'정확한 PDF 위치가 저장되어 있지 않습니다.':null});
  await page.addInitScript(()=>Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{invoke:async(command:string)=>command==='backend_connection'?{port:19999,token:'test',generation:0}:{configured:false,current_version:'0.10.1'}}}));
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://127.0.0.1:19999/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.method()!=='GET'&&!path.endsWith('/position'))writes.push(`${request.method()} ${path}`);
    let data:unknown=[],status=200;
    if(path==='/documents/diagnostic')data=doc;
    else if(path==='/documents/diagnostic/diagnostics')data=options.partial?{...report,summary:{...report.summary,total:1,by_kind:{visual_pdf_fallback:1}},notices:['일부 그림·표는 HTML 표시 문제로 원본 PDF 영역을 사용합니다. 다른 HTML 원문은 유지합니다.'],issues:[{id:'partial-figure',kind:'visual_pdf_fallback',severity:'info',block_id:'fig',page:2,title:'Figure 1 PDF 원본 영역 사용',detail:'HTML 이미지를 불러오지 못해 PDF 원본 영역을 사용한 항목입니다.',source_available:true}]}:options.empty?{...report,busy:!!options.busy,summary:{...report.summary,total:0,by_kind:{}},issues:[]}:{...report,busy:!!options.busy};
    else if(path.endsWith('/source')){
      sourceRequests.push(path);const match=path.match(/\/blocks\/([^/]+)\/source$/);
      if(options.deferSource)await sourceGate;data=info(match?.[1]||null);
    }else if(path.match(/\/pages\/\d+\/image$/)){
      const value=Number(path.match(/\/pages\/(\d+)\//)![1]);pages.push(value);if(options.deferPageOne&&value===1)await pageGate;data={data_url:png+`#page${value}`,page_size:{width:500,height:1000},page_count:3};
    }else if(path.endsWith('/image')){if(options.partial)data={data_url:png+'#crop'};else{status=404;data={detail:'Image not found'};}}
    else if(path==='/settings/reader')data={view:'ko',theme:'light',font_size:16,show_notes:true,note_kinds:['key'],density:'high'};
    else if(path==='/settings/onboarding')data={completed:true};
    try{await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});}catch{/* A cancelled read need not be delivered. */}
    if(path.match(/\/pages\/1\/image$/))pageFinished();if(path.endsWith('/source'))sourceFinished();
  });
  await page.goto('/?doc=diagnostic');await expect(page.locator('#p')).toBeVisible();
  return{pages,writes,sourceRequests,errors,releasePage,releaseSource,pageDone,sourceDone};
}
async function openDiagnostics(page:Page) {
  await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'누락 진단',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'누락 진단',exact:true});await expect(dialog.locator('.diagnostics-issue')).toHaveCount(7);return dialog;
}
function compareDialog(page:Page){return page.getByRole('dialog',{name:'원문 대조',exact:true});}

test('Diagnostics lists declared states, filters attention and compares a block without changing content',async({page},info)=>{
  const state=await setup(page);await page.setViewportSize({width:1440,height:1000});const dialog=await openDiagnostics(page);
  expect(state.pages).toHaveLength(0);await expect(dialog).toContainText('원문과 내용이 정확히 일치하는지는 원문 대조로 확인하세요.');
  await dialog.getByRole('button',{name:'확인 필요 3',exact:true}).click();await expect(dialog.locator('.diagnostics-issue')).toHaveCount(3);
  await page.screenshot({path:info.outputPath('diagnostics-desktop.png'),animations:'disabled'});
  const issue=dialog.locator('.diagnostics-issue').filter({hasText:'그림 이미지 누락'});await issue.getByRole('button',{name:'원문 대조',exact:true}).click();
  const comparison=compareDialog(page);await expect(comparison).toBeVisible();await expect(comparison.getByLabel('대조 PDF 페이지',{exact:true})).toHaveValue('2');
  await expect(comparison.getByRole('img',{name:'대조 원본 PDF 2쪽',exact:true})).toBeVisible();
  const mark=comparison.getByRole('img',{name:'대조 항목의 원본 영역',exact:true});await expect(mark).toBeVisible();
  expect(await mark.evaluate(element=>({left:(element as HTMLElement).style.left,top:(element as HTMLElement).style.top,width:(element as HTMLElement).style.width,height:(element as HTMLElement).style.height}))).toEqual({left:'10%',top:'20%',width:'30%',height:'20%'});
  await expect(comparison.getByRole('region',{name:'현재 리더 대조'})).toContainText(figure.caption_en!);await expect(comparison.getByRole('region',{name:'현재 리더 대조'})).toContainText(figure.caption_ko!);
  await expect(page.locator('#fig')).toHaveCount(1);await page.screenshot({path:info.outputPath('source-comparison-desktop.png'),animations:'disabled'});
  await comparison.getByLabel('원문 대조 닫기').click();await expect(page.locator('#p')).toContainText(paragraph.ko!);await expect(page.locator('#note')).toContainText(note.note!.body_md);expect(state.writes).toEqual([]);expect(state.errors).toEqual([]);
});

test('Page-only diagnostic regions use PDF dimensions and fit the mobile layout',async({page},info)=>{
  const state=await setup(page);await page.setViewportSize({width:390,height:844});const dialog=await openDiagnostics(page);
  await dialog.locator('.diagnostics-issue').filter({hasText:'원본 그림 영역 대조'}).getByRole('button',{name:'원문 대조',exact:true}).click();
  const comparison=compareDialog(page);await expect(comparison.getByLabel('대조 PDF 페이지',{exact:true})).toHaveValue('3');await expect(comparison.getByRole('img',{name:'대조 원본 PDF 3쪽',exact:true})).toBeVisible();
  const mark=comparison.getByRole('img',{name:'대조 항목의 원본 영역',exact:true});await expect(mark).toHaveCSS('position','absolute');expect(await mark.evaluate(element=>(element as HTMLElement).style.left)).toBe('2%');
  expect(state.sourceRequests).toContain('/documents/diagnostic/source');expect(await comparison.evaluate(element=>element.scrollWidth-element.clientWidth)).toBeLessThanOrEqual(1);expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({path:info.outputPath('source-comparison-mobile.png'),animations:'disabled'});await comparison.getByLabel('원문 대조 닫기').click();expect(state.writes).toEqual([]);
});

test('Unmapped arXiv sources keep manual page navigation and ignore an older page response',async({page})=>{
  const state=await setup(page,{arxiv:true,deferPageOne:true});await page.locator('#p').locator('..').getByRole('button',{name:'원문 대조',exact:true}).click();const dialog=compareDialog(page);
  await expect(dialog).toContainText('정확한 PDF 위치가 저장되어 있지 않습니다.');await expect(dialog.getByRole('link',{name:'HTML 원문 열기'})).toHaveAttribute('href','https://arxiv.org/html/2603.07952v1#S1');
  await expect.poll(()=>state.pages).toEqual([1]);await dialog.getByLabel('대조 PDF 페이지',{exact:true}).fill('2');await expect(dialog.getByRole('img',{name:'대조 원본 PDF 2쪽',exact:true})).toHaveAttribute('src',png+'#page2');
  state.releasePage();await state.pageDone;await expect(dialog.getByRole('img',{name:'대조 원본 PDF 2쪽',exact:true})).toHaveAttribute('src',png+'#page2');await expect(dialog.getByRole('img',{name:'대조 항목의 원본 영역'})).toHaveCount(0);
  await dialog.getByLabel('대조 PDF 페이지',{exact:true}).fill('1.5');await expect(dialog.getByLabel('대조 PDF 페이지',{exact:true})).toHaveValue('1');await expect(dialog.getByRole('img',{name:'대조 원본 PDF 1쪽',exact:true})).toHaveAttribute('src',png+'#page1');expect(state.pages.every(Number.isInteger)).toBe(true);
  await expect(dialog.getByRole('region',{name:'현재 리더 대조'})).toContainText(paragraph.en!);await expect(dialog.getByRole('region',{name:'현재 리더 대조'})).toContainText(paragraph.ko!);await dialog.getByLabel('원문 대조 닫기').click();expect(state.errors).toEqual([]);expect(state.writes).toEqual([]);
  await page.getByLabel('논문 정보',{exact:true}).click();await expect(page.getByRole('button',{name:'원본 PDF 보기',exact:true})).toBeVisible();
});

test('Source cancellation and missing PDFs preserve historical blocks and reject unsafe source links',async({page})=>{
  let state=await setup(page,{deferSource:true});await page.locator('#p').locator('..').getByRole('button',{name:'원문 대조',exact:true}).click();let dialog=compareDialog(page);await expect.poll(()=>state.sourceRequests.length).toBe(1);await dialog.getByLabel('원문 대조 닫기').click();state.releaseSource();await state.sourceDone;await expect(dialog).toHaveCount(0);expect(state.pages).toHaveLength(0);expect(state.errors).toEqual([]);
  await page.unroute('http://127.0.0.1:19999/**');state=await setup(page,{missingPdf:true,historical:true});await page.locator('#p').locator('..').getByRole('button',{name:'원문 대조',exact:true}).click();dialog=compareDialog(page);
  await expect(dialog).toContainText('저장된 원본 PDF가 없습니다.');await expect(dialog.getByRole('link')).toHaveCount(0);await expect(dialog.getByLabel('대조 PDF 페이지',{exact:true})).toHaveCount(0);
  await expect(dialog.getByRole('region',{name:'현재 리더 대조'})).toContainText(paragraph.en!);await expect(dialog.getByRole('region',{name:'현재 리더 대조'})).toContainText(paragraph.ko!);await expect(page.locator('#p')).toHaveCount(1);
  await dialog.getByRole('button',{name:'본문 이동',exact:true}).click();await expect(page.locator('#p .previous-source-details')).toHaveAttribute('open','');await expect(page.locator('#note a[href="#p"]')).toBeAttached();expect(state.writes).toEqual([]);
});

test('Empty diagnostics make no completeness claim and sample papers hide diagnostics',async({page})=>{
  let state=await setup(page,{empty:true,busy:true});await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'누락 진단',exact:true}).click();let dialog=page.getByRole('dialog',{name:'누락 진단',exact:true});await expect(dialog.locator('.diagnostics-empty')).toHaveText('확인할 항목이 없습니다.');await expect(dialog.getByRole('button',{name:'원문 다시 분석',exact:true})).toBeDisabled();await expect(dialog).toContainText('논문을 처리하고 있습니다.');await dialog.getByLabel('누락 진단 닫기').click();expect(state.writes).toEqual([]);
  await page.unroute('http://127.0.0.1:19999/**');state=await setup(page,{fixture:true});await page.getByLabel('논문 정보',{exact:true}).click();await expect(page.getByRole('button',{name:'누락 진단',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'원문 대조',exact:true})).toHaveCount(0);expect(state.sourceRequests).toHaveLength(0);
});

test('A partial PDF visual remains informational and opens its PDF region with its HTML anchor',async({page})=>{
  const state=await setup(page,{partial:true});await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'누락 진단',exact:true}).click();const dialog=page.getByRole('dialog',{name:'누락 진단',exact:true});
  const issue=dialog.locator('.diagnostics-issue');await expect(issue).toHaveCount(1);await expect(issue.locator('.diagnostics-severity')).toHaveText('참고');await expect(dialog).toContainText('다른 HTML 원문은 유지합니다.');await expect(dialog.getByRole('button',{name:'확인 필요 0',exact:true})).toBeVisible();
  await issue.getByRole('button',{name:'원문 대조',exact:true}).click();const comparison=compareDialog(page);await expect(comparison.getByLabel('대조 PDF 페이지',{exact:true})).toHaveValue('2');await expect(comparison.getByRole('img',{name:'대조 항목의 원본 영역',exact:true})).toBeVisible();
  await expect(comparison.getByRole('link',{name:'HTML 원문 열기',exact:true})).toHaveAttribute('href','https://arxiv.org/html/2603.07952v1#F1');await expect(comparison.locator('.source-reader .crop-open img')).toHaveAttribute('src',png+'#crop');await expect(comparison.getByRole('region',{name:'현재 리더 대조'})).toContainText(figure.caption_ko!);
  await comparison.getByLabel('원문 대조 닫기').click();await expect(page.locator('#p')).toContainText(paragraph.ko!);await expect(page.locator('#note')).toContainText(note.note!.body_md);expect(state.writes).toEqual([]);
});
