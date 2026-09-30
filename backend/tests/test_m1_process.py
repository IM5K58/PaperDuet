"""Actual OS process interruption, beyond an in-process TestClient restart."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from paperduet.provider import WindowsVault
from test_m1 import make_pdf

ROOT=Path(__file__).resolve().parents[2]


def launch(data_dir):
    token=secrets.token_hex(32)
    env={**os.environ,'PAPERDUET_DATA_DIR':str(data_dir),'PAPERDUET_SESSION_TOKEN':token}
    child=subprocess.Popen([sys.executable,str(ROOT/'backend/entrypoint.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    line=child.stdout.readline()
    if not line:
        child.kill();raise AssertionError('No readiness from backend')
    port=json.loads(line)['port']
    return child,httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+token},timeout=30,trust_env=False)


def test_actual_process_restart_resumes_page_checkpoint(tmp_path):
    data=tmp_path/'계정 이름 공백'/'PaperDuet'
    child,client=launch(data)
    try:
        result=client.post('/documents',content=make_pdf(60),headers={'Content-Type':'application/pdf'})
        assert result.status_code==202,result.text
        doc_id=result.json()['doc_id']
        end=time.monotonic()+20
        while time.monotonic()<end:
            job=client.get(f'/documents/{doc_id}/job').json()
            count=job['checkpoint']['extracted_pages']
            if 1<=count<60:break
            time.sleep(.01)
        assert 1<=count<60,'Did not interrupt an active extraction'
        cache=data/'documents'/doc_id/'pages-v2'/'0000.json'
        cached=cache.read_bytes();modified=cache.stat().st_mtime_ns
        child.kill();child.wait(timeout=10);client.close()
        child,client=launch(data)
        end=time.monotonic()+30
        while time.monotonic()<end:
            job=client.get(f'/documents/{doc_id}/job').json()
            if job['status'] not in {'queued','running'}:break
            time.sleep(.03)
        assert job['status'] in {'awaiting_ai','ready_to_translate'},job
        assert job['checkpoint']['extracted_pages']==60
        assert cache.read_bytes()==cached and cache.stat().st_mtime_ns==modified
        assert max(b['page'] for b in client.get('/documents/'+doc_id).json()['blocks'])==60
    finally:
        client.close()
        if child.poll() is None:child.kill()
        child.wait(timeout=10)


@pytest.mark.skipif(sys.platform!='win32',reason='Windows Credential Manager only')
def test_real_windows_keyring_roundtrip_uses_unique_test_entry():
    # Never inspect or replace a user's PaperDuet entry. This test owns exactly
    # one randomly named credential, and removes it even when assertions fail.
    vault=WindowsVault('PaperDuet-Test-'+secrets.token_hex(12))
    value='test-only-'+secrets.token_hex(24)
    try:
        assert vault.get() is None
        vault.set(value)
        assert vault.get()==value
        vault.delete()
        assert vault.get() is None
    finally:
        vault.delete()
