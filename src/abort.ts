/** Safari versions without AbortSignal.any still need both stop and timeout. */
export function combineSignals(signals:AbortSignal[]):AbortSignal {
  if(typeof AbortSignal.any==='function')return AbortSignal.any(signals);
  const controller=new AbortController();
  const stop=()=>{
    const source=signals.find(signal=>signal.aborted);
    for(const signal of signals)signal.removeEventListener('abort',stop);
    controller.abort(source?.reason);
  };
  for(const signal of signals){
    if(signal.aborted){stop();break;}
    signal.addEventListener('abort',stop,{once:true});
  }
  return controller.signal;
}
