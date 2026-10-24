"""Database versions: the migration files and SCHEMA_VERSION must stay in step."""
import re
import sqlite3
from pathlib import Path

import pytest

from paperduet.store import SCHEMA_VERSION, Store

PACKAGE = Path(__file__).resolve().parents[1] / 'paperduet'


def test_every_migration_up_to_the_schema_version_exists_and_sets_its_number():
    for step in range(2, SCHEMA_VERSION + 1):
        sql = (PACKAGE / f'migration_{step}.sql').read_text(encoding='utf-8')
        assert re.findall(r'PRAGMA\s+user_version\s*=\s*(\d+)', sql) == [str(step)], step
    # A migration beyond SCHEMA_VERSION means the constant was not raised with it.
    assert [p.name for p in PACKAGE.glob('migration_*.sql') if int(p.stem.split('_')[1]) > SCHEMA_VERSION] == []


def test_a_new_database_ends_at_the_schema_version(tmp_path):
    with Store(tmp_path, tmp_path / 'absent.json').connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_a_newer_database_is_refused(tmp_path):
    Store(tmp_path, tmp_path / 'absent.json')
    with sqlite3.connect(tmp_path / 'paperduet.sqlite3') as db:
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
    with pytest.raises(RuntimeError):
        Store(tmp_path, tmp_path / 'absent.json')
