ALTER TABLE documents ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN authors TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX document_hash ON documents(file_hash) WHERE file_hash IS NOT NULL;
CREATE TABLE source_blocks (
    doc_id TEXT NOT NULL, block_id TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    provenance TEXT NOT NULL CHECK(json_valid(provenance)),
    PRIMARY KEY(doc_id,block_id),
    FOREIGN KEY(doc_id,block_id) REFERENCES blocks(doc_id,id) ON DELETE CASCADE
);
CREATE TABLE pipeline_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stage TEXT NOT NULL, model TEXT NOT NULL, tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_migrations(version) VALUES(2);
PRAGMA user_version=2;
