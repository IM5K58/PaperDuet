"""Exercise a packaged backend with a fresh library; no keys or AI requests."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import httpx
import pymupdf

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('executable',type=Path)
    options=parser.parse_args();executable=options.executable.resolve()
    token=secrets.token_hex(32);origin='tauri://localhost' if sys.platform=='darwin' else 'http://tauri.localhost'
    with tempfile.TemporaryDirectory(prefix='PaperDuet packaged 한글 ') as directory:
        env={**os.environ,'PAPERDUET_SESSION_TOKEN':token,'PAPERDUET_DATA_DIR':directory,'PAPERDUET_ORIGIN':origin}
        unix=sys.platform!='win32'
        if unix:env['PAPERDUET_START_GATE']='1'
        else:env.pop('PAPERDUET_START_GATE',None)
        child=subprocess.Popen([str(executable)],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                               start_new_session=unix,creationflags=subprocess.CREATE_NO_WINDOW if not unix else 0)
        try:
            if unix:child.stdin.write(f'start {child.pid}\n'.encode());child.stdin.flush()
            ready=json.loads(child.stdout.readline());port=ready['port']
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+token,'Origin':origin},trust_env=False,timeout=30) as client:
                assert client.get('/health').status_code==200
                assert client.get('/health',headers={'Authorization':''}).status_code==401
                assert client.get('/health',headers={'Origin':'https://example.invalid'}).status_code==403
                with pymupdf.open() as pdf:
                    page=pdf.new_page();page.insert_text((40,60),'PaperDuet packaged PDF smoke test.');raw=pdf.tobytes()
                response=client.post('/documents',content=raw,headers={'Content-Type':'application/pdf'})
                assert response.status_code==202,response.status_code
                doc_id=response.json()['doc_id'];deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    job=client.get(f'/documents/{doc_id}/job').json()
                    if job['status'] not in {'queued','running'}:break
                    time.sleep(.1)
                assert job['status'] in {'awaiting_ai','ready_to_translate'},job['status']
                assert client.get(f'/documents/{doc_id}').json()['blocks']
                assert client.get(f'/documents/{doc_id}/pages/1/image').json()['data_url'].startswith('data:image/png;base64,')
                assert client.get(f'/documents/{doc_id}/thumbnail').headers['content-type']=='image/png'
                assert 'block_count' not in client.get('/documents?stats=false').json()[0]
                assert client.get(f'/documents/{doc_id}/export?format=html').status_code==200
                if unix:
                    child.stdin.close();child.wait(timeout=5)
                    try:client.get('/health')
                    except httpx.TransportError:pass
                    else:raise AssertionError('Backend survived parent EOF')
            print('Packaged backend: auth, origin, PDF extraction, images, library and offline export passed.')
        finally:
            if child.poll() is None:child.kill()
            child.wait(timeout=10)
            child.stdin.close();child.stdout.close()

if __name__=='__main__':main()
