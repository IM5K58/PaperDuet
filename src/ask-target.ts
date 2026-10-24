import type { Document } from './types';

export interface Anchor { block_id:string;field:string;start:number;end:number;text:string }
export type AskTarget={kind:'text';anchor:Anchor}|{kind:'figure';blockId:string}|{kind:'paper';blockId:string};
export function targetAnchor(target:AskTarget):Anchor {
  return target.kind==='text'?target.anchor:{block_id:target.blockId,field:target.kind==='paper'?'document':'caption_en',start:0,end:0,text:''};
}
export function askTarget(anchor:Anchor|null,doc?:Document):AskTarget|null {
  if(!anchor)return null;
  if(anchor.field==='document')return {kind:'paper',blockId:anchor.block_id};
  if(anchor.field==='caption_en'&&!anchor.text&&doc?.blocks.some(b=>b.id===anchor.block_id&&b.type==='fig'&&b.image_path))return {kind:'figure',blockId:anchor.block_id};
  return {kind:'text',anchor};
}
export function targetTitle(target:AskTarget|null,doc?:Document):string {
  if(!target||target.kind==='paper')return '';
  return (target.kind==='text'?target.anchor.text.replace(/\s+/g,' ').trim():doc?.blocks.find(b=>b.id===target.blockId)?.n||'그림').slice(0,120);
}
