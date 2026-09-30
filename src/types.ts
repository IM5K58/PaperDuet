export type View = 'en' | 'split' | 'ko';
export type NoteKind = 'key' | 'res' | 'lim' | 'ins' | 'mth' | 'trm';
export interface Cell { text_en: string; text_ko?: string; colspan: number; rowspan: number; is_header: boolean; numeric: boolean }
export interface Block {
  id: string; doc_id: string; order: number;
  type: 'sec' | 'sub' | 'ssub' | 'p' | 'li' | 'note' | 'card' | 'eq' | 'fig' | 'tab';
  section_path: string[]; page?: number; n?: string; en?: string; ko?: string; latex?: string;
  caption_en?: string; caption_ko?: string; image_path?: string;
  table?: { header: Cell[][]; body: Cell[][]; highlight_rows: number[]; best_cells: [number, number][] };
  card?: { body: string; explain_ko: string };
  note?: { kind: NoteKind; title: string; body_md: string; claim: 'stated' | 'interpretation' | 'mixed'; refs: string[]; origin: 'generated' | 'user' | 'ai_answer' };
  qa_flags: string[];
}
export interface Position { block_id: string | null; offset: number }
export interface Document {
  source_kind?: string; source_url?: string; id: string; title: string; title_ko: string; arxiv_id: string; status: string; blocks: Block[]; reading_position: Position; page_count: number; authors: string; glossary: {term:string;ko:string;definition_ko:string;keep_english:boolean}[] }
export type Provider = 'anthropic'|'openai'|'google';
export type ConnectionMode = 'api_key'|'cli';
export interface PipelineOptions { provider?:Provider;mode?:ConnectionMode;glossary_model:string;translate_model:string;restore_model:string;annotate_model:string;min_ratio:number;batch?:boolean }
export interface Job { doc_id:string;stage:string;status:string;progress:number;checkpoint:{ error?:string;review_count?:number;extracted_pages?:number;translated_count?:number;total_blocks?:number;annotation_count?:number;annotation_total?:number;completed_stages?:string[];options:PipelineOptions;pending_batch?:{submitted_at:number;count:number}|null };usage:{stage:string;model:string;tokens_in:number;tokens_out:number;cache_read?:number;cache_write?:number;calls?:number;requests?:number;batch?:number}[] }
export interface LibraryItem { id:string;title:string;status:string;page_count:number;block_count:number;opened_at?:string }
export interface Settings { view: View; theme: 'system' | 'light' | 'dark'; font_size: number; show_notes: boolean; note_kinds: NoteKind[]; density: 'low' | 'normal' | 'high' }
