// Keep existing Windows build/release scripts; select native tools on macOS.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { existsSync } from 'node:fs';
const root=fileURLToPath(new URL('../',import.meta.url));
const [action,...args]=process.argv.slice(2),windows=process.platform==='win32';
const python=resolve(root,windows?'.venv/Scripts/python.exe':'.venv/bin/python');
if(process.platform==='darwin'){
  process.env.APPLE_SIGNING_IDENTITY??='-';
  process.env.MACOSX_DEPLOYMENT_TARGET??='14.0';
}
if(windows&&existsSync(resolve(root,'.tools/cargo/bin/cargo.exe'))){
  process.env.CARGO_HOME=resolve(root,'.tools/cargo');process.env.RUSTUP_HOME=resolve(root,'.tools/rustup');
  process.env.PATH=resolve(root,'.tools/cargo/bin')+';'+process.env.PATH;
}
const run=(program,parameters)=>execFileSync(program,parameters,{cwd:root,stdio:'inherit',windowsHide:true});
const tauri=(parameters)=>run(process.execPath,[resolve(root,'node_modules/@tauri-apps/cli/tauri.js'),...parameters]);
function sidecar(parameters=[]){
  if(windows)run('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File','scripts/build-sidecar.ps1',...parameters]);
  else run(python,['scripts/build-sidecar.py',...parameters]);
}
try{
  if(action==='test-backend')run(python,['-m','pytest','backend/tests','-q',...args]);
  else if(!['win32','darwin'].includes(process.platform))throw new Error('Desktop builds support Windows and macOS.');
  else if(action==='sidecar')sidecar(args);
  else if(action==='dev'){
    if(windows)run('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File','scripts/dev.ps1',...args]);
    else tauri(['dev',...args]);
  }else if(action==='build'){
    sidecar();
    tauri(['build','--bundles',windows?'nsis':'app,dmg',...args]);
  }else throw new Error(`Unknown desktop action: ${action}`);
}catch(error){if(!Number.isInteger(error.status))console.error(error.message);process.exit(Number.isInteger(error.status)?error.status:1);}
