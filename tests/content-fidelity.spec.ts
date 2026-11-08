import { test, expect, type Page } from '@playwright/test';
import type { Block, Document } from '../src/types';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkMath from 'remark-math';

const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP4QAAAAASUVORK5CYII=';

async function openPaper(page: Page, blocks: Block[]) {
  const paper: Document = { id: 'fidelity', title: 'Content fidelity', title_ko: '', arxiv_id: '', status: 'complete',
    blocks, reading_position: { block_id: null, offset: 0 }, page_count: 1, authors: '', glossary: [] };
  await page.addInitScript(() => {
    Object.defineProperty(window, '__TAURI_INTERNALS__', { value: { invoke: async (command: string) => {
      if (command === 'backend_connection') return { port: 19999, token: 'test', generation: 0 };
      if (command === 'check_update') return { configured: false, current_version: '0.10.1' };
      throw new Error(`Unexpected native command: ${command}`);
    } } });
  });
  await page.route('http://127.0.0.1:19999/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = [];
    if (path === '/documents/fidelity') data = paper;
    else if (path === '/settings/reader') data = { view: 'split', theme: 'light', font_size: 16, show_notes: true, note_kinds: [], density: 'normal' };
    else if (path === '/settings/onboarding') data = { completed: true };
    else if (path.endsWith('/image')) data = { data_url: png };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('/?doc=fidelity');
  await expect(page.locator('[data-block]')).toHaveCount(blocks.length);
}

test('Scientific inline text keeps comparisons, entities, line breaks and safe emphasis', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await openPaper(page, [
    { id: 'raw', doc_id: 'fidelity', order: 0, type: 'p', section_path: [],
      en: String.raw`Before \(x<y\) after <b onclick="window.unsafe=1">bold</b><br>next <strong>strong</strong> <em>italic</em><img src=x onerror="window.unsafe=1"><script>window.unsafe=1</script>` },
    { id: 'encoded', doc_id: 'fidelity', order: 1, type: 'p', section_path: [], en: String.raw`Encoded \(a&lt;b\) tail` },
    { id: 'literal', doc_id: 'fidelity', order: 2, type: 'p', section_path: [], en: String.raw`Literal \(\text{<img src=x>}\) tail` },
  ]);
  const raw = page.locator('#raw [data-ask-field=en]').first();
  await expect(raw.locator('[data-source-text]')).toHaveAttribute('data-source-text', String.raw`\(x<y\)`);
  await expect(raw).toContainText('after bold');
  await expect(raw.locator('br')).toHaveCount(1);
  await expect(raw.locator('b')).toHaveText('bold');
  await expect(raw.locator('strong')).toHaveText('strong');
  await expect(raw.locator('em')).toHaveText('italic');
  await expect(page.locator('#encoded [data-ask-field=en]').first().locator('[data-source-text]')).toHaveAttribute('data-source-text', String.raw`\(a<b\)`);
  await expect(page.locator('#literal [data-ask-field=en]').first().locator('[data-source-text]')).toHaveAttribute('data-source-text', String.raw`\(\text{<img src=x>}\)`);
  await expect(page.locator('[data-ask-field] img,[data-ask-field] script,[data-ask-field] [onclick],[data-ask-field] [onerror]')).toHaveCount(0);
  await expect(page.locator('.katex-error')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { unsafe?: number }).unsafe)).toBeUndefined();
  expect(errors).toEqual([]);
});

test('Unverified table and equation restoration shows the source image first', async ({ page }) => {
  await openPaper(page, [
    { id: 'table', doc_id: 'fidelity', order: 0, type: 'tab', section_path: [], n: 'Table 1', en: 'Extracted score 42',
      restore_error: 'numeric_mismatch', image_path: 'table.png', table: { header: [], body: [[{ text_en: '999', colspan: 1, rowspan: 1, is_header: false, numeric: true }]], highlight_rows: [], best_cells: [] } },
    { id: 'equation', doc_id: 'fidelity', order: 1, type: 'eq', section_path: [], n: '(1)', latex: 'x=42',
      restore_error: 'invalid', image_path: 'equation.png' },
  ]);
  for (const id of ['table', 'equation']) {
    const block = page.locator(`#${id}`);
    await block.scrollIntoViewIfNeeded();
    await expect(block.locator('.pdf-crop img')).toBeVisible();
    await expect.poll(() => block.locator('.pdf-crop img').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await expect(block).toContainText('복원 결과를 확인하지 못해 원본 이미지를 표시합니다.');
    await expect(block.locator('table,.equation-scroll')).toHaveCount(0);
    await expect(block.locator('details')).not.toHaveAttribute('open', '');
  }
  await page.locator('#table summary').click();
  await expect(page.locator('#table pre')).toContainText('Extracted score 42');
  await page.locator('#equation summary').click();
  await expect(page.locator('#equation pre')).toContainText('x=42');
});

test('All math delimiters render while code and currency remain literal', async ({ page }) => {
  await openPaper(page, [{ id: 'delimiters', doc_id: 'fidelity', order: 0, type: 'p', section_path: [],
    en: String.raw`Inline $x<y$ and \(a^2\), display \[z>0\] and $$w=1$$. <code>\(literal\) and $code$ and x<y</code>. Prices $5 and $10; escaped \$x\$. Cost $5 <code>$HOME</code> and $10. Compare x<y after.` }]);
  const source=page.locator('#delimiters [data-ask-field=en]').first();
  await expect(source.locator('.katex')).toHaveCount(4);
  await expect(source.locator('.katex-display')).toHaveCount(2);
  await expect(source.locator('code').first()).toHaveText(String.raw`\(literal\) and $code$ and x<y`);
  await expect(source.locator('code').nth(1)).toHaveText('$HOME');
  await expect(source.locator('code .katex')).toHaveCount(0);
  await expect(source).toContainText(String.raw`Prices $5 and $10; escaped \$x\$. Cost $5 $HOME and $10. Compare x<y after.`);
  await expect(source.locator('.katex-error')).toHaveCount(0);
});

test('Unverified LaTeX remains literal and is safe when its image is unavailable', async ({ page }) => {
  await openPaper(page,[{id:'no-image',doc_id:'fidelity',order:0,type:'eq',section_path:[],restore_error:'invalid',latex:'x<y>z'}]);
  const source=page.locator('#no-image');
  await expect(source).toContainText('추출한 원문을 표시합니다.');
  await expect(source.locator('.equation-scroll,.katex')).toHaveCount(0);
  await source.locator('summary').click();
  await expect(source.locator('pre')).toHaveText('x<y>z');
});

test('Exported Markdown keeps literal punctuation and parses scientific math', async () => {
  const python=resolve(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
  const script=String.raw`from paperduet.exports import block_markdown
from paperduet.models import Block
b=Block(id='p',doc_id='d',order=0,type='p',section_path=[],en=r'1. model_name uses *literal*, [label](value), ` + '`ticks`' + String.raw`, and \literal. \(x<y\) $a^2$ \[z>0\] <code>\(literal\) and x<y</code>')
print(block_markdown(b))`;
  const markdown=execFileSync(python,['-c',script],{encoding:'utf8',windowsHide:true,env:{...process.env,PYTHONPATH:resolve('backend'),PYTHONIOENCODING:'utf8'}});
  const tree=unified().use(remarkParse).use(remarkMath).parse(markdown);
  const nodes:{type:string;value?:string;children?:unknown[]}[]=[];
  const visit=(node:{type:string;value?:string;children?:unknown[]})=>{nodes.push(node);node.children?.forEach(child=>visit(child as typeof node));};visit(tree);
  expect(nodes.filter(node=>['list','emphasis','link','inlineCode'].includes(node.type))).toHaveLength(0);
  expect(nodes.filter(node=>node.type==='inlineMath').map(node=>node.value)).toEqual([String.raw`x\textless{}y`,'a^2']);
  expect(nodes.filter(node=>node.type==='math').map(node=>node.value)).toEqual([String.raw`z\textgreater{}0`]);
  const text=nodes.filter(node=>node.type==='text').map(node=>node.value).join('');
  expect(text).toContain(String.raw`1. model_name uses *literal*, [label](value), ` + '`ticks`' + String.raw`, and \literal.`);
  expect(text).toContain(String.raw`\(literal\) and x<y`);
});
