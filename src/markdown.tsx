import Markdown from 'react-markdown';
import remarkMath from 'remark-math';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';

export function AnswerMarkdown({text}:{text:string}) {
  return <div className="answer-markdown"><Markdown remarkPlugins={[remarkMath,remarkGfm]} rehypePlugins={[[rehypeKatex,{trust:false,strict:'error',maxExpand:1000,maxSize:20}]]} skipHtml components={{a:({children})=><span>{children}</span>,img:()=>null}}>{text}</Markdown></div>;
}
