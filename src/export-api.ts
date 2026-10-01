// This transport is compiled ONLY into standalone HTML. No network or native IPC.
import { resolveTheme } from './theme';
import type { Document, Settings } from './types';
export const exported=JSON.parse(document.getElementById('paperduet-data')!.textContent!) as {document:Document;images:Record<string,string>};
const defaults:Settings={view:'split',theme:resolveTheme(),font_size:16,show_notes:true,note_kinds:['key','res','lim','ins','mth','trm'],density:'high'};
const prefix='paperduet-export:'+exported.document.id+':';
const memory:Record<string,unknown>={};
function read(key:string,fallback:unknown){try{return JSON.parse(localStorage.getItem(prefix+key)||'null')??memory[key]??fallback;}catch{return memory[key]??fallback;}}
function write(key:string,value:unknown){memory[key]=value;try{localStorage.setItem(prefix+key,JSON.stringify(value));}catch{/* file:// storage may be unavailable */}}
export const ERROR_TEXT:Record<string,string>={};
export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
  const value=typeof options.body==='string'?JSON.parse(options.body):null;
  if(path==='/settings/reader'){if(value){write('reader',value);return undefined as T;}return read('reader',defaults) as T;}
  if(path.endsWith('/position')){write('position',value);return undefined as T;}
  if(path===`/documents/${exported.document.id}`)return {...exported.document,reading_position:read('position',{block_id:null,offset:0})} as T;
  const image=path.match(/\/blocks\/([^/]+)\/image$/);
  if(image&&exported.images[image[1]])return {data_url:exported.images[image[1]]} as T;
  if(path.startsWith('/threads'))return [] as T;
  throw new Error('독립 HTML에서는 읽기 기능만 사용할 수 있습니다.');
}
export async function authorizedFetch(_path:string,_options:RequestInit={}):Promise<Response>{throw new Error('Offline export');}
