import json
import os
import secrets
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from paperduet.adapter import adapt_fixture, inline
from paperduet.app import create_app
from paperduet.models import ReaderSettings, ReadingPosition
from paperduet.store import Store

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/rex-omni.blocks.json"


@pytest.mark.sample
def test_adapter_preserves_all_blocks_and_stable_ids():
    doc = adapt_fixture(FIXTURE)
    assert Counter(b.type for b in doc.blocks) == dict(sec=9, sub=21, ssub=11, p=122, li=16, note=61, tab=17, fig=18, eq=5, card=5)
    assert [b.id for b in doc.blocks] == [f"b{i:04d}" for i in range(285)]
    assert doc.model_dump() == adapt_fixture(FIXTURE).model_dump()
    assert next(b for b in doc.blocks if b.n == "4.2.3").section_path == ["4", "4.2", "4.2.3"]
    ids = {b.id for b in doc.blocks}
    for b in doc.blocks:
        if b.note:
            assert set(b.note.refs) <= ids
            assert b.note.claim == "interpretation" and "V5" in b.qa_flags
        if b.type == "fig":
            assert b.image_path is None


@pytest.mark.sample
def test_table_content_spans_highlights_and_numbers_are_preserved():
    doc = adapt_fixture(FIXTURE)
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for original, block in zip(raw, doc.blocks):
        if block.table is None:
            continue
        soup = BeautifulSoup(original["html"], "html.parser")
        cells = soup.select("th, td")
        parsed = [cell for row in block.table.header + block.table.body for cell in row]
        assert [c.get_text() for c in cells] == [BeautifulSoup(c.text_en, "html.parser").get_text() for c in parsed]
        assert [int(c.get("colspan", 1)) for c in cells] == [c.colspan for c in parsed]
        assert [int(c.get("rowspan", 1)) for c in cells] == [c.rowspan for c in parsed]
    table2 = next(b.table for b in doc.blocks if b.n == "Table 2")
    assert all(sum(c.colspan for c in row) == 13 for row in table2.header + table2.body)
    assert len(table2.highlight_rows) == 1 and len(table2.best_cells) == 3


def test_inline_content_is_sanitized_without_losing_visible_text():
    assert inline('<b onclick="bad()">text</b><script>alert(1)</script><img src=x onerror=bad()>') == '<b>text</b>'
    assert inline('<mark>key</mark> <span class="tok">&lt;12&gt;</span>') == '<b>key</b> <code>&lt;12&gt;</code>'


@pytest.mark.sample
def test_sqlite_roundtrip_constraints_and_idempotent_seed(tmp_path):
    store = Store(tmp_path / "한글 공백 경로", FIXTURE)
    settings = ReaderSettings(view="ko", theme="dark", font_size=19)
    store.save_settings(settings)
    store.position("rex-omni", ReadingPosition(block_id="b0123", offset=20))
    again = Store(tmp_path / "한글 공백 경로", FIXTURE)
    assert again.settings() == settings
    assert len(again.document("rex-omni").blocks) == 285
    assert again.document("rex-omni").reading_position.block_id == "b0123"
    with store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO threads(id,doc_id,block_id,anchor) VALUES('bad','absent','b0000','{}')")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.fixture
def api(tmp_path):
    token = secrets.token_hex(32)
    return TestClient(create_app(token, tmp_path, FIXTURE)), token


@pytest.mark.parametrize("path", ["/health", "/documents/rex-omni", "/settings/reader", "/does-not-exist"])
def test_ac14_every_actual_request_needs_auth(api, path):
    client, token = api
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    assert client.get(path, headers={"Authorization": f"Bearer {token}", "Origin": "https://malicious.example"}).status_code == 403
    assert client.get(path, headers={"Authorization": f"Bearer {token}", "Origin": "null"}).status_code == 403


@pytest.mark.sample
def test_allowed_origin_preflight_and_token_not_exposed(api):
    client, token = api
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://tauri.localhost"}
    result = client.get("/documents/rex-omni", headers=headers)
    assert result.status_code == 200
    assert len(result.json()["blocks"]) == 285 and token not in result.text
    assert result.headers["Access-Control-Allow-Origin"] == "http://tauri.localhost"
    assert client.options("/health", headers={"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}).status_code == 204
    assert client.options("/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}).status_code == 403
    assert client.options("/health").status_code == 403
    assert client.get("/health", headers=headers).json() == {"status": "ok", "milestone": "M4"}


@pytest.mark.sample
def test_api_settings_and_position_persistence(api):
    client, token = api
    headers = {"Authorization": f"Bearer {token}"}
    value = ReaderSettings(view="en", theme="dark").model_dump()
    assert client.put("/settings/reader", json=value, headers=headers).status_code == 204
    assert client.get("/settings/reader", headers=headers).json() == value
    assert client.patch("/documents/rex-omni/position", json={"block_id": "b0042", "offset": 8}, headers=headers).status_code == 204
    assert client.get("/documents/rex-omni", headers=headers).json()["reading_position"]["block_id"] == "b0042"
    assert client.patch("/documents/rex-omni/position", json={"block_id": "unknown"}, headers=headers).status_code == 404
    assert client.put("/settings/reader", json={"font_size": 1000}, headers=headers).status_code == 422
    rejected = client.put("/settings/reader", json={"api_key": "must-not-store"}, headers=headers)
    assert rejected.status_code == 422
    assert "must-not-store" not in rejected.text


def test_real_process_readiness_and_loopback_api(tmp_path):
    token = secrets.token_hex(32)
    env = {**os.environ, "PAPERDUET_SESSION_TOKEN": token, "PAPERDUET_DATA_DIR": str(tmp_path / "실제 한글 경로"), "PAPERDUET_START_GATE": "1"}
    process = subprocess.Popen([sys.executable, str(ROOT / "backend/entrypoint.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        process.stdin.write(b"start\n"); process.stdin.flush()
        ready = json.loads(process.stdout.readline())
        assert set(ready) == {"event", "port"}
        base = f"http://127.0.0.1:{ready['port']}"
        assert httpx.get(base + "/health").status_code == 401
        assert httpx.get(base + "/health", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    finally:
        process.kill(); process.wait(timeout=10)
    assert token.encode() not in process.stderr.read()
