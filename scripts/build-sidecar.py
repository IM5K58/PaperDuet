"""Native macOS backend bundle. Windows keeps its existing PowerShell builder."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-only',action='store_true')
    options=parser.parse_args()
    if sys.platform!='darwin':parser.error('Build the macOS sidecar on a Mac with matching-architecture Python.')
    package=ROOT/'backend/paperduet'
    version=int(re.search(r'^SCHEMA_VERSION\s*=\s*(\d+)',(package/'store.py').read_text(encoding='utf-8'),re.M)[1])
    migrations=[package/f'migration_{step}.sql' for step in range(2,version+1)]
    if any(not path.is_file() for path in migrations):raise RuntimeError('A required database migration is missing.')
    build=ROOT/'build';build.mkdir(exist_ok=True)
    # CLI process isolation is Windows-only in the first macOS beta.
    policy=build/'build_policy.json';policy.write_text(json.dumps({'cli_enabled':False}),encoding='utf-8')
    subprocess.run(['node','scripts/build-export.mjs'],cwd=ROOT,check=True)
    stage=ROOT/'src-tauri/resources/backend-build'
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--console',
             '--name','paperduet-backend','--paths',str(ROOT/'backend'),'--distpath',str(stage),
             '--workpath',str(build/'pyinstaller'),'--specpath',str(build),
             '--target-architecture',platform.machine()]
    for source,destination in [(package/'schema.sql','paperduet'),*((p,'paperduet') for p in migrations),
                               (package/'prompts','paperduet/prompts'),(policy,'paperduet'),(ROOT/'backend/resources','resources')]:
        command+=['--add-data',f'{source}:{destination}']
    for module in ['keyring.backends.macOS','uvicorn.logging','uvicorn.loops.asyncio',
                   'uvicorn.protocols.http.h11_impl','uvicorn.lifespan.on']:
        command+=['--hidden-import',module]
    identity=os.environ.get('APPLE_SIGNING_IDENTITY')
    if identity and identity!='-':command+=['--codesign-identity',identity]
    command.append(str(ROOT/'backend/entrypoint.py'))
    subprocess.run(command,cwd=ROOT,check=True)
    source=stage/'paperduet-backend'
    if options.stage_only:
        print(f'Staged backend: {source}')
        return
    target=ROOT/'src-tauri/resources/backend'
    # Resolve both paths before recursive replacement; never follow a bundle symlink.
    allowed=(ROOT/'src-tauri/resources').resolve()
    if target.is_symlink() or source.is_symlink() or target.resolve().parent!=allowed or not source.resolve().is_relative_to(allowed):
        raise RuntimeError('Unsafe backend bundle output path')
    if target.exists():shutil.rmtree(target)
    shutil.move(str(source),str(target))
    print(f'Built macOS {platform.machine()} backend: {target}')

if __name__=='__main__':main()
