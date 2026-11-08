import { test,expect,type Page } from '@playwright/test';
import type { Block,Document,PipelineOptions } from '../src/types';
import type { OcrCatalog } from '../src/ocr';
import type { ReanalysisPreview } from '../src/reanalysis';

const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP4QAAAAASUVORK5CYII=';
const original:Block={id:'original',doc_id:'ocr-paper',order:0,type:'p',section_path:['1'],page:1,en:'The existing native paragraph has 42 samples.',ko:'기존 번역과 표본 42개를 보존한다.'};
const note:Block={id:'note',doc_id:'ocr-paper',order:1,type:'note',section_path:['1'],note:{kind:'key',title:'기존 메모의 근거',body_md:'보존되는 주석이다.',claim:'stated',refs:['original'],origin:'user'}};
const options:PipelineOptions={provider:'anthropic',mode:'api_key',glossary_model:'test',translate_model:'test',restore_model:'test',annotate_model:'test',min_ratio:.25};
const paper:Document={id:'ocr-paper',title:'OCR paper',title_ko:'',arxiv_id:'',status:'complete',source_kind:'pdf',page_count:5,authors:'Author',blocks:[original,note],reading_position:{block_id:null,offset:0},glossary:[]};

async function setup(page:Page,config:{defer?:boolean;unavailable?:boolean;busy?:boolean;many?:boolean;empty?:boolean;error?:string;progress?:boolean;extractionRequired?:boolean}={}) {
  const posts:{pages:number[];language:string}[]=[],applies:string[][]=[],images:number[]=[],unexpected:string[]=[];let normalReanalysis=0,estimateCount=0,applied=false,release=()=>{},postDone=()=>{};
  const gate=new Promise<void>(done=>{release=done;}),done=new Promise<void>(resolve=>{postDone=resolve;});
  const doc:Document={...paper,status:config.progress?'failed':'complete',blocks:config.progress?[]:paper.blocks};
  const catalog:OcrCatalog={available:!config.unavailable,languages:['eng','kor','eng+kor'],max_pages:20,busy:!!config.busy,source_extraction_required:!!config.extractionRequired,pages:[{page:1,eligible:!!config.progress,has_text:!config.progress,recognized_blocks:0},{page:2,eligible:true,has_text:false,recognized_blocks:0},{page:3,eligible:true,has_text:false,recognized_blocks:0},{page:4,eligible:false,has_text:false,recognized_blocks:2},{page:5,eligible:true,has_text:false,recognized_blocks:0},...(config.many?Array.from({length:21},(_,index)=>({page:index+6,eligible:true,has_text:false,recognized_blocks:0})):[])],notices:config.unavailable?['글자 읽기 구성 요소가 준비되어 있지 않습니다.']:config.extractionRequired?['일반 원문 추출을 먼저 다시 시도해 주세요.']:['인식 결과는 원본 페이지와 대조해 주세요.']};
  await page.addInitScript(()=>{if(!('__TAURI_INTERNALS__' in window))Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{invoke:async(command:string)=>command==='backend_connection'?{port:19999,token:'test',generation:0}:{configured:false,current_version:'0.10.1'}}});});
  await page.route('http://127.0.0.1:19999/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,method=request.method();let data:unknown=[],status=200;
    if(path==='/documents/ocr-paper/ocr'){
      if(method==='GET')data=catalog;
      else{
        const body=request.postDataJSON() as {pages:number[];language:string};posts.push(body);if(config.defer)await gate;
        if(config.error){status=422;data={detail:config.error};}else{
          const recognized=body.pages.includes(2)&&!config.empty;
          const preview:ReanalysisPreview={id:'ocr-preview',doc_id:doc.id,method:'ocr',created_at:'2026-11-07T00:00:00Z',applied:false,counts:{added:recognized?1:0,changed:0,missing:0,unchanged:0},notices:['원본과 기존 번역·메모는 유지됩니다.'],ocr:{pages:body.pages,language:body.language,results:body.pages.map(value=>({page:value,status:config.empty||value===3?'empty':value===2?'recognized':'failed',blocks:!config.empty&&value===2?1:0,...(value!==2&&value!==3&&!config.empty?{error:'OCR_PAGE_TOO_LARGE'}:{})}))},changes:recognized?[{id:'ocr-text',kind:'added',old_block:null,new_block:{id:'recognized-p2',doc_id:doc.id,order:2,type:'p',section_path:[],page:2,en:'Recognized text preserves 42 samples.'},translation_kept:false,requires_translation:true,selectable:true}]:[]};data=preview;
        }
      }
    }else if(path.endsWith('/reanalysis/ocr-preview/apply')){
      applies.push(request.postDataJSON().change_ids);applied=true;data={applied:1,document:{...doc,status:'ready_to_translate',untranslated_count:1,blocks:[...doc.blocks,{id:'ocr-applied',doc_id:doc.id,order:2,type:'p',section_path:[],page:2,en:'Recognized text preserves 42 samples.',ocr:true}]}};
    }else if(path==='/documents/ocr-paper/reanalysis'){normalReanalysis++;data={detail:'Unexpected reanalysis'};status=400;}
    else if(path==='/documents/ocr-paper')data=doc;
    else if(path==='/documents')data=[{id:doc.id,title:doc.title,status:applied?'ready_to_translate':doc.status,page_count:5,block_count:doc.blocks.length,last_page:null,read_percent:0,has_thumbnail:false}];
    else if(path.endsWith('/progress')){
      const job={doc_id:doc.id,status:applied?'ready_to_translate':'failed',stage:applied?'Translate':'Structure',progress:applied?0.3:0.2,checkpoint:{options,completed_stages:applied?['Ingest','Extract','Structure']:['Ingest'],error:applied?undefined:'PIPELINE_FAILED'},usage:[]};await route.fulfill({status:200,contentType:'text/event-stream',body:`event: progress\ndata: ${JSON.stringify(job)}\n\n`});return;
    }else if(path.endsWith('/estimate')){estimateCount++;data={input_tokens:applied?42:0,output_tokens:applied?21:0,restore_regions:0};}
    else if(path.endsWith('/diagnostics'))data={doc_id:doc.id,generated_at:'now',busy:false,summary:{total:1,by_kind:{page_without_text:1},blocks:{paragraphs:1,figures:0,tables:0,equations:0},pages:{total:5,with_text:1,without_text:[2]}},notices:[],issues:[{id:'scan-p2',kind:'page_without_text',severity:'info',block_id:null,page:2,title:'2쪽 텍스트 확인 필요',detail:'스캔 페이지일 수 있습니다.',source_available:true}]};
    else if(path.match(/\/pages\/\d+\/image$/)){const value=Number(path.match(/\/pages\/(\d+)\//)![1]);images.push(value);data={data_url:png+`#page${value}`,page_size:{width:500,height:700},page_count:5};}
    else if(path.endsWith('/source'))data={block_id:'ocr-applied',page:2,page_count:5,bbox:[20,40,300,80],region:null,page_size:{width:500,height:700},source_kind:'pdf',source_url:'',source_anchor:null,available:true,reason:null};
    else if(path==='/settings/reader')data={view:'split',theme:'light',font_size:16,show_notes:true,note_kinds:['key'],density:'high'};
    else if(path==='/settings/onboarding')data={completed:true};
    else if(path==='/settings/providers')data={configured:false,keyring_available:true,cli_enabled:true,options};
    if(method!=='GET'&&!path.endsWith('/ocr')&&!path.endsWith('/apply')&&!path.endsWith('/position'))unexpected.push(`${method} ${path}`);
    try{await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});}catch{/* Closing aborts the read-only OCR request. */}
    if(path.endsWith('/ocr')&&method==='POST')postDone();
  });
  await page.goto(config.progress?'/?library=1':'/?doc=ocr-paper');if(config.progress)await expect(page.getByRole('button',{name:'OCR paper 읽기',exact:true})).toBeVisible();else await expect(page.locator('#original')).toBeVisible();
  return{posts,applies,images,unexpected,release,done,reanalysisCount:()=>normalReanalysis,estimates:()=>estimateCount};
}
async function openOcr(page:Page){await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'스캔 페이지 글자 읽기',exact:true}).click();const dialog=page.getByRole('dialog',{name:'스캔 페이지 글자 읽기',exact:true});await expect(dialog.getByLabel('2쪽 글자 읽기 선택',{exact:true})).toBeVisible();return dialog;}

test('OCR selects pages explicitly, previews partial results and applies only reviewed text',async({page},info)=>{
  const state=await setup(page);const dialog=await openOcr(page);await expect(dialog.locator('input:checked')).toHaveCount(0);expect(state.images).toHaveLength(0);await expect(dialog.getByLabel('글자 읽기 언어')).toHaveValue('eng');await expect(dialog.getByLabel('1쪽 글자 읽기 선택')).toBeDisabled();await expect(dialog.getByLabel('4쪽 글자 읽기 선택')).toBeDisabled();await expect(dialog).toContainText('이미 읽은 원문 2개');
  await dialog.getByRole('button',{name:'2쪽 원본 보기',exact:true}).click();await expect(dialog.getByRole('img',{name:'글자 읽기 원본 PDF 2쪽',exact:true})).toBeVisible();for(const value of [2,3,5])await dialog.getByLabel(`${value}쪽 글자 읽기 선택`).check();await dialog.getByLabel('글자 읽기 언어').selectOption('kor');await page.screenshot({path:info.outputPath('ocr-selection.png'),animations:'disabled'});
  await dialog.getByRole('button',{name:'선택한 3쪽 글자 읽기',exact:true}).click();const result=page.getByRole('dialog',{name:'글자 읽기 결과',exact:true});await expect(result).toBeVisible();expect(state.posts).toEqual([{pages:[2,3,5],language:'kor'}]);expect(state.applies).toHaveLength(0);expect(state.reanalysisCount()).toBe(0);await expect(result).toContainText('읽은 쪽 1 · 글자 없음 1 · 실패 1');await expect(result).toContainText('페이지 크기가 너무 커서');
  const before=state.images.length;await result.locator('.reanalysis-compare>summary').click();await result.locator('.reanalysis-columns').scrollIntoViewIfNeeded();await expect(result.getByRole('img',{name:'원본 PDF 2쪽 · 전체 페이지',exact:true})).toBeVisible();await expect.poll(()=>state.images.length).toBeGreaterThan(before);await expect(result.getByRole('region',{name:'읽은 글자'})).toContainText('Recognized text preserves 42 samples.');await page.screenshot({path:info.outputPath('ocr-result.png'),animations:'disabled'});
  await result.locator('.reanalysis-change input').check();await result.getByRole('button',{name:'선택한 1개 검토'}).click();await result.getByRole('button',{name:'1개 반영',exact:true}).click();await expect(result).toHaveCount(0);expect(state.applies).toEqual([['ocr-text']]);await expect(page.locator('#original')).toContainText(original.ko!);await expect(page.locator('#note a[href="#original"]')).toBeAttached();await expect(page.locator('#ocr-applied')).toContainText('Recognized text preserves 42 samples.');await expect(page.locator('#ocr-applied')).toContainText('OCR로 읽은 원문');expect(state.unexpected).toEqual([]);
  await page.locator('#ocr-applied').locator('..').getByRole('button',{name:'원문 대조',exact:true}).click();const comparison=page.getByRole('dialog',{name:'원문 대조',exact:true});await expect(comparison.getByLabel('대조 PDF 페이지',{exact:true})).toHaveValue('2');await expect(comparison.getByRole('img',{name:'대조 항목의 원본 영역',exact:true})).toBeVisible();
});

test('Closing an in-flight OCR request never applies or later opens its result',async({page})=>{
  const state=await setup(page,{defer:true});const dialog=await openOcr(page);await dialog.getByLabel('2쪽 글자 읽기 선택').check();await dialog.getByRole('button',{name:'선택한 1쪽 글자 읽기',exact:true}).click();await expect(dialog).toContainText('선택한 1쪽에서 글자를 읽고 있습니다');await dialog.getByLabel('글자 읽기 닫기').click();state.release();await state.done;await expect(page.getByRole('dialog',{name:'글자 읽기 결과'})).toHaveCount(0);await expect(page.locator('#original')).toContainText(original.ko!);expect(state.applies).toEqual([]);expect(state.unexpected).toEqual([]);
});

test('OCR respects availability, the page limit and existing native or recognized text',async({page})=>{
  let state=await setup(page,{unavailable:true});let dialog=await openOcr(page);await expect(dialog).toContainText('현재 이 PC에서 글자 읽기를 사용할 수 없습니다.');await expect(dialog.getByRole('button',{name:'선택한 0쪽 글자 읽기'})).toBeDisabled();await dialog.getByLabel('글자 읽기 닫기').click();expect(state.posts).toEqual([]);
  await page.unroute('http://127.0.0.1:19999/**');state=await setup(page,{many:true});dialog=await openOcr(page);await dialog.getByRole('button',{name:'앞에서 20쪽 선택',exact:true}).click();await expect(dialog.locator('input:checked')).toHaveCount(20);await expect(dialog.getByLabel('26쪽 글자 읽기 선택',{exact:true})).toBeDisabled();await dialog.getByLabel('2쪽 글자 읽기 선택',{exact:true}).uncheck();await expect(dialog.getByLabel('26쪽 글자 읽기 선택',{exact:true})).toBeEnabled();expect(state.posts).toEqual([]);
});

test('A diagnostic page entry is explicit selection and empty OCR results stay unapplied',async({page},info)=>{
  const state=await setup(page,{empty:true});await page.setViewportSize({width:390,height:844});await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'누락 진단',exact:true}).click();await page.getByRole('button',{name:'이 쪽 글자 읽기',exact:true}).click();const dialog=page.getByRole('dialog',{name:'스캔 페이지 글자 읽기',exact:true});await expect(dialog.getByLabel('2쪽 글자 읽기 선택')).toBeChecked();expect(await dialog.evaluate(element=>element.scrollWidth-element.clientWidth)).toBeLessThanOrEqual(1);await page.screenshot({path:info.outputPath('ocr-mobile.png'),animations:'disabled'});
  await dialog.getByRole('button',{name:'선택한 1쪽 글자 읽기',exact:true}).click();const result=page.getByRole('dialog',{name:'글자 읽기 결과',exact:true});await expect(result).toContainText('읽은 쪽 0 · 글자 없음 1 · 실패 0');await expect(result.getByRole('button',{name:'선택한 0개 검토'})).toBeDisabled();await result.getByRole('button',{name:'다시 선택',exact:true}).click();await expect(page.getByRole('dialog',{name:'스캔 페이지 글자 읽기'})).toBeVisible();expect(state.reanalysisCount()).toBe(0);expect(state.applies).toEqual([]);
});

test('OCR remains available after extraction failure and refreshes processing state after apply',async({page})=>{
  const state=await setup(page,{progress:true});await page.getByRole('button',{name:'OCR paper 읽기',exact:true}).click();const processing=page.getByRole('dialog',{name:'논문 처리',exact:true});await expect(processing.getByRole('button',{name:'원문·대역 읽기'})).toBeDisabled();await processing.getByRole('button',{name:'스캔 페이지 글자 읽기',exact:true}).click();const dialog=page.getByRole('dialog',{name:'스캔 페이지 글자 읽기',exact:true});await dialog.getByLabel('2쪽 글자 읽기 선택').check();await dialog.getByRole('button',{name:'선택한 1쪽 글자 읽기',exact:true}).click();const result=page.getByRole('dialog',{name:'글자 읽기 결과',exact:true});await result.locator('.reanalysis-change input').check();await result.getByRole('button',{name:'선택한 1개 검토'}).click();await result.getByRole('button',{name:'1개 반영',exact:true}).click();await expect(processing.getByRole('button',{name:'원문·대역 읽기'})).toBeEnabled();await expect.poll(()=>state.estimates()).toBeGreaterThan(1);expect(state.unexpected).toEqual([]);
});

test('OCR failures and a required native extraction are explained without replacing saved text',async({page})=>{
  let state=await setup(page,{error:'OCR_LANGUAGE_UNAVAILABLE'});let dialog=await openOcr(page);await dialog.getByLabel('2쪽 글자 읽기 선택').check();await dialog.getByRole('button',{name:'선택한 1쪽 글자 읽기',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('선택한 언어의 글자 읽기 자료가 없습니다.');await expect(dialog.getByLabel('2쪽 글자 읽기 선택')).toBeChecked();await expect(page.locator('#original')).toContainText(original.ko!);expect(state.posts).toHaveLength(1);expect(state.applies).toEqual([]);await dialog.getByLabel('글자 읽기 닫기').click();
  await page.unroute('http://127.0.0.1:19999/**');state=await setup(page,{extractionRequired:true});dialog=await openOcr(page);await expect(dialog).toContainText('일반 원문 추출을 먼저 다시 시도해 주세요.');await expect(dialog.getByRole('button',{name:'선택한 0쪽 글자 읽기'})).toBeDisabled();expect(state.posts).toEqual([]);
});
