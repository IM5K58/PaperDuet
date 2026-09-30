"""Official CLI invocation only. Never inspect authentication/config files."""
import asyncio
from contextlib import aclosing
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .provider import ProviderError
from .providers import StructuredAdapter, flat, image_content


def cli_enabled():
    # A build resource, not a user-switchable setting, gates distribution builds.
    policy=Path(__file__).with_name('build_policy.json')
    return json.loads(policy.read_text(encoding='utf-8')).get('cli_enabled',False) if policy.exists() else not getattr(sys,'frozen',False)


def resolve_cli(provider, manual=None):
    if not cli_enabled(): raise ProviderError('CLI_DISABLED')
    name={'anthropic':'claude','openai':'codex'}.get(provider)
    if not name: raise ProviderError('CLI_UNSUPPORTED')
    home=Path.home();appdata=Path(os.environ.get('APPDATA',str(home)))
    candidates=[Path(manual)] if manual else [Path(p) for p in [shutil.which(name+'.exe'),shutil.which(name+'.cmd')] if p]
    if not manual:
        candidates += [home/'.local/bin'/f'{name}.exe',appdata/'npm'/f'{name}.cmd']
        if name=='codex':
            candidates += sorted((Path(os.environ.get('LOCALAPPDATA',str(home)))/'OpenAI/Codex/bin').glob('*/codex.exe'),reverse=True)
    for path in candidates:
        path=path.expanduser().resolve()
        if not path.is_file(): continue
        if path.suffix.lower()=='.exe': return [str(path)]
        if path.suffix.lower() not in {'.cmd','.ps1'}: continue
        # Resolve only official npm package locations, never run/interpret a shim.
        root=path.parent/'node_modules'
        package=root/('@anthropic-ai/claude-code' if name=='claude' else '@openai/codex')
        native=package/'bin/claude.exe'
        if name=='claude' and native.is_file(): return [str(native)]
        script=package/('cli.js' if name=='claude' else 'bin/codex.js')
        node=path.parent/'node.exe'
        if not node.is_file(): node=Path(shutil.which('node.exe') or '')
        if script.is_file() and node.is_file(): return [str(node),str(script)]
    raise ProviderError('CLI_NOT_FOUND')


class ProcessJob:
    """Assign suspended child before any CLI/plugin code can create descendants."""
    def __init__(self,pid):
        self.handle=None
        if sys.platform!='win32': return
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),
                ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_ulonglong) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        api=ctypes.WinDLL('kernel32',use_last_error=True);self.api=api
        api.CreateJobObjectW.restype=wintypes.HANDLE;api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        api.OpenProcess.restype=wintypes.HANDLE;api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=api.CreateJobObjectW(None,None)
        process=api.OpenProcess(0x0100|0x0800|0x0001|0x0400,False,pid)
        try:
            limits=Extended();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
            if not self.handle or not process or not api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(self.handle,process):
                raise ProviderError('CLI_ISOLATION_FAILED')
            resume=ctypes.WinDLL('ntdll').NtResumeProcess;resume.argtypes=[wintypes.HANDLE];resume.restype=ctypes.c_long
            if resume(process)!=0: raise ProviderError('CLI_ISOLATION_FAILED')
        except BaseException:
            self.close();raise
        finally:
            if process: api.CloseHandle(process)

    def close(self):
        if self.handle:
            self.api.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
            self.api.QueryInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
            self.api.TerminateJobObject(self.handle,1)
            # Closing a job initiates asynchronous teardown. Wait for descendants
            # to release their working directory before removing the temp folder.
            for _ in range(200):
                info=ctypes.create_string_buffer(48)
                if not self.api.QueryInformationJobObject(self.handle,1,info,48,None) or int.from_bytes(info.raw[40:44],'little')==0:break
                time.sleep(.01)
            self.api.CloseHandle(self.handle);self.handle=None


def environment():
    # Pass only OS/runtime discovery variables. No API-key env fallback and no
    # inherited hooks, proxy settings, session tokens, or nested-agent markers.
    allowed={'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','USERPROFILE','HOMEDRIVE','HOMEPATH','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA'}
    env={k:v for k,v in os.environ.items() if k.upper() in allowed}
    env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1',LANG='C.UTF-8',LC_ALL='C.UTF-8',NO_COLOR='1',
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    return env


async def execute(command, data, cwd, timeout=600):
    process=None;job=None
    try:
        async with asyncio.timeout(timeout):
            process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,cwd=cwd,env=environment(),limit=4*1024*1024,
                creationflags=(0x08000000|0x4) if sys.platform=='win32' else 0)
            job=ProcessJob(process.pid)
            async def feed():
                process.stdin.write(data.encode('utf-8'));await process.stdin.drain();process.stdin.close()
            feed_task=asyncio.create_task(feed());size=0
            try:
                while line:=await process.stdout.readline():
                    size+=len(line)
                    if size>8_000_000: raise ProviderError('AI_OUTPUT_LIMIT')
                    yield line.decode('utf-8',errors='strict').rstrip('\r\n')
                await feed_task
                if await process.wait()!=0: raise ProviderError('CLI_REQUEST_FAILED')
            finally:
                if not feed_task.done(): feed_task.cancel()
                await asyncio.gather(feed_task,return_exceptions=True)
    except TimeoutError: raise ProviderError('CLI_TIMEOUT') from None
    except ProviderError: raise
    except (OSError,UnicodeError,ValueError): raise ProviderError('CLI_REQUEST_FAILED') from None
    finally:
        if job: job.close()
        if process and process.returncode is None:
            try: process.kill()
            except ProcessLookupError: pass
            await process.wait()


CLI_ROLE=('You are a model inside PaperDuet. The user message is JSON: follow its "system" field as your instructions '
          'and answer its last message. No tools are available.')


class CLIProvider(StructuredAdapter):
    mode='cli'
    supports_images=True
    def __init__(self,provider,path=None):
        self.id,self.path=provider,path
        self.stream_granularity='token' if provider=='anthropic' else 'message'

    def connected(self):
        try: resolve_cli(self.id,self.path);return True
        except ProviderError: return False

    async def list_models(self):
        # Official CLIs have no stable model-list endpoint; editable aliases.
        ids=(['sonnet','opus','haiku','claude-opus-5-5','claude-opus-5','claude-opus-4-8','claude-opus-4-7','claude-opus-4-6','claude-sonnet-5','claude-sonnet-4-6','claude-haiku-4-5']
             if self.id=='anthropic' else ['default','gpt-6-astra','gpt-6-sol','gpt-6-luna','gpt-5.5'])
        return [{'id':m,'name':m} for m in ids]

    async def probe(self,args):
        with tempfile.TemporaryDirectory(prefix='PaperDuet CLI ') as folder:
            return '\n'.join([line async for line in execute(resolve_cli(self.id,self.path)+args,'',folder,30)])

    async def health_check(self):
        command=resolve_cli(self.id,self.path)
        version=await self.probe(['--version'])
        # Parse, never return raw CLI output or account identifiers.
        import re
        version=re.search(r'\d+\.\d+\.\d+(?:[-.a-zA-Z0-9]*)?',version)
        try:
            result=await self.probe(['auth','status','--json'] if self.id=='anthropic' else ['login','status'])
            logged=json.loads(result).get('loggedIn',False) if self.id=='anthropic' else True
        except ProviderError:
            logged=False
        except ValueError:
            logged=False
        return {'status':'ok' if logged else 'login_required','path':command[-1],'version':version[0] if version else 'unknown',
            'models':await self.list_models(),'supports_images':True,'stream_granularity':self.stream_granularity}

    async def stream(self,model,system,messages,image=None,effort=None):
        command=resolve_cli(self.id,self.path)
        help_text=await self.probe(['--help'] if self.id=='anthropic' else ['exec','--help'])
        required=['--tools','--strict-mcp-config','--setting-sources','--no-session-persistence'] if self.id=='anthropic' else ['--ignore-user-config','--ignore-rules','--ephemeral']
        if not all(flag in help_text for flag in required): raise ProviderError('CLI_UPDATE_REQUIRED')
        # The complete prompt, including user text and system policy, goes on stdin.
        prompt=json.dumps({'system':system,'messages':[{**m,'content':flat(m['content'])} for m in messages]},ensure_ascii=False)
        with tempfile.TemporaryDirectory(prefix='PaperDuet CLI 한글 ') as folder:
            if self.id=='anthropic':
                args=['-p','--output-format','stream-json','--verbose','--include-partial-messages','--input-format','stream-json',
                    '--tools','','--permission-mode','dontAsk','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
                    '--setting-sources','','--settings','{"disableAllHooks":true}',
                    '--disable-slash-commands','--no-chrome','--no-session-persistence','--model',model]
                # Replace Claude Code's own agent prompt (~14k tokens per call) with a one-line role.
                if '--system-prompt' in help_text: args+=['--system-prompt',CLI_ROLE]
                if effort and '--effort' in help_text and 'haiku' not in model: args+=['--effort',effort]
                content=[{'type':'text','text':prompt}]
                if image: content.append({'type':'image','source':{'type':'base64','media_type':'image/png','data':image_content(image)}})
                data=json.dumps({'type':'user','message':{'role':'user','content':content}},ensure_ascii=False)+'\n'
            else:
                args=['--no-daemon','-a','never','exec','--sandbox','read-only','--skip-git-repo-check','--ephemeral',
                    '--ignore-user-config','--ignore-rules','--json','-c','web_search="disabled"',
                    '-c','features.shell_tool=false','-c','features.apply_patch_freeform=false','-c','features.multi_agent=false',
                    '-c','mcp_servers={}']
                if effort: args+=['-c',f'model_reasoning_effort="{effort}"']
                if model!='default':args+=['--model',model]
                if image:
                    copied=Path(folder)/'context.png';shutil.copyfile(image,copied);args+=['--image',str(copied)]
                args+=['-'];data=prompt
            answered='';finished=False;usage={}
            async with aclosing(execute(command+args,data,folder)) as lines:
                async for line in lines:
                    try: event=json.loads(line)
                    except ValueError: continue
                    delta=''
                    if self.id=='anthropic':
                        if event.get('type')=='stream_event':
                            part=event.get('event',{}).get('delta',{})
                            if part.get('type')=='text_delta': delta=part.get('text','')
                        if event.get('type')=='result':
                            if event.get('is_error'): raise ProviderError('CLI_REQUEST_FAILED')
                            result=event.get('result','')
                            if not answered: delta=result
                            u=event.get('usage',{});usage={'tokens_in':u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0),'tokens_out':u.get('output_tokens',0),
                                'cache_read':u.get('cache_read_input_tokens',0),'cache_write':u.get('cache_creation_input_tokens',0)};finished=True
                    else:
                        if event.get('type') in {'error','turn.failed'}: raise ProviderError('CLI_REQUEST_FAILED')
                        if event.get('type')=='item.completed' and event.get('item',{}).get('type')=='agent_message': delta=event['item'].get('text','')
                        if event.get('type')=='turn.completed':
                            u=event.get('usage',{});usage={'tokens_in':u.get('input_tokens',0),'tokens_out':u.get('output_tokens',0),'cache_read':u.get('cached_input_tokens',0),'cache_write':0};finished=True
                    if delta: answered+=delta;yield {'text':delta}
            if not finished or not answered: raise ProviderError('AI_STREAM_INTERRUPTED')
            yield {'usage':usage}
