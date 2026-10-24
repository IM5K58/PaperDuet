"""Start-up recovery: legacy completion is re-derived once per library, not on every start."""
import asyncio

from paperduet.pipeline import COMPLETION_REFRESHED, Pipeline
from m2_sample import seed


def test_legacy_completion_is_rederived_once_per_library(tmp_path, monkeypatch):
    store, doc_id = seed(tmp_path)
    store.update_job(doc_id, 'Render', 'review', 1)  # left behind by the removed automatic review
    calls = []
    original = Pipeline.refresh_completion
    def counted(self, doc, finished=False):
        calls.append(doc)
        return original(self, doc, finished)
    monkeypatch.setattr(Pipeline, 'refresh_completion', counted)

    asyncio.run(Pipeline(store, None).recover())
    assert calls == [doc_id]
    assert store.job(doc_id)['status'] == 'complete'  # every target block has its translation
    with store.connect() as db:
        assert db.execute('SELECT payload FROM app_settings WHERE key=?', (COMPLETION_REFRESHED,)).fetchone()[0] == 'true'

    asyncio.run(Pipeline(store, None).recover())
    assert calls == [doc_id]  # later starts skip the pass; edits refresh their own paper
