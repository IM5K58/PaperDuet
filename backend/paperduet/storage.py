"""Quiescent copy + SQLite backup. Never delete the user's original data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
from contextlib import closing

def copy_storage(store,target):
    destination=Path(target)
    source=store.data_dir
    if not destination.is_absolute() or destination.parent==destination:raise ValueError('STORAGE_INVALID')
    lexical=destination.absolute();original=source.absolute()
    if lexical==original or lexical.is_relative_to(original) or original.is_relative_to(lexical):raise ValueError('STORAGE_INVALID')
    destination=destination.resolve();resolved=source.resolve()
    if destination==resolved or destination.is_relative_to(resolved) or resolved.is_relative_to(destination):raise ValueError('STORAGE_INVALID')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):raise ValueError('STORAGE_NOT_EMPTY')
    destination.mkdir(parents=True,exist_ok=True)
    # A unique owned staging folder makes a failed copy distinguishable from
    # user files. Only this folder may be removed during rollback.
    staging=destination/('.paperduet-copy-'+uuid.uuid4().hex);staging.mkdir()
    try:
        files=[]
        for root,dirs,names in os.walk(source,followlinks=False):
            root=Path(root)
            if root.is_symlink() or any((root/d).is_symlink() for d in dirs):raise ValueError('STORAGE_LINK_UNSUPPORTED')
            for name in names:
                path=root/name;relative=path.relative_to(source)
                if path.is_symlink():raise ValueError('STORAGE_LINK_UNSUPPORTED')
                if relative.parts[0]=='incoming' or name in {'paperduet.sqlite3','paperduet.sqlite3-wal','paperduet.sqlite3-shm'}:continue
                files.append((path,relative))
        total=sum(p.stat().st_size for p,_ in files)+store.path.stat().st_size
        if shutil.disk_usage(destination).free < total+32*1024*1024:raise ValueError('STORAGE_NO_SPACE')
        for path,relative in files:
            out=staging/relative;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,out)
            with path.open('rb') as before,out.open('rb') as after:
                if hashlib.file_digest(before,'sha256').digest()!=hashlib.file_digest(after,'sha256').digest():raise ValueError('STORAGE_VERIFY_FAILED')
        with store.connect() as original,closing(sqlite3.connect(staging/'paperduet.sqlite3')) as copied:
            original.backup(copied)
            if copied.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('STORAGE_VERIFY_FAILED')
        marker={'format':'paperduet-storage','schema':5}
        (staging/'storage.json').write_text(json.dumps(marker),encoding='utf-8')
        for child in staging.iterdir():child.rename(destination/child.name)
        staging.rmdir()
        return {'path':str(destination),'bytes':total,'files':len(files)+1,'verified':True}
    except Exception:
        if staging.exists():shutil.rmtree(staging)
        raise
