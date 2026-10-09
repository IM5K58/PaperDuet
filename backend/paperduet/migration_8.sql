-- Paper summary and flow: one per paper, made on request from the source text.
-- payload holds the structured summary, the section flow and the key figures;
-- edited marks a summary the reader has changed (regenerating asks first).
CREATE TABLE paper_summaries (
    doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('running','ready','failed')),
    progress REAL NOT NULL DEFAULT 0,
    payload TEXT CHECK(payload IS NULL OR json_valid(payload)),
    model TEXT NOT NULL DEFAULT '',
    error TEXT,
    generation INTEGER NOT NULL DEFAULT 0,
    edited INTEGER NOT NULL DEFAULT 0 CHECK(edited IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_migrations(version) VALUES(8);
PRAGMA user_version=8;
