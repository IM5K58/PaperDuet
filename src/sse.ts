/** Incremental UTF-8 SSE decoding shared by Ask AI and job progress. */
export async function* readEvents<T>(response:Response):AsyncGenerator<T> {
  if(!response.body)throw new Error('응답 스트림이 없습니다.');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',finished=false;
  try{
    for(;;){
      const chunk=await reader.read();buffer+=decoder.decode(chunk.value,{stream:!chunk.done});
      let match:RegExpExecArray|null;
      while((match=/\r?\n\r?\n/.exec(buffer))){
        const raw=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
        const data=raw.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
        if(data)yield JSON.parse(data) as T;
      }
      if(chunk.done){finished=true;break;}
    }
  }finally{if(!finished)await reader.cancel().catch(()=>{});reader.releaseLock();}
}
