import Markdown from 'react-markdown';
import remarkMath from 'remark-math';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';

// Models often delimit math as \( … \) and \[ … \]; remark-math only knows $ and $$,
// and Markdown would eat the backslashes, leaving raw "QW_i^Q" on screen.
// Code spans and fences are left alone.
export function normalizeMath(text:string) {
  return text.split(/(```[\s\S]*?```|`[^`\n]*`)/).map((part,i)=>i%2?part:part
    .replace(/\\\[([\s\S]+?)\\\]/g,(_,math:string)=>`\n$$\n${math.trim()}\n$$\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g,(_,math:string)=>`$${math.trim()}$`)).join('');
}

export function AnswerMarkdown({text}:{text:string}) {
  // strict:false: Korean words inside a formula (\text{손실} or not) render instead of turning red.
  return <div className="answer-markdown"><Markdown remarkPlugins={[remarkMath,remarkGfm]} rehypePlugins={[[rehypeKatex,{trust:false,strict:false,maxExpand:1000,maxSize:20}]]} skipHtml components={{a:({children})=><span>{children}</span>,img:()=>null}}>{normalizeMath(text)}</Markdown></div>;
}
