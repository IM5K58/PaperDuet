"""Native platform boundaries; never read a user's credential entry."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import types
import secrets

from fastapi.testclient import TestClient
import pytest

from paperduet.app import create_app
from paperduet.provider import ProviderError, SystemVault
import paperduet.cli_provider as cli

@pytest.mark.parametrize(('platform','module','class_name'),[
    ('win32','keyring.backends.Windows','WinVaultKeyring'),
    ('darwin','keyring.backends.macOS','Keyring'),
])
def test_vault_chooses_native_backend_without_discovery(platform,module,class_name,monkeypatch):
    entries={}
    class Native:
        keychain='untrusted-override'
        def get_password(self,service,provider):return entries.get((service,provider))
        def set_password(self,service,provider,value):entries[service,provider]=value
        def delete_password(self,service,provider):entries.pop((service,provider),None)
    fake=types.ModuleType(module);setattr(fake,class_name,Native)
    monkeypatch.setitem(sys.modules,module,fake)
    monkeypatch.setattr(sys,'platform',platform)
    vault=SystemVault(service='unique-test-service',provider='google')
    assert vault.get() is None
    vault.set('test-only-value');assert vault.get()=='test-only-value'
    if platform=='darwin':assert vault._backend().keychain is None
    vault.delete();assert vault.get() is None

def test_vault_never_falls_back_on_unsupported_os(monkeypatch):
    monkeypatch.setattr(sys,'platform','linux')
    with pytest.raises(ProviderError,match='KEYRING_UNAVAILABLE'):SystemVault().get()

def test_macos_cli_stays_disabled_even_with_enabled_build_policy(tmp_path,monkeypatch):
    (tmp_path/'build_policy.json').write_text('{"cli_enabled":true}')
    monkeypatch.setattr(cli,'__file__',str(tmp_path/'cli_provider.py'))
    monkeypatch.setattr(sys,'platform','darwin')
    assert not cli.cli_enabled()
    with pytest.raises(ProviderError,match='CLI_DISABLED'):cli.resolve_cli('anthropic')

def test_macos_origin_is_the_only_allowed_browser_origin(tmp_path):
    token='p'*64;origin='tauri://localhost'
    with TestClient(create_app(token,tmp_path,tmp_path/'absent.json',origin)) as client:
        headers={'Authorization':'Bearer '+token,'Origin':origin}
        response=client.get('/health',headers=headers)
        assert response.status_code==200 and response.headers['Access-Control-Allow-Origin']==origin
        assert client.get('/health',headers={**headers,'Origin':'http://tauri.localhost'}).status_code==403
        assert client.get('/health',headers={'Origin':origin}).status_code==401
        assert client.options('/health',headers={'Origin':origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'}).status_code==204

def entrypoint():
    path=Path(__file__).resolve().parents[1]/'entrypoint.py'
    spec=importlib.util.spec_from_file_location('sidecar_boot_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_macos_data_directory_and_parent_pipe_validation(monkeypatch,tmp_path):
    boot=entrypoint();monkeypatch.setattr(sys,'platform','darwin')
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    assert boot.default_data_dir()==tmp_path/'Library/Application Support/PaperDuet'
    monkeypatch.setattr(boot.os,'getpgrp',lambda:12345,raising=False)
    with pytest.raises(RuntimeError,match='Invalid backend process group'):boot.arm_parent_pipe(io.StringIO(),0)
    with pytest.raises(RuntimeError,match='Invalid backend process group'):boot.arm_parent_pipe(io.StringIO(),12346)

def test_parent_eof_targets_only_the_validated_backend_group(monkeypatch):
    import signal
    boot=entrypoint();called=[]
    class InlineThread:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(boot.os,'getpgrp',lambda:12345,raising=False)
    monkeypatch.setattr(boot.os,'killpg',lambda *args:called.append(args),raising=False)
    monkeypatch.setattr(signal,'SIGKILL',9,raising=False)
    monkeypatch.setattr(boot.threading,'Thread',InlineThread)
    boot.arm_parent_pipe(io.StringIO(),12345)
    assert called==[(12345,9)]

@pytest.mark.skipif(sys.platform!='darwin' or os.environ.get('PAPERDUET_TEST_KEYCHAIN')!='1',reason='Opt-in macOS keychain permission test')
def test_native_macos_keychain_roundtrip():
    # This test owns one random entry; it never touches the app's real API keys.
    vault=SystemVault(service='PaperDuet-Test-'+secrets.token_hex(12))
    value='test-only-'+secrets.token_hex(24)
    try:
        assert vault.get() is None
        vault.set(value);assert vault.get()==value
        vault.delete();assert vault.get() is None
    finally:vault.delete()
