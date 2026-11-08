import { test, expect, type Page } from '@playwright/test';
import type { Block, Document } from '../src/types';
import type { ReanalysisPreview } from '../src/reanalysis';

const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP4QAAAAASUVORK5CYII=';
const paragraph:Block={id:'old',doc_id:'reanalysis',order:1,type:'p',section_path:['1'],n:'1.1',en:'The original finding has 42 samples.',ko:'원래 번역은 표본 42개를 설명한다.'};
const newer:Block={...paragraph,id:'new-preview',en:'The improved source has 42 samples and a complete sentence.',ko:undefined};
const figure:Block={id:'figure',doc_id:'reanalysis',order:3,type:'fig',section_path:['1'],n:'Figure 1',caption_en:'A complete figure.',caption_ko:'보존되는 그림 설명.',image_path:'old.png'};
const equation:Block={id:'equation',doc_id:'reanalysis',order:4,type:'eq',section_path:['1'],n:'(1)',latex:'x=42'};
const heading:Block={id:'heading',doc_id:'reanalysis',order:0,type:'sec',section_path:['1'],n:'1',en:'Findings',ko:'발견'};
const note:Block={id:'note',doc_id:'reanalysis',order:2,type:'note',section_path:['1'],note:{kind:'key',title:'기존 메모 근거',body_md:'유지되는 주석 내용이다.',claim:'stated',refs:['old'],origin:'user'}};
const table:Block={id:'new-table',doc_id:'reanalysis',order:5,type:'tab',section_path:['1'],n:'Table 2',caption_en:'Recovered results.',table:{header:[[{text_en:'Model',rowspan:2,colspan:1,is_header:true,numeric:false},{text_en:'Scores',rowspan:1,colspan:2,is_header:true,numeric:false}],[{text_en:'A',rowspan:1,colspan:1,is_header:true,numeric:false},{text_en:'B',rowspan:1,colspan:1,is_header:true,numeric:false}]],body:[[{text_en:'Ours',rowspan:1,colspan:1,is_header:false,numeric:false},{text_en:'42',rowspan:1,colspan:1,is_header:false,numeric:true},{text_en:'43',rowspan:1,colspan:1,is_header:false,numeric:true}]],highlight_rows:[],best_cells:[]}};
const original:Document={id:'reanalysis',title:'Reanalysis paper',title_ko:'',arxiv_id:'',source_kind:'pdf',status:'complete',page_count:2,authors:'Author',reading_position:{block_id:null,offset:0},glossary:[],untranslated_count:0,blocks:[heading,paragraph,note,figure,equation]};
const preview:ReanalysisPreview={id:'preview-1',doc_id:'reanalysis',method:'pdf',created_at:'2026-11-07T01:00:00Z',applied:false,counts:{added:1,changed:2,missing:1,unchanged:1},notices:['기존 번역과 메모는 이전 원문에서 유지합니다.'],changes:[
  {id:'text-change',kind:'changed',old_block:paragraph,new_block:newer,translation_kept:true,requires_translation:true,selectable:true,reason:'누락되었던 문장이 복원되었습니다.'},
  {id:'figure-change',kind:'changed',old_block:figure,new_block:{...figure,image_path:'new.png'},translation_kept:true,requires_translation:false,selectable:true},
  {id:'added-table',kind:'added',old_block:null,new_block:table,translation_kept:false,requires_translation:true,selectable:true},
  {id:'missing-equation',kind:'missing',old_block:equation,new_block:null,translation_kept:false,requires_translation:false,selectable:false},
  {id:'unchanged-heading',kind:'unchanged',old_block:heading,new_block:heading,translation_kept:true,requires_translation:false,selectable:false},
]};

async function setup(page:Page,options:{creationError?:boolean;applyError?:boolean;fixture?:boolean;mixed?:boolean}={}){
  const applied:string[][]=[],images:string[]=[],created:string[]=[],figureImages:string[]=[];let figureUpdated=false;
  const current={...original,source_kind:options.mixed?'mixed':original.source_kind,status:options.fixture?'fixture':original.status};
  await page.addInitScript(()=>Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{invoke:async(command:string)=>command==='backend_connection'?{port:19999,token:'test',generation:0}:{configured:false,current_version:'0.10.1'}}}));
  await page.route('http://127.0.0.1:19999/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let data:unknown=[],status=200;
    if(path==='/documents/reanalysis/reanalysis'&&request.method()==='POST'){
      created.push(path);if(options.creationError){status=409;data={detail:'JOB_BUSY'};}else data={...preview,method:options.mixed?'mixed':preview.method};
    }else if(path.endsWith('/preview-1/apply')){
      const ids=request.postDataJSON().change_ids as string[];applied.push(ids);
      if(options.applyError){status=409;data={detail:'REANALYSIS_STALE'};}else{
        figureUpdated=ids.includes('figure-change');const activeFigure=figureUpdated?{...figure,image_path:'new.png'}:figure;
        data={applied:ids.length,document:{...current,status:ids.includes('text-change')?'ready_to_translate':current.status,untranslated_count:ids.includes('text-change')?1:0,blocks:ids.includes('text-change')?[heading,{...paragraph,superseded_by:'new-source'},{...newer,id:'new-source',order:1.5},note,activeFigure,equation]:[heading,paragraph,note,activeFigure,equation]}};
      }
    }else if(path.includes('/reanalysis/preview-1/changes/')&&path.endsWith('/image')){images.push(url.search);data={data_url:png};}
    else if(path==='/documents/reanalysis')data=current;
    else if(path==='/settings/reader')data={view:'split',theme:'light',font_size:16,show_notes:true,note_kinds:['key'],density:'high'};
    else if(path==='/settings/onboarding')data={completed:true};
    else if(path==='/documents/reanalysis/blocks/figure/image'){const value=png+(figureUpdated?'#new':'#old');figureImages.push(value);data={data_url:value};}
    else if(path.endsWith('/image'))data={data_url:png};
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/?doc=reanalysis');await expect(page.locator('#old')).toBeVisible();
  return {applied,images,created,figureImages};
}
async function openPreview(page:Page){
  await page.getByLabel('논문 정보',{exact:true}).click();await page.getByRole('button',{name:'원문 다시 분석',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'원문 다시 분석',exact:true});await expect(dialog).toBeVisible();return dialog;
}

test('Reanalysis compares source material, applies only an explicit selection and preserves prior work',async({page},info)=>{
  const state=await setup(page);await page.setViewportSize({width:1440,height:1050});const dialog=await openPreview(page);
  await expect(dialog.locator('.reanalysis-change')).toHaveCount(4);await expect(dialog.locator('input:checked')).toHaveCount(0);
  await expect(dialog.getByRole('button',{name:'선택한 0개 검토'})).toBeDisabled();
  await dialog.getByLabel('같은 항목도 보기 (1)').check();await expect(dialog.locator('.reanalysis-change')).toHaveCount(5);
  const text=dialog.locator('.reanalysis-change').filter({has:page.getByLabel('1.1 반영 선택 (text-change)')});
  await text.locator('summary').click();await expect(text.getByRole('region')).toHaveCount(2);
  await expect(text.getByRole('heading',{name:'이전 원문',exact:true})).toBeVisible();await expect(text.getByRole('heading',{name:'새 원문',exact:true})).toBeVisible();
  await expect(text).toContainText(paragraph.en!);await expect(text).toContainText(newer.en!);await expect(text).toContainText(paragraph.ko!);
  await page.screenshot({path:info.outputPath('reanalysis-preview.png')});
  const figures=dialog.locator('.reanalysis-change').filter({has:page.getByLabel('Figure 1 반영 선택 (figure-change)')});
  await figures.locator('summary').click();await figures.locator('.reanalysis-columns').scrollIntoViewIfNeeded();await expect(figures.locator('img')).toHaveCount(2);await expect.poll(()=>state.images.length).toBe(2);
  expect(state.images).toEqual(expect.arrayContaining(['?side=before','?side=after']));
  const added=dialog.locator('.reanalysis-change').filter({has:page.getByLabel('Table 2 반영 선택 (added-table)')});await added.locator('summary').click();
  await expect(added.locator('th').first()).toHaveAttribute('rowspan','2');await expect(added.locator('th').nth(1)).toHaveAttribute('colspan','2');
  await expect(dialog.locator('.kind-missing.reanalysis-change')).toContainText('기존 내용 유지');await expect(dialog.locator('.kind-missing.reanalysis-change input')).toHaveCount(0);
  await text.getByRole('checkbox').check();await dialog.getByRole('button',{name:'선택한 1개 검토'}).click();expect(state.applied).toHaveLength(0);
  await expect(dialog).toContainText('기존 번역·메모·대화는 이전 원문에서 유지합니다.');
  await dialog.getByRole('button',{name:'1개 반영',exact:true}).click();await expect(dialog).toHaveCount(0);expect(state.applied).toEqual([['text-change']]);
  await expect(page.locator('#new-source')).toContainText(newer.en!);await expect(page.locator('#note')).toContainText('유지되는 주석 내용이다.');
  await expect(page.locator('#old')).toHaveCount(1);await expect(page.locator('#old [data-block]')).toHaveCount(0);await expect(page.locator('#old .previous-source-details')).not.toHaveAttribute('open','');
  await page.locator('#note a[href="#old"]').click();await expect(page.locator('#old .previous-source-details')).toHaveAttribute('open','');await expect(page.locator('#old')).toContainText(paragraph.ko!);await expect(page.locator('#old')).toContainText(paragraph.en!);
  await expect(page.locator('#old a[href="#new-source"]')).toHaveText('새 원문으로 이동');await expect(page.locator('#equation')).toBeAttached();await expect(page.locator('#figure')).toContainText(figure.caption_ko!);
});

test('Reanalysis cancellation leaves the reader intact and the mobile comparison fits',async({page},info)=>{
  const state=await setup(page);await page.setViewportSize({width:390,height:844});const dialog=await openPreview(page);
  const text=dialog.locator('.reanalysis-change').filter({has:page.getByLabel('1.1 반영 선택 (text-change)')});await text.locator('summary').click();await text.getByRole('checkbox').check();
  expect(await dialog.evaluate(element=>element.scrollWidth-element.clientWidth)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({path:info.outputPath('reanalysis-mobile.png')});await dialog.getByRole('button',{name:'취소',exact:true}).click();
  expect(state.applied).toHaveLength(0);await expect(page.locator('#old')).toContainText(paragraph.ko!);await expect(page.locator('#new-source')).toHaveCount(0);await expect(page.getByLabel('논문 정보',{exact:true})).toBeFocused();
});

test('Reanalysis keeps changes unapplied when processing is busy or the preview is stale',async({page})=>{
  let state=await setup(page,{creationError:true});let dialog=await openPreview(page);await expect(dialog.getByRole('alert')).toBeVisible();expect(state.applied).toHaveLength(0);
  await dialog.getByLabel('다시 분석 닫기').click();await page.unroute('http://127.0.0.1:19999/**');
  state=await setup(page,{applyError:true});dialog=await openPreview(page);await dialog.getByLabel('1.1 반영 선택 (text-change)').check();await dialog.getByRole('button',{name:'선택한 1개 검토'}).click();await dialog.getByRole('button',{name:'1개 반영',exact:true}).click();
  await expect(dialog.getByRole('alert')).toBeVisible();await expect(dialog).toBeVisible();expect(state.applied).toHaveLength(1);await expect(page.locator('#new-source')).toHaveCount(0);await expect(page.locator('#old')).toContainText(paragraph.ko!);
});

test('Bundled sample papers do not offer reanalysis',async({page})=>{
  const state=await setup(page,{fixture:true});await page.getByLabel('논문 정보',{exact:true}).click();await expect(page.getByRole('button',{name:'원문 다시 분석',exact:true})).toHaveCount(0);expect(state.created).toHaveLength(0);
});

test('Mixed source reanalysis refreshes a mounted figure and preserves its translation',async({page})=>{
  const state=await setup(page,{mixed:true});const figureBlock=page.locator('#figure');await figureBlock.scrollIntoViewIfNeeded();
  await expect(figureBlock.locator('.crop-open img')).toHaveAttribute('src',png+'#old');const retained=await figureBlock.elementHandle();
  const dialog=await openPreview(page);await expect(dialog).toContainText('HTML + PDF 원문');await dialog.getByLabel('Figure 1 반영 선택 (figure-change)').check();await dialog.getByRole('button',{name:'선택한 1개 검토'}).click();await dialog.getByRole('button',{name:'1개 반영',exact:true}).click();await expect(dialog).toHaveCount(0);
  await figureBlock.scrollIntoViewIfNeeded();await expect(figureBlock.locator('.crop-open img')).toHaveAttribute('src',png+'#new');expect(state.figureImages).toEqual([png+'#old',png+'#new']);
  expect(await retained!.evaluate(element=>element.isConnected&&document.getElementById('figure')===element)).toBe(true);await expect(figureBlock).toContainText(figure.caption_ko!);expect(state.applied).toEqual([['figure-change']]);
});
