CREATE TABLE document_reanalyses (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    baseline TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0 CHECK(applied IN (0,1)),
    applied_ids TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(applied_ids)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT
);
CREATE INDEX document_reanalyses_document ON document_reanalyses(doc_id,created_at);
INSERT INTO schema_migrations(version) VALUES(10);
PRAGMA user_version=10;
